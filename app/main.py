import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from .ratelimit import RateLimiter
from .store import make_store

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def create_app(llm_client=None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title='Innayaat Kumar Negotiator', version='0.1.0')
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_methods=['GET', 'POST'],
        allow_headers=['Authorization', 'Content-Type', 'X-Session-ID', 'X-Negotiation-Token'],
    )
    app.state.store = make_store(settings.redis_url, settings.session_ttl_seconds)
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    if llm_client is not None:
        app.state.llm = llm_client
    elif settings.openai_api_key:
        from openai import AsyncOpenAI
        app.state.llm = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)
    else:
        app.state.llm = None  # templates only
    app.include_router(router)

    @app.get('/health')
    async def health():
        return {'ok': True, 'llm': app.state.llm is not None, 'store': type(app.state.store).__name__}

    return app


app = create_app()
