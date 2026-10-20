"""Hand-written replies: greetings, and whenever the model is unavailable or breaks a rule."""
from ..engine.money import fmt_inr
from ..engine.rules import Kind, Tag

GREETINGS = {
    'VIP': "Welcome back — it's always a pleasure. {title} is {price}, and as one of our most valued patrons I have a little more room for you. What would you like to offer?",
    'LOYAL': "Lovely to see you again. {title} is {price}. Tell me what feels right and let's work something out.",
    'NEW_USER': "Welcome to Innayaat Kumar! {title} is {price}. Make me an offer — I'd love to help you start your collection.",
    'GUEST': "Welcome! {title} is {price}. Make me an offer, or ask for my best price, and we'll find something that feels right.",
}


def greeting(tier: str, title: str, list_price) -> str:
    return GREETINGS.get(tier, GREETINGS['GUEST']).format(title=title, price=fmt_inr(list_price))


def welcome_back(title: str, price, accepted: bool) -> str:
    if accepted:
        return f"Welcome back! Our deal on {title} at {fmt_inr(price)} still stands — tap “Add to bag” whenever you're ready."
    return f"Welcome back! My offer on {title} is still {fmt_inr(price)}. Shall we pick up where we left off?"


def _gift(perks) -> str:
    return " — and I'll gift wrap it for you, on the house" if 'gift_wrap' in (perks or []) else ''


def fallback_reply(kind: Kind, *, price=None, list_price=None, tag=None, perks=None, member_price=None,
                   bundle_percent=None) -> str:
    p = fmt_inr(price) if price is not None else ''
    if kind == Kind.ACCEPT:
        return f"Wonderful — {p} it is{_gift(perks)}. Tap “Add to bag” and your price is saved for 24 hours."
    if kind == Kind.COUNTER and tag == Tag.OPENING:
        return f"For you, I can do a special {p}. Shall I hold it?"
    if kind == Kind.COUNTER and tag == Tag.MEET:
        return f"We're so close — let's meet in the middle at {p}{_gift(perks)}. Deal?"
    if kind == Kind.COUNTER and tag == Tag.HOLD:
        return f"I can do {p} for you right now — tell me what you have in mind and let's see."
    if kind == Kind.COUNTER:
        return f"I appreciate the offer. This is handcrafted work, but I can come down to {p}{_gift(perks)}. How does that sound?"
    if kind == Kind.FINAL:
        return f"I've stretched as far as I can: {p} is my very best for this piece{_gift(perks)}. Shall we close at that?"
    if kind == Kind.MEMBER_OFFER:
        return f"{p} is the lowest I can do for a guest — but sign in and I can offer you {fmt_inr(member_price)}."
    if kind == Kind.BUNDLE:
        return f"I can't go lower on this piece alone, but add one more piece from below and I'll take {bundle_percent:g}% off your whole order."
    if kind == Kind.RESCUE:
        return f"Before you go — I'd hate for you to leave without it. {p}{_gift(perks)}, just for you. Shall I hold it?"
    if kind == Kind.CLOSED:
        return "Thank you for stopping by — it's still yours at the listed price whenever you're ready."
    if price:
        return f"Happy to help! My current offer stands at {p}. Would you like to go ahead?"
    return f"Happy to help! It's {fmt_inr(list_price)} — ask for my best price or tell me what you have in mind."
