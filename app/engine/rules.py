"""
The negotiation strategy. Pure functions, no I/O, no AI: given the session state and
what the shopper just did, decide the next move. The language model only *phrases*
these moves, so no prompt can talk the price below the floor.

It sells like a good shop owner, spending the discount budget (list price → floor,
from the Django backend) gradually:

- Opening:   asked for "your best price", it makes the first move (opening price).
- Effort:    until the shopper has made `effort_rounds` offers (default 3), the
             price never goes below the half-way target — the deeper discount a good
             customer score allows has to be earned in the chat.
- Bargaining: every firm offer gets a counter. Each concession is a shrinking share
             of what's left (35% → 25% → 15% → 10%), plus half of how far the shopper
             just moved (reciprocity). Low-balls get a smaller step, never a flat no.
- Closing:   when the two prices are close — or a realistic offer is still on the table
             after the effort rounds — it proposes to meet in the middle; an offer that
             matches or beats its counter is accepted.
- Sweetener: once most of the budget is spent it adds free gift wrap (if allowed).
- Stuck below the floor: glide down in big steps while far from the floor, then
             final price (floor) → "sign in for a better price" (guests) → bundle offer.
             Once the final price is out, asking again repeats it honestly (and the
             sign-in price, if one was offered) — it never pretends there's more room.
- Walk-away: one rescue offer, if any budget is left.

Prices only ever move towards the shopper and never below the floor.
"""
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from enum import Enum

from .money import D, nice_price, price_step

CONCESSION_SHARES = (Decimal('0.35'), Decimal('0.25'), Decimal('0.15'), Decimal('0.10'))
RECIPROCITY = Decimal('0.5')          # we move half as far as the shopper just moved
LOWBALL_FACTOR = Decimal('0.5')       # smaller steps for offers far below the floor
DEEP_LOWBALL = Decimal('0.9')         # "far below" = under 90% of the floor
MAX_STEP_SHARE = Decimal('0.6')       # never give more than 60% of what's left in one step
INSTANT_ACCEPT = Decimal('0.97')      # tiny asks are accepted straight away
CLOSE_GAP_SHARE = Decimal('0.03')     # "close" = within 3% of the list price…
CLOSE_GAP_SHARE_MIN = Decimal('2')     # …or two rounding steps (₹20 / ₹100 / ₹200)
SWEETENER_AT = Decimal('0.7')         # free gift wrap once 70% of the budget is spent
NUDGE_SHARE = Decimal('0.1')          # "a little more?" without a number: 10% of what's left…
MAX_NUDGES = 2                        # …at most twice per piece (offers, not pleading, unlock the rest)
RESCUE_SHARE = Decimal('0.6')         # walk-away rescue gives 60% of what's left
GLIDE_GAP = Decimal('0.25')           # stuck low-balls: still far from the floor = over 25% of the budget away…
GLIDE_STEP = Decimal('0.6')           # …so step 60% of the way there before naming the final price


class Status(str, Enum):
    OPEN = 'OPEN'
    ACCEPTED = 'ACCEPTED'
    BUNDLE_OFFERED = 'BUNDLE_OFFERED'
    CLOSED = 'CLOSED'


class Kind(str, Enum):
    CHAT = 'CHAT'                  # no price talk: answer, keep it warm
    COUNTER = 'COUNTER'            # a new or repeated price from us (see tag)
    ACCEPT = 'ACCEPT'
    FINAL = 'FINAL'                # our last word: the floor
    MEMBER_OFFER = 'MEMBER_OFFER'  # guest: sign in and we can do better
    BUNDLE = 'BUNDLE'
    RESCUE = 'RESCUE'              # one last offer when they walk away
    CLOSED = 'CLOSED'


class Tag(str, Enum):
    OPENING = 'OPENING'   # our first move
    STEP = 'STEP'         # a concession
    MEET = 'MEET'         # split the difference to close
    HOLD = 'HOLD'         # repeating our current price


@dataclass
class State:
    list_price: Decimal
    floor_price: Decimal
    opening_price: Decimal
    target_price: Decimal
    position: Decimal | None = None          # our current asking price (starts at list)
    member_price: Decimal | None = None      # what a guest gets by signing in
    bundle_percent: Decimal | None = None    # lever; set when offered
    can_bundle: bool = False
    can_gift_wrap: bool = False
    can_rescue: bool = True
    low_stock: int | None = None
    effort_rounds: int = 3
    status: Status = Status.OPEN
    rounds: int = 0                          # firm offers so far
    stuck_rounds: int = 0                    # firm offers below the floor
    last_offer: Decimal | None = None
    accepted_price: Decimal | None = None
    perks: list = field(default_factory=list)
    rescue_used: bool = False
    final_made: bool = False
    member_offer_made: bool = False
    nudges: int = 0
    accepted_at: float | None = None         # Unix time the deal was agreed (deals are honoured for a day)
    offers: list = field(default_factory=list)

    def __post_init__(self):
        if self.position is None:
            self.position = self.list_price

    # JSON round-trip for the session store
    def to_dict(self) -> dict:
        out = {}
        for k, v in asdict(self).items():
            out[k] = str(v) if isinstance(v, Decimal) else v.value if isinstance(v, Enum) else v
        return out

    @classmethod
    def from_dict(cls, data: dict) -> 'State':
        kwargs = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            v = data[f.name]
            if f.name == 'status':
                v = Status(v)
            elif v is not None and 'Decimal' in str(f.type):
                v = Decimal(v)
            kwargs[f.name] = v
        return cls(**kwargs)


@dataclass
class Decision:
    kind: Kind
    price: Decimal | None = None
    tag: Tag | None = None
    perks: list = field(default_factory=list)         # e.g. ['gift_wrap'] newly added this turn
    member_price: Decimal | None = None
    bundle_percent: Decimal | None = None
    shopper_moved: Decimal | None = None              # how much they raised their offer (for tone)


def _limit(state: State) -> Decimal:
    """Lowest price allowed right now: the half-way target until enough effort, then the floor."""
    return state.target_price if state.rounds < state.effort_rounds else state.floor_price


def _close_gap(state: State) -> Decimal:
    return max(state.list_price * CLOSE_GAP_SHARE, price_step(state.list_price) * CLOSE_GAP_SHARE_MIN)


def _sweeten(state: State, new_position: Decimal) -> list:
    budget = state.list_price - state.floor_price
    if (state.can_gift_wrap and 'gift_wrap' not in state.perks and budget > 0
            and state.list_price - new_position >= budget * SWEETENER_AT):
        state.perks.append('gift_wrap')
        return ['gift_wrap']
    return []


def _accept(state: State, price: Decimal) -> Decision:
    price = max(min(D(price), state.list_price), state.floor_price)
    state.status = Status.ACCEPTED
    state.accepted_price = price
    state.position = price
    return Decision(Kind.ACCEPT, price, perks=list(state.perks))


def _opening(state: State) -> Decision:
    if state.position >= state.list_price and state.opening_price < state.list_price:
        state.position = state.opening_price
        return Decision(Kind.COUNTER, state.position, Tag.OPENING)
    return Decision(Kind.COUNTER, state.position, Tag.HOLD)


def _stuck_move(state: State) -> Decision:
    """The shopper keeps offering below our floor: glide down, final price, then member price, then bundle."""
    state.stuck_rounds += 1
    gap = state.position - state.floor_price
    if not state.final_made and gap > (state.list_price - state.floor_price) * GLIDE_GAP:
        # Don't drop the whole remaining budget in one go — a big but believable step first
        state.position = nice_price(state.position - gap * GLIDE_STEP, state.floor_price)
        perks = _sweeten(state, state.position)
        return Decision(Kind.COUNTER, state.position, Tag.STEP, perks=perks)
    if not state.final_made:
        state.final_made = True
        state.position = nice_price(state.floor_price, state.floor_price)
        perks = _sweeten(state, state.position)  # at the floor the whole budget is spent: add the sweetener
        return Decision(Kind.FINAL, state.position, perks=perks)
    if state.member_price is not None and not state.member_offer_made:
        state.member_offer_made = True
        return Decision(Kind.MEMBER_OFFER, state.position, member_price=state.member_price)
    if state.can_bundle and state.status != Status.BUNDLE_OFFERED and state.bundle_percent:
        state.status = Status.BUNDLE_OFFERED
        return Decision(Kind.BUNDLE, state.position, bundle_percent=state.bundle_percent)
    return Decision(Kind.FINAL, state.position)


def decide(state: State, intent: str, amount: Decimal | None = None) -> Decision:
    """
    Advance the state for one shopper message.
    intent: OFFER (with amount), ACCEPT_COUNTER, ASK_PRICE, WALK_AWAY, QUESTION, SMALLTALK
    """
    decision = _decide(state, intent, amount)
    # Once the sign-in price has been offered it stays on the table (and may be repeated)
    if state.member_offer_made and decision.member_price is None and decision.kind not in (Kind.ACCEPT, Kind.CLOSED):
        decision.member_price = state.member_price
    return decision


def _nudge(state: State) -> Decision:
    """They plead for "a little more" after we've countered: a small goodwill step, a couple of times at most."""
    limit = _limit(state)
    left = state.position - limit
    if state.nudges >= MAX_NUDGES or left <= 0:
        return Decision(Kind.COUNTER, state.position, Tag.HOLD)
    state.nudges += 1
    step = min(left, max(left * NUDGE_SHARE, price_step(state.list_price)))
    state.position = nice_price(state.position - step, limit)
    return Decision(Kind.COUNTER, state.position, Tag.STEP, perks=_sweeten(state, state.position))


def _repeat_final(state: State) -> Decision:
    """Asked again after our final price: say so honestly, and offer guests the sign-in price."""
    if state.member_price is not None:
        # Still pushing after our final price: the moment to mention the sign-in price (guests)
        state.member_offer_made = True
        return Decision(Kind.MEMBER_OFFER, state.position, member_price=state.member_price)
    return Decision(Kind.FINAL, state.position)


def _decide(state: State, intent: str, amount: Decimal | None = None) -> Decision:
    if state.status == Status.ACCEPTED:
        return Decision(Kind.ACCEPT, state.accepted_price, perks=list(state.perks))
    if state.status == Status.CLOSED:
        return Decision(Kind.CLOSED)

    if intent == 'WALK_AWAY':
        limit = _limit(state)
        left = state.position - limit
        if state.can_rescue and not state.rescue_used and left > 0:
            state.rescue_used = True
            state.position = nice_price(state.position - left * RESCUE_SHARE, limit)
            perks = _sweeten(state, state.position)
            return Decision(Kind.RESCUE, state.position, perks=perks)
        state.status = Status.CLOSED
        return Decision(Kind.CLOSED)

    if intent == 'ACCEPT_COUNTER':
        if state.position < state.list_price or state.perks:
            return _accept(state, state.position)
        return _opening(state)  # "ok" before any price talk: make our first move

    if intent == 'ASK_PRICE' or (intent == 'OFFER' and amount is None):
        if state.final_made:
            return _repeat_final(state)
        if state.position >= state.list_price:
            return _opening(state)
        return _nudge(state)

    if intent != 'OFFER' or amount is None or D(amount) <= 0:
        if state.final_made:
            return _repeat_final(state)
        return Decision(Kind.CHAT, state.position if state.position < state.list_price else None)

    # A firm offer
    offer = min(D(amount), state.list_price)
    previous = state.last_offer
    state.rounds += 1
    state.last_offer = offer
    state.offers.append(str(offer))
    moved = offer - previous if previous is not None and offer > previous else None

    if offer >= state.position:
        return _accept(state, state.position)
    if offer >= state.list_price * INSTANT_ACCEPT:
        return _accept(state, offer)

    limit = _limit(state)
    if offer < state.floor_price:
        # Far below: a couple of anchoring counters first, then the stuck-move ladder
        if state.rounds >= state.effort_rounds or state.position <= nice_price(state.floor_price, state.floor_price):
            return _stuck_move(state)

    # Close enough — or they've kept bargaining past the effort rounds with a realistic offer:
    # meet in the middle (never below what their effort has unlocked so far)
    persistent = state.rounds > state.effort_rounds and offer >= state.floor_price
    if offer >= limit and (state.position - offer <= _close_gap(state) or persistent):
        meet = nice_price((offer + state.position) / 2, limit)
        if meet <= offer:
            return _accept(state, offer)
        state.position = min(meet, state.position)
        perks = _sweeten(state, state.position)
        return Decision(Kind.COUNTER, state.position, Tag.MEET, perks=perks, shopper_moved=moved)

    # Concede a shrinking share of what's left, plus half the shopper's own move
    left = max(state.position - limit, Decimal('0'))
    share = CONCESSION_SHARES[min(state.rounds - 1, len(CONCESSION_SHARES) - 1)]
    step = left * share
    deep_lowball = offer < state.floor_price * DEEP_LOWBALL
    if moved and not deep_lowball:
        step += moved * RECIPROCITY  # reward real movement, not a jump from absurd to merely very low
    if deep_lowball:
        step *= LOWBALL_FACTOR
    step = min(step, left * MAX_STEP_SHARE)
    new_position = nice_price(state.position - step, limit)
    if state.position >= state.list_price:
        # Making an offer must never get a worse first move than asking "your best price?"
        new_position = min(new_position, max(state.opening_price, limit))
    if state.position - new_position < price_step(state.list_price) and state.position > limit:
        # Always move a visible step while there's room (never "no change" on a counter)
        new_position = max(limit, state.position - max(price_step(state.list_price), left * Decimal('0.1')))
        new_position = nice_price(new_position, limit)

    if offer >= limit and new_position <= offer:
        return _accept(state, offer)

    state.position = min(new_position, state.position)
    perks = _sweeten(state, state.position)
    return Decision(Kind.COUNTER, state.position, Tag.STEP, perks=perks, shopper_moved=moved)
