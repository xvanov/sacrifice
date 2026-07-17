# Makefile for the sacrifice dev stack
# Manages: Postgres (docker), FastAPI backend, Celery worker, Expo frontend.
# Process control is done by PORT (lsof), not by PID file.

SHELL        := /bin/bash
PORT_BE      := 8000
PORT_FE      := 8082
DB_CONTAINER := sacrifice-db
LOG_DIR      := logs
BACKEND_DIR  := backend
FRONTEND_DIR := frontend
VENV         := $(BACKEND_DIR)/.venv
BE_LOG       := $(LOG_DIR)/backend.log
FE_LOG       := $(LOG_DIR)/frontend.log
CELERY_LOG   := $(LOG_DIR)/celery.log

BE_HEALTH_URL := http://localhost:$(PORT_BE)/api/health
FE_URL        := http://localhost:$(PORT_FE)/

# Backend readiness timeout (seconds); frontend takes longer for first bundle.
BE_TIMEOUT := 30
FE_TIMEOUT := 60

.PHONY: help up down restart status health logs test e2e smoke cli-link \
        up-db up-backend up-frontend \
        down-db down-backend down-frontend \
        celery stop-celery \
        wait-backend wait-frontend \
        _logdir

help:
	@echo "Targets:"
	@echo "  up         Start postgres, backend, frontend (idempotent, waits for readiness)"
	@echo "  down       Stop frontend, backend, postgres"
	@echo "  restart    down + up"
	@echo "  status     Show port + container state and probe health endpoints"
	@echo "  health     One-shot health probe; exits non-zero if anything unhealthy"
	@echo "  logs       tail -f backend + frontend logs"
	@echo "  cli-link   Symlink the 'sacrifice' CLI into ~/.local/bin"
	@echo "  celery     Start celery worker in background (logs/celery.log)"
	@echo "  stop-celery  Stop the celery worker"
	@echo "  test       Run backend pytest + frontend jest"
	@echo "  e2e        Run the CLI end-to-end test (needs live stack + celery + SACRIFICE_TOKEN)"

_logdir:
	@mkdir -p $(LOG_DIR)

cli-link:
	@mkdir -p $(HOME)/.local/bin
	@ln -sf $(abspath $(VENV)/bin/sacrifice) $(HOME)/.local/bin/sacrifice
	@echo "[cli] symlinked $(VENV)/bin/sacrifice -> $(HOME)/.local/bin/sacrifice"
	@echo "[cli] ensure ~/.local/bin is on your PATH (it usually is)"

# ------- UP -------

up: _logdir cli-link up-db up-backend up-frontend wait-backend wait-frontend
	@echo ""
	@echo "Stack is up:"
	@echo "  Backend : $(BE_HEALTH_URL)"
	@echo "  Frontend: $(FE_URL)"
	@echo "  Postgres: container $(DB_CONTAINER) (port 5433)"
	@echo "  Logs    : $(BE_LOG), $(FE_LOG)"
	@echo "  CLI     : sacrifice (try 'sacrifice --help')"

up-db:
	@if [ "$$(docker inspect -f '{{.State.Running}}' $(DB_CONTAINER) 2>/dev/null)" = "true" ]; then \
		echo "[db] $(DB_CONTAINER) already running"; \
	else \
		echo "[db] starting $(DB_CONTAINER)..."; \
		docker start $(DB_CONTAINER) >/dev/null; \
	fi

# Media storage for the dev runtime. The app default (/var/sacrifice/media)
# needs root; for local dev we use a repo-local, writable dir. This is set
# only on the runtime process (NOT in .env) so it does not leak into pytest,
# which reads ../.env and asserts the production default path.
MEDIA_DIR := $(abspath .media)
# Goal-type generation writes "directions" to disk for the factory to pick up.
# Default (/var/factory/directions) needs root; use a repo-local dir for dev.
# Runtime-only (NOT .env) so pytest's temp_directions_path fixture is unaffected.
DIRECTIONS_DIR := $(abspath .directions)

# OAuth runtime config (Google/GitHub). Kept HERE rather than in .env because
# pytest reads ../.env and hardcodes the production defaults (e.g. FRONTEND_URL
# = http://localhost:8082) — putting these in .env breaks those tests. As
# runtime env they override the .env values only for the live server.
#
# When this machine has a Tailscale MagicDNS name, OAuth is anchored on
# https://<name>.ts.net — served by `tailscale serve` (443 → frontend :8082,
# /api + /auth → backend :8000). HTTPS on a real domain is the only setup
# Google accepts beyond http://localhost, and it works from every tailnet
# device. Register the callback URLs printed by `make oauth-urls` once in the
# Google/GitHub consoles. Falls back to the localhost setup (HANDOFF.md §3)
# when Tailscale is absent.
# The LIVE app's database. Deliberately NOT in .env: the software-factory /
# bench copies .env into its worktrees and runs pytest there, and the test
# suite TRUNCATEs whatever DB .env names — that wiped live data twice
# (2026-07-16/17). .env keeps the bench-scratch "sacrifice" DB; the real
# server and celery get this override at runtime only.
LIVE_DB_URL := postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice_live

PORT_FE_WEB := 8090
TS_HOST := $(shell tailscale status --json 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null)
ifneq ($(TS_HOST),)
OAUTH_FRONTEND_URL  := https://$(TS_HOST)
OAUTH_GOOGLE_RDR    := https://$(TS_HOST)/api/auth/google/callback
OAUTH_GITHUB_RDR    := https://$(TS_HOST)/auth/github/callback
else
OAUTH_FRONTEND_URL  := http://localhost:$(PORT_FE_WEB)
OAUTH_GOOGLE_RDR    := http://localhost:$(PORT_BE)/api/auth/google/callback
OAUTH_GITHUB_RDR    := http://localhost:$(PORT_BE)/auth/github/callback
endif

.PHONY: oauth-urls
oauth-urls:
	@echo "Frontend URL (after-login redirect): $(OAUTH_FRONTEND_URL)"
	@echo "Google  redirect URI to register  : $(OAUTH_GOOGLE_RDR)"
	@echo "GitHub  callback URL to register  : $(OAUTH_GITHUB_RDR)"

up-backend: _logdir
	@if lsof -ti :$(PORT_BE) >/dev/null 2>&1; then \
		echo "[backend] already bound on :$(PORT_BE), skipping"; \
	else \
		echo "[backend] starting uvicorn on :$(PORT_BE) (log: $(BE_LOG), media: $(MEDIA_DIR))..."; \
		mkdir -p $(MEDIA_DIR) $(DIRECTIONS_DIR); \
		cd $(BACKEND_DIR) && \
			DATABASE_URL=$(LIVE_DB_URL) \
			SACRIFICE_MEDIA_DIR=$(MEDIA_DIR) \
			DIRECTIONS_PATH=$(DIRECTIONS_DIR) FACTORY_DIRECTIONS_PATH=$(DIRECTIONS_DIR) \
			FRONTEND_URL=$(OAUTH_FRONTEND_URL) \
			GOOGLE_REDIRECT_URI=$(OAUTH_GOOGLE_RDR) GITHUB_REDIRECT_URI=$(OAUTH_GITHUB_RDR) \
			nohup .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port $(PORT_BE) \
			> ../$(BE_LOG) 2>&1 & disown; \
	fi

up-frontend: _logdir
	@if lsof -ti :$(PORT_FE) >/dev/null 2>&1; then \
		echo "[frontend] already bound on :$(PORT_FE), skipping"; \
	else \
		echo "[frontend] starting expo on :$(PORT_FE) (log: $(FE_LOG))..."; \
		cd $(FRONTEND_DIR) && nohup npx expo start --web --port $(PORT_FE) \
			> ../$(FE_LOG) 2>&1 & disown; \
	fi

wait-backend:
	@echo -n "[backend] waiting for $(BE_HEALTH_URL) "; \
	for i in $$(seq 1 $(BE_TIMEOUT)); do \
		if curl -sf $(BE_HEALTH_URL) >/dev/null 2>&1; then \
			echo " ready"; exit 0; \
		fi; \
		echo -n "."; sleep 1; \
	done; \
	echo " TIMEOUT after $(BE_TIMEOUT)s"; \
	echo "[backend] tail of $(BE_LOG):"; tail -20 $(BE_LOG) 2>/dev/null; \
	exit 1

wait-frontend:
	@echo -n "[frontend] waiting for $(FE_URL) "; \
	for i in $$(seq 1 $(FE_TIMEOUT)); do \
		code=$$(curl -s -o /dev/null -w "%{http_code}" $(FE_URL) 2>/dev/null); \
		if [ "$$code" = "200" ]; then \
			echo " ready (HTTP 200)"; exit 0; \
		fi; \
		echo -n "."; sleep 1; \
	done; \
	echo " TIMEOUT after $(FE_TIMEOUT)s"; \
	echo "[frontend] tail of $(FE_LOG):"; tail -20 $(FE_LOG) 2>/dev/null; \
	exit 1

# ------- DOWN -------

down: down-frontend down-backend stop-celery down-db
	@echo "Stack is down."

down-backend:
	@pids=$$(lsof -ti :$(PORT_BE) 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "[backend] killing pids on :$(PORT_BE): $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 1; \
		pids2=$$(lsof -ti :$(PORT_BE) 2>/dev/null); \
		if [ -n "$$pids2" ]; then \
			echo "[backend] still alive, SIGKILL: $$pids2"; \
			kill -9 $$pids2 2>/dev/null || true; \
		fi; \
	else \
		echo "[backend] nothing on :$(PORT_BE)"; \
	fi

down-frontend:
	@pids=$$(lsof -ti :$(PORT_FE) 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "[frontend] killing pids on :$(PORT_FE): $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 1; \
		pids2=$$(lsof -ti :$(PORT_FE) 2>/dev/null); \
		if [ -n "$$pids2" ]; then \
			echo "[frontend] still alive, SIGKILL: $$pids2"; \
			kill -9 $$pids2 2>/dev/null || true; \
		fi; \
	else \
		echo "[frontend] nothing on :$(PORT_FE)"; \
	fi

down-db:
	@if [ "$$(docker inspect -f '{{.State.Running}}' $(DB_CONTAINER) 2>/dev/null)" = "true" ]; then \
		echo "[db] stopping $(DB_CONTAINER)..."; \
		docker stop $(DB_CONTAINER) >/dev/null; \
	else \
		echo "[db] $(DB_CONTAINER) not running"; \
	fi

restart: down up

# ------- STATUS / HEALTH -------

status:
	@echo "=== ports ==="
	@for p in $(PORT_BE) $(PORT_FE); do \
		pids=$$(lsof -ti :$$p 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "  :$$p bound by pids: $$pids"; \
		else \
			echo "  :$$p free"; \
		fi; \
	done
	@echo "=== docker ==="
	@state=$$(docker inspect -f '{{.State.Status}}' $(DB_CONTAINER) 2>/dev/null || echo "not-found"); \
	echo "  $(DB_CONTAINER): $$state"
	@echo "=== health ==="
	@if out=$$(curl -sf $(BE_HEALTH_URL) 2>/dev/null); then \
		echo "  backend  OK  $$out"; \
	else \
		echo "  backend  FAIL  $(BE_HEALTH_URL)"; \
	fi
	@code=$$(curl -s -o /dev/null -w "%{http_code}" $(FE_URL) 2>/dev/null); \
	if [ "$$code" = "200" ]; then \
		echo "  frontend OK  HTTP $$code"; \
	else \
		echo "  frontend FAIL  HTTP $$code"; \
	fi

health:
	@rc=0; \
	if out=$$(curl -sf $(BE_HEALTH_URL) 2>/dev/null); then \
		echo "backend  OK  $$out"; \
	else \
		echo "backend  FAIL  $(BE_HEALTH_URL)"; rc=1; \
	fi; \
	code=$$(curl -s -o /dev/null -w "%{http_code}" $(FE_URL) 2>/dev/null); \
	if [ "$$code" = "200" ]; then \
		echo "frontend OK  HTTP $$code"; \
	else \
		echo "frontend FAIL  HTTP $$code"; rc=1; \
	fi; \
	exit $$rc

# ------- LOGS -------

logs:
	@touch $(BE_LOG) $(FE_LOG)
	@tail -f $(BE_LOG) $(FE_LOG)

# ------- CELERY -------

# Celery has no listening port, so we identify it by command name.
# The worker's argv is "<abs venv>/bin/python3 .venv/bin/celery -A ... worker"
# (celery re-execs through its venv python), so match on the absolute venv
# path followed by celery+worker — anchoring on "$(VENV)/bin/celery" never
# matched and left stale workers running.
CELERY_PATTERN := $(abspath $(VENV)).*celery.*worker

celery: _logdir
	@if pgrep -af "$(CELERY_PATTERN)" | grep -v pgrep >/dev/null 2>&1; then \
		echo "[celery] worker already running"; \
	else \
		echo "[celery] starting worker+beat (log: $(CELERY_LOG))..."; \
		cd $(BACKEND_DIR) && DATABASE_URL=$(LIVE_DB_URL) \
			nohup .venv/bin/celery -A app.core.celery_app worker -B --loglevel=info \
			> ../$(CELERY_LOG) 2>&1 & disown; \
	fi

stop-celery:
	@pids=$$(pgrep -af "$(CELERY_PATTERN)" | grep -v pgrep | awk '{print $$1}'); \
	if [ -n "$$pids" ]; then \
		echo "[celery] killing pids: $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 1; \
		pids2=$$(pgrep -af "$(CELERY_PATTERN)" | grep -v pgrep | awk '{print $$1}'); \
		if [ -n "$$pids2" ]; then kill -9 $$pids2 2>/dev/null || true; fi; \
	else \
		echo "[celery] nothing to stop"; \
	fi

# ------- TESTS -------

test:
	@echo "=== backend pytest ==="
	cd $(BACKEND_DIR) && .venv/bin/pytest
	@echo "=== frontend jest ==="
	cd $(FRONTEND_DIR) && npx jest

# End-to-end CLI test. NOT part of `make test` because it requires the full
# live stack (backend on :$(PORT_BE), Postgres, Redis, Celery worker) plus a
# valid JWT in $$SACRIFICE_TOKEN, and it hits external services (api.github.com).
# Run `make up && make celery` first, then `SACRIFICE_TOKEN=... make e2e`.
e2e:
	@if [ -z "$$SACRIFICE_TOKEN" ]; then \
		echo "[e2e] SACRIFICE_TOKEN not set. Log in with 'sacrifice login' or export a token."; \
		echo "[e2e]   export SACRIFICE_TOKEN=eyJ..."; \
		exit 1; \
	fi
	@echo "[e2e] running CLI end-to-end test against $${SACRIFICE_API_URL:-http://localhost:$(PORT_BE)}"
	cd $(BACKEND_DIR) && .venv/bin/python e2e_test.py

# Runtime smoke — the factory's D002 verifier. Boots/reuses the backend and
# drives register→login→create→activate→submit-proof. No token, no Celery, no
# LLM, no external network (the api_endpoint goal points at the backend's own
# /api/health). This is the fast pre-merge "does the product actually run?"
# gate, distinct from `e2e` (CLI, full stack, external services).
smoke:
	@./scripts/smoke.sh
