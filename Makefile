SHELL := /bin/bash
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env

.PHONY: help up down logs ps restart build pull clean \
	backend.dev frontend.dev backend.shell backend.test backend.lint \
	migrate revision init seed-connectors reset-totp \
	backup.now backup.list backup.restore backup.verify mint-cert \
	llm.up llm.down llm.logs llm.pull llm.models

help:
	@grep -E '^[a-zA-Z_.-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- compose ---
up: ## Start the full stack
	$(COMPOSE) up -d
down: ## Stop the stack
	$(COMPOSE) down
logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=200
ps: ## List services
	$(COMPOSE) ps
restart: ## Restart all services
	$(COMPOSE) restart
build: ## Build all images
	$(COMPOSE) build
pull: ## Pull base images
	$(COMPOSE) pull

# --- backend ---
backend.dev: ## Run backend in dev mode (uvicorn reload)
	cd backend && uvicorn daedalus.main:app --reload --host 0.0.0.0 --port 8000
backend.shell: ## Open a shell inside the api container
	$(COMPOSE) exec api bash
backend.test: ## Run pytest
	cd backend && pytest -q
backend.lint: ## Run ruff
	cd backend && ruff check .

ci: ## Run the full gate locally (backend lint+tests, frontend typecheck+unit tests)
	cd backend && ruff check . && pytest -q
	cd frontend && npm ci && npx tsc -b && npm test

# --- db ---
migrate: ## Apply DB migrations
	$(COMPOSE) exec api alembic upgrade head
revision: ## Create new alembic revision (use MSG=)
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(MSG)"

# --- frontend ---
frontend.dev: ## Run the Vite dev server (proxies /api → :8000, /ws → :8001)
	cd frontend && npm install && npm run dev
frontend.build: ## Build the SPA
	cd frontend && npm install && npm run build
frontend.test: ## Run frontend unit + component tests (vitest)
	cd frontend && npm install && npm test
frontend.e2e: ## Run Playwright E2E against a live stack (set E2E_BASE_URL)
	cd frontend && npm install && npx playwright install --with-deps chromium && npm run test:e2e

# --- observability + dev profile ---
obs.up: ## Start observability stack (prometheus + grafana + loki + otel + mailpit)
	$(COMPOSE) --profile dev --profile observability up -d
obs.down: ## Stop observability stack
	$(COMPOSE) --profile dev --profile observability down

# --- self-hosted LLM (vLLM) ---
llm.up: ## Bring up the vLLM service (requires NVIDIA toolkit on the host)
	$(COMPOSE) --profile llm up -d llm
llm.down: ## Stop the vLLM service
	$(COMPOSE) --profile llm stop llm
llm.logs: ## Tail vLLM logs
	$(COMPOSE) --profile llm logs -f --tail=200 llm
llm.pull: ## Pre-pull the vLLM image without starting the model
	$(COMPOSE) --profile llm pull llm
llm.models: ## List models the vLLM container is currently serving
	$(COMPOSE) --profile llm exec llm curl -s http://localhost:8000/v1/models

# --- platform ---
init: ## First-run init (create owner, TOTP enroll, import connectors)
	$(COMPOSE) exec api python -m daedalus.cli init
seed-connectors: ## Re-import default connector pack
	$(COMPOSE) exec api python -m daedalus.cli import-connectors /etc/daedalus/connectors
reset-totp: ## Re-issue TOTP + recovery codes for a user (use EMAIL=)
	@test -n "$(EMAIL)" || (echo "set EMAIL=user@example.com" && exit 1)
	$(COMPOSE) exec api python -m daedalus.cli reset-totp --email "$(EMAIL)"
mint-cert: ## Mint an mTLS client cert for a user (use EMAIL= [PIN=true])
	@test -n "$(EMAIL)" || (echo "set EMAIL=user@example.com" && exit 1)
	$(COMPOSE) exec api python -m daedalus.cli mint-client-cert \
		--email "$(EMAIL)" \
		--out-dir /run/daedalus/secrets/clients \
		$(if $(filter true,$(PIN)),--pin,)

# --- backups ---
backup.now: ## Trigger an on-demand pg_dump → MinIO upload now
	$(COMPOSE) exec pg-backup pg-backup
backup.list: ## List dumps currently stored in MinIO
	$(COMPOSE) exec pg-backup mc ls --recursive "daedalus/$$PG_BACKUP_BUCKET/" || true
backup.restore: ## Restore from a specific dump (use KEY=db/20260503T0230Z.sql.gz)
	@test -n "$(KEY)" || (echo "set KEY=<bucket-key>; see make backup.list" && exit 1)
	$(COMPOSE) exec pg-backup bash -c '\
		mc cp "daedalus/$$PG_BACKUP_BUCKET/$(KEY)" /tmp/restore.sql.gz && \
		gunzip -c /tmp/restore.sql.gz | PGPASSWORD=$$POSTGRES_PASSWORD psql \
			-h $$POSTGRES_HOST -U $$POSTGRES_USER -d $$POSTGRES_DB'
backup.verify: ## Restore-test the latest dump into a scratch DB (#21 — proves backups are restorable)
	$(COMPOSE) exec pg-backup bash -c '\
		set -e; \
		KEY=$$(mc ls --recursive "daedalus/$$PG_BACKUP_BUCKET/" | sort | tail -1 | awk "{print \$$NF}"); \
		echo "verifying restore of $$KEY"; \
		mc cp "daedalus/$$PG_BACKUP_BUCKET/$$KEY" /tmp/verify.sql.gz; \
		PGPASSWORD=$$POSTGRES_PASSWORD psql -h $$POSTGRES_HOST -U $$POSTGRES_USER -d postgres \
			-c "DROP DATABASE IF EXISTS daedalus_restore_check; CREATE DATABASE daedalus_restore_check"; \
		gunzip -c /tmp/verify.sql.gz | PGPASSWORD=$$POSTGRES_PASSWORD psql \
			-h $$POSTGRES_HOST -U $$POSTGRES_USER -d daedalus_restore_check >/dev/null; \
		PGPASSWORD=$$POSTGRES_PASSWORD psql -h $$POSTGRES_HOST -U $$POSTGRES_USER -d daedalus_restore_check \
			-c "SELECT count(*) AS tables FROM information_schema.tables WHERE table_schema='"'"'public'"'"'"; \
		PGPASSWORD=$$POSTGRES_PASSWORD psql -h $$POSTGRES_HOST -U $$POSTGRES_USER -d postgres \
			-c "DROP DATABASE daedalus_restore_check"; \
		echo "restore verified OK"'

clean: ## Remove dist + caches (does not touch volumes)
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
