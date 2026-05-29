from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True, pool_size=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session = async_session()
    try:
        yield session
    except GeneratorExit:
        # When 'async for ... break' is used in Python < 3.13, the
        # generator's cleanup races with in-progress operations on the
        # session.  We catch GeneratorExit so the caller retains
        # ownership; the connection returns to the pool when the session
        # is garbage-collected.  In the normal path (FastAPI DI with
        # proper context teardown), the framework catches the StopIteration
        # after the route handler returns and then the generator's async
        # cleanup properly closes the session.
        pass
    else:
        await session.close()
