"""Checks on the model's reply before the shopper sees it."""
import re
from decimal import Decimal, InvalidOperation

# ₹25,000 / Rs 25k / 25000 rupees / bare 25,000 or 25000 (but not 350 ml, 27 cm, 2026)
MONEY_RE = re.compile(
    r'(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)(?:\s*(k|lakh)\b)?'
    r'|\b(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)\b(?!\s*(?:ml|cm|mm|m|g|kg|%))',
    re.I,
)
YEAR_RE = re.compile(r'^(19|20)\d{2}$')
FORBIDDEN = re.compile(r'\b(floor|minimum price|lowest (?:we|i) can|margin|cost price|system prompt|as an ai)\b', re.I)


def _amounts(text: str) -> list[Decimal]:
    found = []
    for a, unit, b in MONEY_RE.findall(text):
        raw = (a or b).replace(',', '')
        if not a and YEAR_RE.match(raw):
            continue
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        if unit.lower() == 'k':
            value *= 1000
        elif unit.lower() == 'lakh':
            value *= 100000
        found.append(value)
    return found


ADD_TO_BAG = re.compile(r'add (it )?to (your |the )?(bag|cart)|hold (this|the) price', re.I)


# Words that claim we can't go lower — only true on a final move
FINALITY = re.compile(r"\b(my best|best price|very best|best i can|lowest|least i can|least price|final price|"
                      r"last price|rock bottom|can'?t go (?:any )?lower|as far as i can|bas itna hi)\b", re.I)


def problems(text: str, allowed: set[Decimal], *, agreed: bool = False, final: bool = False) -> list[str]:
    """Why a reply is unsafe to send (empty list = fine). `agreed`: a deal is done; `final`: it's our last word."""
    issues = []
    if not final and not agreed and FINALITY.search(text):
        issues.append('claims a best/final price that is not final')
    if not agreed and ADD_TO_BAG.search(text):
        issues.append('talks about adding to bag before a deal')
    for amount in _amounts(text):
        if not any(abs(amount - ok) <= Decimal('1') for ok in allowed):
            issues.append(f'unapproved amount {amount}')
    if FORBIDDEN.search(text):
        issues.append('mentions internal pricing')
    if len(text) > 700:
        issues.append('too long')
    return issues
