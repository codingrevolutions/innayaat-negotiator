"""Calls to the Django store backend (product, price rules, shopper tier)."""
import httpx

from .config import Settings


class BackendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def negotiation_quote(settings: Settings, variant_id: str, *, user_token: str | None, guest_id: str | None) -> dict:
    """
    POST /internal/negotiation/quote/  (service key + the shopper's own JWT if signed in).
    The discount budget: list / floor / opening / target prices, levers, product facts,
    the shopper's id and tier, and bundle candidates.
    """
    headers = {'X-Service-Key': settings.backend_service_key}
    if user_token:
        headers['Authorization'] = user_token
    if guest_id:
        headers['X-Session-ID'] = guest_id
    url = settings.backend_url.rstrip('/') + '/internal/negotiation/quote/'
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json={'variant_id': variant_id}, headers=headers)
        except httpx.HTTPError as exc:
            raise BackendError(502, f'Store backend unreachable: {exc.__class__.__name__}')
    if resp.status_code != 200:
        try:
            message = resp.json().get('error', 'This piece is not available to negotiate.')
        except ValueError:
            message = 'This piece is not available to negotiate.'
        raise BackendError(resp.status_code, message)
    return resp.json()


async def report_event(settings: Settings, event: dict) -> None:
    """Best-effort: log one negotiation turn in Django for the outcomes report (never blocks a reply)."""
    if not settings.backend_service_key:
        return
    url = settings.backend_url.rstrip('/') + '/internal/negotiation/events/'
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=event, headers={'X-Service-Key': settings.backend_service_key})
    except httpx.HTTPError:
        pass
