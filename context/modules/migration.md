# Migration Scripts

## Purpose
`scripts/migration/` is the repo's machine-to-machine transfer and bootstrap module. It is not part of the runtime app; it exists to preserve Sacrifice state alongside software-factory state when moving development machines (`scripts/migration/README.md`).

## Entry points
- `scripts/migration/bundle.sh` creates a single tarball on the old machine.
- `scripts/migration/bootstrap.sh` restores that tarball on the new machine.
- `scripts/migration/README.md` is the authoritative description of what the scripts preserve, recreate, and intentionally leave out.

## What the workflow preserves
According to `scripts/migration/README.md`, the bundle step preserves:
- `software-factory/.env`
- `software-factory/state/factory.db`
- `sacrifice/.env`
- a PostgreSQL dump taken from the `sacrifice-db` container

The bootstrap step then restores those files, recreates Python virtual environments and frontend dependencies, creates Docker containers for PostgreSQL and Redis, runs `alembic upgrade head`, and smoke-checks the factory setup.

## Operational assumptions and constraints
- The bundle step expects the `sacrifice-db` container to be running so it can `pg_dump` current Sacrifice data.
- The bootstrap step auto-installs missing packages only for Ubuntu/Debian and installs `uv` via the official installer, as described in the README.
- Clone URLs are SSH-based (`git@github.com:...`), so the target machine needs working SSH GitHub access.
- Redis data is intentionally recreated empty; it is treated as broker state, not preserved state.
- The scripts do not copy screenshots, logs, other gitignored scratch files, or CI/GitHub secrets.
