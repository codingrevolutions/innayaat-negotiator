"""Session storage: Redis in production, in-memory for local development and tests."""
import json
import time
from dataclasses import asdict, dataclass, field
from .engine.rules import State


@dataclass
class Session:
    id: str
    token: str                     # secret the browser must send back (proves it owns the session)
    subject: str                   # user id, or guest session id
    variant_id: str
    product: dict                  # title, image, specs, list price … from the backend
    state: State
    messages: list = field(default_factory=list)
    turns: int = 0
    created_at: float = field(default_factory=time.time)
    tier: str = 'GUEST'
    score: int = 0

    def to_json(self) -> str:
        data = asdict(self)
        data['state'] = self.state.to_dict()
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> 'Session':
        data = json.loads(raw)
        data['state'] = State.from_dict(data['state'])
        return cls(**data)


MEMORY_TTL = 24 * 60 * 60  # a shopper's negotiation on a piece is remembered for a day


def memory_key(subject: str, variant_id: str) -> str:
    return f'negotiation-memory:{subject}:{variant_id}'


class MemoryStore:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._data: dict[str, tuple[float, str]] = {}
        self._memory: dict[str, tuple[float, str]] = {}

    async def remember(self, session: 'Session') -> None:
        self._memory[memory_key(session.subject, session.variant_id)] = (
            time.time() + MEMORY_TTL, json.dumps(session.state.to_dict()))

    async def recall(self, subject: str, variant_id: str) -> State | None:
        item = self._memory.get(memory_key(subject, variant_id))
        if not item or item[0] < time.time():
            return None
        return State.from_dict(json.loads(item[1]))

    async def get(self, session_id: str) -> Session | None:
        item = self._data.get(session_id)
        if not item or item[0] < time.time():
            self._data.pop(session_id, None)
            return None
        return Session.from_json(item[1])

    async def save(self, session: Session) -> None:
        self._data[session.id] = (time.time() + self.ttl, session.to_json())


class RedisStore:
    def __init__(self, url: str, ttl: int):
        import redis.asyncio as redis
        self.ttl = ttl
        self.client = redis.from_url(url, decode_responses=True)

    async def get(self, session_id: str) -> Session | None:
        raw = await self.client.get(f'negotiation:{session_id}')
        return Session.from_json(raw) if raw else None

    async def save(self, session: Session) -> None:
        await self.client.set(f'negotiation:{session.id}', session.to_json(), ex=self.ttl)

    async def remember(self, session: Session) -> None:
        await self.client.set(memory_key(session.subject, session.variant_id),
                              json.dumps(session.state.to_dict()), ex=MEMORY_TTL)

    async def recall(self, subject: str, variant_id: str) -> State | None:
        raw = await self.client.get(memory_key(subject, variant_id))
        return State.from_dict(json.loads(raw)) if raw else None


def make_store(redis_url: str, ttl: int):
    return RedisStore(redis_url, ttl) if redis_url else MemoryStore(ttl)
