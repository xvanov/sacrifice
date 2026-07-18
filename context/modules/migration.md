# Migration module

## Purpose
The migration module contains shell scripts for packaging and restoring a working Sacrifice environment across machines (`scripts/migration/bootstrap.sh`, `scripts/migration/bundle.sh`).

## Shape
- `scripts/migration/bundle.sh` packages source, env files, and a PostgreSQL dump into a migration bundle.
- `scripts/migration/bootstrap.sh` restores that bundle, installs dependencies from the repo manifests, recreates services such as PostgreSQL/Redis when needed, and helps rebuild the local environment.

## Security relevance
These scripts touch environment files, local state, and database contents, so they are adjacent to auth and token handling even though they are not part of the request path. If the database or env bundle contains auth material, the migration bundle can move that material wholesale between machines.

## Current constraints
- The scripts are intended for local machine bootstrap and data transfer, not for production deployment (`scripts/migration/bootstrap.sh`, `scripts/migration/bundle.sh`).
- They rely on the repository's existing dependency manifests rather than defining a separate runtime (`backend/pyproject.toml`, `frontend/package.json`, `scripts/migration/bootstrap.sh`).
