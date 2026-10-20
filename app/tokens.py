"""
Price passes: a short-lived, signed statement "this shopper may buy variant X at
price P". The frontend hands it to Django when adding to the bag; Django checks
the signature with the same secret, the expiry, the shopper and the one-time id.

Format: base64url(json payload) + "." + base64url(HMAC-SHA256(payload, secret))
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from decimal import Decimal


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def issue(secret: str, *, variant_id: str, subject: str, session_id: str, ttl: int,
          price: Decimal | None = None, bundle_percent: Decimal | None = None, perks: list | None = None) -> str:
    if not secret:
        raise RuntimeError('PRICE_PASS_SECRET is not configured')
    payload = {
        'v': str(variant_id),
        'sub': str(subject),
        'sid': session_id,
        'kind': 'BUNDLE' if bundle_percent is not None else 'PRICE',
        'exp': int(time.time()) + ttl,
        'jti': secrets.token_urlsafe(12),
    }
    if price is not None:
        payload['p'] = f'{Decimal(price):.2f}'
    if bundle_percent is not None:
        payload['pct'] = f'{Decimal(bundle_percent):.2f}'
    if perks:
        payload['perks'] = sorted(set(perks))
    body = _b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f'{body}.{sig}'


def verify(secret: str, token: str) -> dict:
    """Return the payload or raise ValueError (used by tests; Django has its own copy)."""
    try:
        body, sig = token.split('.')
    except ValueError:
        raise ValueError('malformed pass')
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ValueError('bad signature')
    payload = json.loads(_unb64(body))
    if payload['exp'] < time.time():
        raise ValueError('expired')
    return payload
