from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

# Pool sizing rationale:
# - pool_size=10: covers critical-path webhook + API reads concurrently
# - max_overflow=20: absorbs background-task bursts (AI calls, cache invalidation)
# - pool_timeout=10: fail fast if pool is exhausted rather than stalling TradingView
# - pool_recycle=1800: refresh connections every 30 min to avoid stale TCP / PG timeouts
engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=1800,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session
