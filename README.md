# Innayaat Kumar — Negotiator

The "Name your price" concierge, as its own service. Shoppers chat; fixed rules decide
the price; OpenAI models only read messages and phrase replies; Django verifies the
agreed price when the piece is added to the bag.

```
Browser ──chat──► Negotiator (this repo, :8100) ──price rules / shopper tier──► Django (:8000)
   │                     │
   │◄── signed price pass ┘
   └── add to bag + pass ──────────────────────────────────────────────────────► Django verifies (once)
```

## How a price is decided
`app/engine/rules.py` — pure, unit-tested, no AI. Floor and list price come from Django
(`/api/internal/negotiation/context/`), which owns the money rules (loyalty tiers, the
category's max negotiation %). Counters only move toward the shopper and never below
the floor; after 3 low offers it proposes a bundle (if similar pieces exist) or a final price.

## Two models
| Job | Setting | Default |
|---|---|---|
| Read the shopper's message (offer amount, intent, language) | `PARSE_MODEL` | `gpt-5.4-nano` |
| Write the reply | `REPLY_MODEL` | `gpt-5.4-mini` |

`REASONING_EFFORT=none` keeps replies at ~1–3 s. Every reply passes `app/llm/guardrails.py`
(only approved rupee amounts, no talk of floors, no "add to bag" before a deal); anything
that fails is replaced by a hand-written reply from `app/llm/templates.py`. If OpenAI is
down, a regex parser and the templates keep the negotiation working.

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env     # fill OPENAI_API_KEY, and the two shared secrets (same values in Innayaat_backend/.env)
.venv/bin/uvicorn app.main:app --port 8100
.venv/bin/python -m pytest -q
```
Frontend: set `VITE_NEGOTIATOR_URL=http://127.0.0.1:8100` in `Innayaat_frontend2/.env.local`.

## API
- `POST /v1/sessions` `{variant_id}` + `Authorization` (shopper JWT) or `X-Session-ID` → greeting, `session_id`, `session_token`
- `POST /v1/sessions/{id}/messages` `{text}` + `X-Negotiation-Token` → server-sent events: `meta` (decision, price, `price_pass`), `chunk`…, `done` (`?stream=false` for JSON)
- `GET /v1/sessions/{id}` + `X-Negotiation-Token` → transcript and status
- `GET /health`

## Production notes
- Set `REDIS_URL` (sessions shared across workers) and real `CORS_ORIGINS`.
- Secrets only in the host's secret store / `.env` (git-ignored). Rotate the OpenAI key if it was ever shared.
- The rate limiter is per-process; put the service behind one worker or move limits to Redis when scaling out.
