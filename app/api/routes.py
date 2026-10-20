import asyncio
import json
import secrets
import time
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import tokens
from ..backend import BackendError, negotiation_quote, report_event
from ..config import Settings, get_settings
from ..engine.rules import Kind, State, Status, decide
from ..llm.parse import parse_message
from ..llm.reply import write_reply
from ..llm.templates import greeting, welcome_back
from ..store import Session

router = APIRouter(prefix='/v1')


class StartBody(BaseModel):
    variant_id: str = Field(min_length=1, max_length=64)


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=600)


def _dec(value) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, '') else None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    return forwarded.split(',')[0].strip() or (request.client.host if request.client else 'unknown')


def _price_pass(settings: Settings, session: Session) -> str | None:
    """A fresh signed pass for the agreed deal (or bundle), so an old one expiring never loses the deal."""
    st = session.state
    common = dict(variant_id=session.variant_id, subject=session.subject, session_id=session.id,
                  ttl=settings.price_pass_ttl_seconds, perks=st.perks)
    if st.status == Status.ACCEPTED and st.accepted_price is not None:
        return tokens.issue(settings.price_pass_secret, price=st.accepted_price, **common)
    if st.status == Status.BUNDLE_OFFERED and st.bundle_percent:
        return tokens.issue(settings.price_pass_secret, bundle_percent=st.bundle_percent, **common)
    return None


def _public(session: Session) -> dict:
    st = session.state
    return {
        'session_id': session.id,
        'status': st.status.value,
        'tier': session.tier,
        'list_price': str(st.list_price),
        'current_price': str(st.position) if st.position < st.list_price else None,
        'accepted_price': str(st.accepted_price) if st.accepted_price is not None else None,
        'bundle_percent': str(st.bundle_percent) if st.status == Status.BUNDLE_OFFERED else None,
        'perks': st.perks,
        'product': {k: session.product.get(k) for k in ('title', 'image', 'option', 'slug')},
        'suggestions': session.product.get('similar', []) if st.status == Status.BUNDLE_OFFERED else [],
        'messages': session.messages,
    }


async def _owned(app_state, session_id: str, token: str | None) -> Session:
    session = await app_state.store.get(session_id)
    if session is None:
        raise HTTPException(404, 'This conversation has expired. Start a new one from the product page.')
    if not token or not secrets.compare_digest(token, session.token):
        raise HTTPException(403, 'Not your conversation.')
    return session


@router.post('/sessions')
async def start_session(body: StartBody, request: Request, settings: Settings = Depends(get_settings),
                        authorization: str | None = Header(None), x_session_id: str | None = Header(None)):
    app_state = request.app.state
    if not app_state.limiter.allow(_client_key(request)):
        raise HTTPException(429, 'Too many requests — please wait a moment.')
    if not authorization and not x_session_id:
        raise HTTPException(400, 'Missing shopper identity.')
    try:
        q = await negotiation_quote(settings, body.variant_id, user_token=authorization, guest_id=x_session_id)
    except BackendError as exc:
        raise HTTPException(exc.status if exc.status in (400, 403, 404) else 502, exc.message)

    list_price = Decimal(q['list_price'])
    floor = min(Decimal(q['floor_price']), list_price)
    levers = q.get('levers') or {}
    state = State(
        list_price=list_price, floor_price=floor,
        opening_price=max(min(Decimal(q.get('opening_price') or list_price), list_price), floor),
        target_price=max(Decimal(q.get('target_price') or floor), floor),
        member_price=_dec(levers.get('member_price')),
        bundle_percent=_dec(levers.get('bundle_percent')),
        can_bundle=bool(q.get('similar')) and bool(levers.get('bundle_percent')),
        can_gift_wrap=bool(levers.get('free_gift_wrap')),
        can_rescue=bool(levers.get('rescue_offer', True)),
        low_stock=levers.get('low_stock'),
        effort_rounds=int(q.get('effort_rounds') or 3),
    )
    # Reopening the chat continues the last 24h of negotiation on this piece (no fresh start to farm offers)
    remembered = await app_state.store.recall(str(q['subject']), body.variant_id)
    resumed = remembered is not None and remembered.list_price == list_price and remembered.floor_price >= floor
    if resumed and remembered.status == Status.ACCEPTED and remembered.accepted_at \
            and time.time() - remembered.accepted_at > settings.deal_hold_hours * 3600:
        resumed = False  # the deal lapsed: start a fresh conversation
    if resumed:
        state = remembered
        state.low_stock = levers.get('low_stock')
        if state.status == Status.CLOSED:
            state.status = Status.OPEN

    session = Session(
        id=secrets.token_urlsafe(16), token=secrets.token_urlsafe(24), subject=str(q['subject']),
        variant_id=body.variant_id, product={**q['product'], 'list_price': str(list_price), 'similar': q.get('similar', [])},
        state=state, tier=q.get('tier', 'GUEST'), score=int(q.get('score') or 0),
    )
    title = q['product']['title']
    if resumed and state.position < list_price:
        text = welcome_back(title, state.accepted_price or state.position, state.status == Status.ACCEPTED)
    else:
        text = greeting(session.tier, title, list_price)
    session.messages.append({'role': 'bot', 'text': text, 'ts': time.time()})
    await app_state.store.save(session)
    await app_state.store.remember(session)
    return {**_public(session), 'session_token': session.token, 'price_pass': _price_pass(settings, session)}


@router.get('/sessions/{session_id}')
async def get_session(session_id: str, request: Request, x_negotiation_token: str | None = Header(None)):
    return _public(await _owned(request.app.state, session_id, x_negotiation_token))


@router.post('/sessions/{session_id}/messages')
async def send_message(session_id: str, body: MessageBody, request: Request, settings: Settings = Depends(get_settings),
                       x_negotiation_token: str | None = Header(None), stream: bool = True):
    app_state = request.app.state
    if not app_state.limiter.allow(_client_key(request)):
        raise HTTPException(429, 'Too many requests — please wait a moment.')
    session = await _owned(app_state, session_id, x_negotiation_token)
    st = session.state

    if session.turns >= settings.max_turns and st.status == Status.OPEN:
        st.status = Status.CLOSED
    session.turns += 1
    text = body.text.strip()
    session.messages.append({'role': 'user', 'text': text, 'ts': time.time()})

    parsed = await parse_message(app_state.llm, settings.parse_model, text,
                                 last_counter=st.position if st.position < st.list_price else None,
                                 list_price=st.list_price, reasoning_effort=settings.reasoning_effort)
    decision = decide(st, parsed.intent, parsed.amount)

    reply, _ = await write_reply(
        app_state.llm, settings.reply_model, decision=decision, product=session.product, history=session.messages[:-1],
        shopper_text=text, language=parsed.language, list_price=st.list_price, shopper_offer=parsed.amount,
        reasoning_effort=settings.reasoning_effort, low_stock=st.low_stock,
    )
    session.messages.append({'role': 'bot', 'text': reply, 'ts': time.time()})

    if decision.kind == Kind.ACCEPT and st.accepted_at is None:
        st.accepted_at = time.time()
    price_pass = _price_pass(settings, session) if decision.kind in (Kind.ACCEPT, Kind.BUNDLE) else None
    await app_state.store.save(session)
    await app_state.store.remember(session)

    # Outcomes report (fire and forget)
    asyncio.create_task(report_event(settings, {
        'session_id': session.id, 'subject': session.subject, 'tier': session.tier, 'score': session.score,
        'variant_id': session.variant_id,
        'product_title': session.product.get('title', ''), 'category': session.product.get('category', ''),
        'round': st.rounds, 'shopper_text': text, 'shopper_offer': str(parsed.amount) if parsed.amount else None,
        'decision': decision.kind.value, 'price': str(decision.price) if decision.price is not None else None,
        'list_price': str(st.list_price), 'reply': reply,
    }))

    meta = {
        'type': 'meta',
        'decision': decision.kind.value,
        'tag': decision.tag.value if decision.tag else None,
        'price': str(decision.price) if decision.price is not None else None,
        'member_price': str(decision.member_price) if decision.member_price is not None else None,
        'bundle_percent': str(decision.bundle_percent) if decision.bundle_percent is not None else None,
        'perks': decision.perks,
        'price_pass': price_pass,
        'session': _public(session),
    }
    if not stream:
        return {**meta, 'reply': reply}

    async def events():
        yield f"data: {json.dumps(meta)}\n\n"
        words = reply.split(' ')
        for i in range(0, len(words), 3):  # the reply is already checked; send it in small pieces for a typing feel
            yield f"data: {json.dumps({'type': 'chunk', 'content': ' '.join(words[i:i + 3]) + (' ' if i + 3 < len(words) else '')})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})
