from decimal import Decimal as D

from app.engine.money import fmt_inr, nice_price
from app.engine.rules import Kind, State, Status, Tag, decide


def state(**kw):
    # The Georgette Bandhani saree, guest: list ₹7,400, floor ₹6,068
    base = dict(list_price=D('7400'), floor_price=D('6068'), opening_price=D('7100'), target_price=D('6750'),
                member_price=D('5950'), bundle_percent=D('5'), can_bundle=True, can_gift_wrap=True)
    base.update(kw)
    return State(**base)


def offer(s, amount):
    return decide(s, 'OFFER', D(amount))


def test_asking_for_best_price_gets_the_opening_offer():
    s = state()
    d = decide(s, 'ASK_PRICE')
    assert d.kind == Kind.COUNTER and d.tag == Tag.OPENING and d.price == D('7100')
    again = decide(s, 'ASK_PRICE')                  # "a little more?": a small goodwill step…
    assert again.tag == Tag.STEP and D('6750') <= again.price < D('7100')
    decide(s, 'ASK_PRICE')
    held = decide(s, 'ASK_PRICE')                   # …only twice; then it asks for an offer
    assert held.tag == Tag.HOLD and held.price >= D('6750')


def test_yes_before_any_price_triggers_opening_not_a_dead_end():
    d = decide(state(), 'ACCEPT_COUNTER')
    assert d.kind == Kind.COUNTER and d.tag == Tag.OPENING


def test_user_conversation_replay_sells_instead_of_stalling():
    s = state()
    assert decide(s, 'ASK_PRICE').price == D('7100')
    low = offer(s, '2000')
    assert low.kind == Kind.COUNTER and D('6068') <= low.price < D('7100')   # counters even a low-ball
    four = offer(s, '4000')
    assert four.kind == Kind.COUNTER and four.price < low.price
    glide = offer(s, '4000')                                                 # still far from the floor: a big step, not the floor
    assert glide.kind == Kind.COUNTER and D('6068') < glide.price < four.price
    stuck = offer(s, '4000')
    while stuck.kind == Kind.COUNTER:
        stuck = offer(s, '4000')
    assert stuck.kind == Kind.FINAL and stuck.price == D('6100')            # floor, rounded to ₹50
    assert decide(s, 'ACCEPT_COUNTER').kind == Kind.ACCEPT and s.accepted_price == D('6100')


def test_concessions_shrink_and_never_cross_floor():
    s = state()
    prices = [offer(s, a).price for a in ('5500', '5600', '5700', '5800', '5900')]
    real = [p for p in prices if p is not None]
    assert all(p >= D('6068') for p in real)
    assert real == sorted(real, reverse=True)


def test_reciprocity_bigger_move_gets_bigger_step():
    a, b = state(), state()
    offer(a, '6200'); offer(b, '6200')
    small = offer(a, '6250').price
    big = offer(b, '6600').price
    assert big is None or small is None or big <= small


def test_meet_in_the_middle_then_deal():
    s = state()
    offer(s, '6500')                       # counter somewhere above
    d = offer(s, s.position - D('150'))    # within ₹200 → meet
    assert d.kind in (Kind.COUNTER, Kind.ACCEPT)
    if d.kind == Kind.COUNTER:
        assert d.tag == Tag.MEET
        assert decide(s, 'ACCEPT_COUNTER').kind == Kind.ACCEPT


def test_matching_our_price_accepts_at_our_price():
    s = state()
    counter = offer(s, '6500').price
    d = offer(s, counter + D('500'))
    assert d.kind == Kind.ACCEPT and d.price == counter


def test_gift_wrap_sweetener_only_once_most_budget_is_spent():
    s = state()
    for a in ('6100', '6150', '6200'):
        offer(s, a)
    assert s.perks == []                    # ~60% spent: no sweetener yet
    s2 = state()
    perks = []
    for _ in range(6):
        d = offer(s2, '3000')
        perks += d.perks
        if d.kind == Kind.FINAL:
            break
    assert d.kind == Kind.FINAL and perks == ['gift_wrap']
    done = decide(s2, 'ACCEPT_COUNTER')
    assert done.kind == Kind.ACCEPT and done.perks == ['gift_wrap']


def test_stuck_below_floor_final_then_member_then_bundle():
    s = state()
    kinds = [offer(s, '3000').kind for _ in range(7)]
    ladder = [k for k in kinds if k != Kind.COUNTER]
    assert kinds[:2] == [Kind.COUNTER, Kind.COUNTER]
    assert ladder[:3] == [Kind.FINAL, Kind.MEMBER_OFFER, Kind.BUNDLE]
    assert s.status == Status.BUNDLE_OFFERED


def test_signed_in_shopper_skips_member_offer():
    s = state(member_price=None)
    kinds = [offer(s, '3000').kind for _ in range(6)]
    ladder = [k for k in kinds if k != Kind.COUNTER]
    assert ladder[:2] == [Kind.FINAL, Kind.BUNDLE]


def test_walk_away_gets_one_rescue_offer():
    s = state()
    offer(s, '5000')
    before = s.position
    d = decide(s, 'WALK_AWAY')
    assert d.kind == Kind.RESCUE and D('6068') <= d.price < before
    assert decide(s, 'WALK_AWAY').kind == Kind.CLOSED


def test_small_ask_accepted_and_above_list_capped():
    assert offer(state(), '7250').kind == Kind.ACCEPT
    s = state()
    assert offer(s, '9000').price == D('7400')


def test_state_round_trip():
    s = state()
    offer(s, '5000')
    restored = State.from_dict(s.to_dict())
    assert restored == s


def test_money_helpers():
    assert fmt_inr(D('3200000')) == '₹32,00,000'
    assert fmt_inr(D('1850')) == '₹1,850'
    assert nice_price(D('29123'), D('26000')) == D('29100.00')
    assert nice_price(D('26010'), D('26020')) == D('26050.00')
    assert nice_price(D('2304'), D('2112')) == D('2300.00')     # ₹10 steps under ₹5,000
    assert nice_price(D('2296'), D('2112')) == D('2300.00')


def test_jumping_between_lowballs_is_not_rewarded():
    s = state()
    first = offer(s, '2000').price
    second = offer(s, '4000').price                 # still far below the floor
    assert first - second <= (first - D('6068')) * D('0.25')


def test_effort_gate_keeps_price_above_target_until_three_offers():
    s = state()                                     # target ₹6,750, floor ₹6,068
    assert offer(s, '6100').price >= D('6750')
    assert offer(s, '6100').price >= D('6750')
    offer(s, '6100')                                # third offer: the gate opens
    meet = offer(s, '6100')                         # still realistic and persistent: meet in the middle
    assert meet.kind == Kind.COUNTER and meet.tag == Tag.MEET and D('6068') <= meet.price < D('6750')
    assert decide(s, 'ACCEPT_COUNTER').kind == Kind.ACCEPT


def test_offer_between_floor_and_target_not_accepted_on_first_try():
    s = state()
    d = offer(s, '6500')                           # above floor, below target
    assert d.kind == Kind.COUNTER and d.price >= D('6750')


def test_small_items_still_move_every_counter():
    # The Kintsugi bowls: list ₹2,400, Home cap 12% → floor ₹2,112, half-way ₹2,260, opening ₹2,300
    s = State(list_price=D('2400'), floor_price=D('2112'), opening_price=D('2300'), target_price=D('2260'),
              can_gift_wrap=True)
    assert decide(s, 'ASK_PRICE').price == D('2300')
    a = decide(s, 'OFFER', D('1800')).price
    b = decide(s, 'OFFER', D('1850')).price
    assert D('2260') <= b < a < D('2300')          # moving, but not past the half-way point yet


def test_noor_saree_glides_down_and_stays_honest_after_final():
    # The Noor chiffon chat: list ₹9,800, guest floor ₹8,650, sign-in price ₹8,550
    s = State(list_price=D('9800'), floor_price=D('8650'), opening_price=D('9400'), target_price=D('9250'),
              member_price=D('8550'), can_gift_wrap=True)
    prices = [offer(s, a).price for a in ('5000', '5000', '6000', '7000')]
    assert prices[0] <= D('9400')                                   # an offer never gets less than "best price?" would
    assert all(a - b >= 50 for a, b in zip(prices, prices[1:]))       # every counter visibly moves
    assert all(a - b < 1000 for a, b in zip(prices, prices[1:]))      # …and never dumps the whole budget at once
    final = offer(s, '7000')
    assert final.kind == Kind.FINAL and final.price == D('8650')
    member = decide(s, 'ASK_PRICE')                                 # "some more?" after the final: sign-in price
    assert member.kind == Kind.MEMBER_OFFER and member.member_price == D('8550')
    again = decide(s, 'ASK_PRICE')                                  # "what is your last price?"
    assert again.kind == Kind.MEMBER_OFFER and again.price == D('8650') and again.member_price == D('8550')
    chat = decide(s, 'QUESTION')                                    # "but you just said ₹8,550"
    assert chat.member_price == D('8550')


def test_pleading_without_a_number_gets_small_steps_then_offers_are_needed():
    s = state()
    first = offer(s, '5000').price
    nudged = [decide(s, 'ASK_PRICE') for _ in range(3)]
    assert [d.tag for d in nudged] == [Tag.STEP, Tag.STEP, Tag.HOLD]
    assert first - nudged[1].price < (first - D('6750')) * D('0.5')   # small, and never past the half-way target
    assert all(d.price >= D('6750') for d in nudged)
