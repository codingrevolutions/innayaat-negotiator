import time
from decimal import Decimal as D

import pytest

from app import tokens
from app.llm.guardrails import problems
from app.llm.parse import fallback, regex_amount


def test_price_pass_round_trip_and_tamper():
    t = tokens.issue('s3cret', variant_id='v1', subject='u1', session_id='s1', ttl=60, price=D('2700'))
    payload = tokens.verify('s3cret', t)
    assert payload['p'] == '2700.00' and payload['v'] == 'v1' and payload['kind'] == 'PRICE'
    body, sig = t.split('.')
    with pytest.raises(ValueError):
        tokens.verify('s3cret', body[:-2] + 'xx.' + sig)
    with pytest.raises(ValueError):
        tokens.verify('other', t)


def test_price_pass_expiry(monkeypatch):
    t = tokens.issue('s', variant_id='v', subject='u', session_id='x', ttl=1, price=D('1'))
    monkeypatch.setattr(time, 'time', lambda: 10**12)
    with pytest.raises(ValueError):
        tokens.verify('s', t)


def test_guardrails():
    ok = {D('32000'), D('29500')}
    assert problems('I can do ₹29,500 for you.', ok) == []
    assert problems('It holds 350 ml, made in 2026.', ok) == []
    assert problems('Fine, ₹20,000 then', ok)
    assert problems('Our floor is high', ok)
    # Hindi "kar" after a price is not "thousand"
    assert problems('Main ₹29,500 kar sakta hoon', ok) == []
    assert problems('Rs 32k pe final', ok) == []
    # Only an agreed deal may send the shopper to the bag
    assert problems('I can do ₹29,500. Tap Add to bag and I will hold this price.', ok)
    assert problems('Deal at ₹29,500 — tap Add to bag.', ok, agreed=True) == []
    # "Best"/"final" only on a final move
    assert problems('My best price is ₹29,500 — deal?', ok)
    assert problems('My best price is ₹29,500 — deal?', ok, final=True) == []
    assert problems('This is the best piece in our range, and I can do ₹29,500.', ok) == []


def test_regex_parsing():
    assert regex_amount('can you do 25k?') == D('25000')
    assert regex_amount('₹27,500 final') == D('27500')
    assert regex_amount('1.2 lakh') == D('120000')
    assert fallback('ok deal').intent == 'ACCEPT_COUNTER'
    assert fallback('leave it, bye').intent == 'WALK_AWAY'
    assert fallback('is it pure silk?').intent == 'QUESTION'


def test_fallback_parser_reads_pleading_as_asking_for_price():
    from app.llm.parse import fallback
    assert fallback('no just some more').intent == 'ASK_PRICE'
    assert fallback('thoda aur kam karo').intent == 'ASK_PRICE'
    p = fallback('last 7000 not more than that')
    assert p.intent == 'OFFER' and p.amount == 7000
