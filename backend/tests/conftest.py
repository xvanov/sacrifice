# Hermetic test env. The suite must run without a real .env (a fresh factory
# worktree / CI checkout has none — .env is gitignored). Every external call is
# mocked in unit tests, so these dummy-but-present values just stop the app
# from short-circuiting/500-ing on empty config. Set BEFORE `app.config` is
# imported below, and via setdefault so a real exported env still wins.
# (Without this, ~13 credential-dependent tests failed only in a credentialless
# checkout, making the factory's dev-loop test gate unsatisfiable — 2026-07-07.)
import os as _os

_TEST_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice",
    "JWT_SECRET": "test-jwt-secret-not-for-production",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_PUBLISHABLE_KEY": "pk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_dummy",
    "GOOGLE_CLIENT_ID": "test-google-client-id",
    "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
    "GITHUB_CLIENT_ID": "test-github-client-id",
    "GITHUB_CLIENT_SECRET": "test-github-client-secret",
    "YOUTUBE_API_KEY": "test-youtube-key",
    "AZURE_FOUNDRY_ENDPOINT": "https://test-foundry.example.com/",
    "AZURE_FOUNDRY_API_KEY": "test-azure-key",
    "SACRIFICE_MEDIA_DIR": "/tmp/sacrifice-test-media",
    # Never run the suite against live Stripe. The deployed ``.env`` carries the
    # live keys and ``STRIPE_LIVE_MODE``, and ``app.config`` reads that file — so
    # without this the suite would resolve a real ``sk_live_`` key. Most tests mock
    # Stripe, but not all: an unpatched deadline sweep has previously reached
    # ``PaymentMethod.list`` for real (see the charge_boundary note in
    # tests/test_blocked_goals_operator.py), and doing that with a live key moves
    # real money. Forced below as well, because a default is not a guarantee.
    "STRIPE_LIVE_MODE": "false",
}
for _k, _v in _TEST_ENV_DEFAULTS.items():
    _os.environ.setdefault(_k, _v)

# Not negotiable, unlike the defaults above: an exported STRIPE_LIVE_MODE must not
# be able to point the suite at real cards.
_os.environ["STRIPE_LIVE_MODE"] = "false"

import pytest_asyncio
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.base import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = settings.database_url

# Collection-time assertion, so a suite that would touch live Stripe cannot even
# start. Checked against the resolved settings rather than the env var, because
# what matters is the key ``stripe.api_key`` is actually assigned from.
assert not settings.stripe_live_mode, (
    "settings.stripe_live_mode is on during tests; refusing to run against live Stripe"
)
assert not settings.stripe_secret_key.startswith("sk_live_"), (
    "a live Stripe secret key resolved during tests; refusing to run — a single "
    "unmocked call would move real money"
)


async def _ensure_chat_session_columns(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS session_id VARCHAR(255)"
            )
        )
        await conn.execute(
            text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS goal_id UUID")
        )
        await conn.execute(
            text(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS awaiting_direction_id VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "UPDATE chat_sessions SET last_activity_at = COALESCE(last_activity_at, updated_at, created_at, NOW())"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE chat_sessions ALTER COLUMN last_activity_at SET DEFAULT NOW()"
            )
        )
        await conn.execute(
            text("ALTER TABLE chat_sessions ALTER COLUMN last_activity_at SET NOT NULL")
        )
        # D010: goals table may have been created before awaiting_direction_id was added.
        await conn.execute(
            text(
                "ALTER TABLE goals ADD COLUMN IF NOT EXISTS awaiting_direction_id VARCHAR(255)"
            )
        )
        # create_all creates an enum type but never ALTERs an existing one, so a
        # long-lived test database keeps the enum it was first built with. Any DB
        # created before proof_dispatch_failed was added fails every
        # broker-outage test with InvalidTextRepresentationError until this runs.
        # Same reasoning as the ADD COLUMN IF NOT EXISTS statements above.
        await conn.execute(
            text(
                "ALTER TYPE audit_event_type "
                "ADD VALUE IF NOT EXISTS 'proof_dispatch_failed'"
            )
        )


@pytest_asyncio.fixture(autouse=True)
async def _clear_rate_limit_store():
    """Clear the rate-limiter store before every test so no test leaks into another."""
    from app.core.rate_limiter import _store

    _store.clear()
    yield


_SCHEMA_READY = False


async def _ensure_schema_once(test_engine):
    """Build the test schema at most once per pytest process.

    This used to run per test (the fixture below is autouse), which meant
    ``create_all`` took an AccessExclusiveLock on every table before every
    single test. At ~850 tests that was merely wasteful; past ~1200 it became
    a correctness problem — the DDL lock races the row locks held by other
    tests' sessions against this same shared database, surfacing as
    ``DeadlockDetectedError`` and ``Could not refresh instance '<User>'`` in
    whichever tests happen to interleave. The schema is process-wide state,
    so it belongs in a process-wide guard, not in the per-test path.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_chat_session_columns(test_engine)
    _SCHEMA_READY = True


@pytest_asyncio.fixture(autouse=True)
async def test_db():
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    test_async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with test_async_session() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    await _ensure_schema_once(test_engine)

    yield

    # Truncate only D010-specific tables after each test to keep isolation
    # without the blanket DROP that the reviewer flagged as too invasive.
    _D010_TABLES = {
        "audit_events",
        "chat_spend_ledger",
        "chat_sessions",
        "goals",
        "goal_criteria",
        "media_uploads",
        "notifications",
        "proof_submissions",
        "payments",
        "reset_token_jtis",
        "users",
        "verification_tokens",
    }
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _D010_TABLES:
                await conn.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await test_engine.dispose()

    app.dependency_overrides.clear()
