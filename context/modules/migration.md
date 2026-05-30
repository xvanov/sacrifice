# Migration module

## Purpose
`scripts/migration/` contains the repo’s cross-machine state-preservation workflow for software-factory and Sacrifice. It is designed to move code plus preserved local state to a new Linux machine (`scripts/migration/README.md`).

## Entry points and public surfaces
- `scripts/migration/README.md` documents the two-step workflow: create a single migration bundle on the old machine, then restore it on the new machine.
- `scripts/migration/bootstrap.sh` is the new-machine entrypoint. It verifies prerequisites, installs missing tools, clones repos when absent, recreates backend/frontend dependencies, restores preserved state, starts Docker services, runs Alembic, and performs a smoke test.
- The directory listing also shows `bundle.sh`, which the README describes as the old-machine script that captures `.env` files, `factory.db`, and a gzipped Postgres dump into one tarball.

## Operational shape
- The preserved artifacts are factory secrets, factory state DB, Sacrifice secrets, and Sacrifice Postgres data; Redis is recreated empty on the destination machine (`scripts/migration/README.md`).
- The bootstrap path assumes an apt-based Linux distribution for automatic package installation and uses Docker containers named `sacrifice-db` and `sacrifice-redis` for runtime services (`scripts/migration/bootstrap.sh`).
- Python environments are recreated with `uv sync`, while the frontend dependencies are recreated with `npm install` (`scripts/migration/bootstrap.sh`).

## Active constraints
- `bootstrap.sh` is intentionally idempotent but opinionated toward Ubuntu/Debian-like systems with `apt-get` available (`scripts/migration/bootstrap.sh`).
- Repo state comes from git; screenshots, logs, scratch content, and other ignored artifacts are explicitly not migrated (`scripts/migration/README.md`).
- The scripts expect SSH-based GitHub clone access for both repositories (`scripts/migration/bootstrap.sh`, `scripts/migration/README.md`).
