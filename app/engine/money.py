from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

ZERO = Decimal('0')


def D(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def price_step(value: Decimal) -> Decimal:
    """Rounding unit that still lets small concessions show: ₹10 under ₹5,000, ₹50 under ₹50,000, else ₹100."""
    return Decimal('10') if value < 5000 else Decimal('50') if value < 50000 else Decimal('100')


def nice_price(value: Decimal, floor: Decimal) -> Decimal:
    """Round to a friendly figure (see price_step), never below the floor."""
    step = price_step(value)
    rounded = (value / step).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * step
    if rounded < floor:
        rounded = (floor / step).quantize(Decimal('1'), rounding=ROUND_CEILING) * step
    return rounded.quantize(Decimal('0.01'))


def fmt_inr(value: Decimal) -> str:
    """₹32,000 / ₹1,850 (Indian grouping, no paise for whole rupees)."""
    value = D(value)
    whole = int(value)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ','.join(groups) + ',' + tail
    paise = (value - whole).quantize(Decimal('0.01'))
    return f"₹{s}" + (f".{int(paise * 100):02d}" if paise else '')
