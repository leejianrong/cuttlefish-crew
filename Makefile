.DEFAULT_GOAL := help
.PHONY: help dev demo lint type check test test-all test-sandbox-live test-live ci install-hooks \
	install-systemd-service frontend-install frontend-check frontend-test frontend-build docs

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev: ## Sync the dev environment (uv sync)
	uv sync

# The one-command way to actually see the dashboard running (slice D2,
# dev-playbook's own "runnable in one command" guidance) -- one process now,
# not two (ADR-0012, KAN-1707): builds the dashboard once, then `cuttlefish
# serve` alone serves both the JSON API and that build, same origin. Installs
# frontend/node_modules itself if it's missing, so this is genuinely the first
# command a newcomer needs, not a second step after `cd frontend && npm
# install` first. Developing the frontend itself (hot reload) still means
# running `cd frontend && npm run dev` and `cuttlefish serve` separately.
demo: ## Build the dashboard + run cuttlefish serve (LOG=1 or LOG=<path> also saves the output)
	./scripts/demo.sh

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

type: ## mypy --strict over src
	uv run mypy src

check: lint type ## Lint + type-check

test: ## Unit tests only — the fast inner-loop target, no infra
	uv run pytest tests/unit -q

# The kopicode delegation is tested against kopicode's own headless surface directly
# (docs/PLAN.md "Testing approach"), never a mock. Those tests need a real `kopicode`
# binary on PATH and skip themselves, rather than fail, when it's absent — the same
# posture satay-runtime's own studio-gated tests take for a missing extra.
test-all: ## The FULL suite (unit + integration + e2e)
	uv run pytest -q

# Real kopicode against a real model (the `requires_live_credential` tests): cents per run, and
# they were once spent on every `make test-all` whenever `.env` held a key.
test-live: ## real kopicode + real model tests (OPENROUTER_API_KEY) — COSTS MONEY (cents), never in CI
	@echo "This spends real model credit (cents). Ctrl-C to abort."
	@read -r -p "continue? [y/N] " a; [ "$$a" = "y" ] || exit 1
	CUTTLEFISH_TEST_LIVE=1 uv run pytest -m requires_live_credential -q

# cuttlefish/sandbox's E2bSandboxProvider is tested against a real E2B account
# (docs/SLICES.md V2 test plan), the same cost-bearing posture kopicode's own
# paid `make bench` takes — never in CI, always an explicit, confirmed local run.
test-sandbox-live: ## create/exec/snapshot/destroy against a real E2B account — COSTS MONEY, never in CI
	@echo "This spends real E2B account time. Ctrl-C to abort."
	@read -r -p "continue? [y/N] " a; [ "$$a" = "y" ] || exit 1
	uv run pytest -m requires_e2b_credential -q

# The dashboard frontend (frontend/, ADR-0009) -- this repo's first non-Python
# build pipeline. `npm ci` (not `install`) so a stale lockfile fails loudly in CI
# rather than silently drifting, the same discipline `uv sync --frozen` already
# holds for the Python side.
frontend-install: ## npm ci in frontend/
	cd frontend && npm ci

frontend-check: ## svelte-check + tsc over frontend/ -- no build step
	cd frontend && npm run check

frontend-test: ## vitest over frontend/ -- pure-logic unit tests, no browser/DOM
	cd frontend && npm run test

frontend-build: ## Production build of frontend/ to frontend/dist/
	cd frontend && npm run build

ci: check test-all frontend-check frontend-test frontend-build ## Everything CI gates on (lint + mypy + full suite + frontend)

# docs-site/ (KAN-1718) is a separate source tree from docs/ -- curated,
# user-facing content only, never the internal ADR/QUESTIONS/gtm material.
docs: ## Build the docs site locally (zensical build --strict) to site/
	uvx zensical build --clean --strict

install-hooks: ## Install the pre-push git hook
	./scripts/install-hooks.sh

# ADR-0014 (KAN-1709): a systemd --user unit, so cuttlefish serve auto-restarts
# if it dies instead of needing a human to notice. Does not start it -- review
# the installed unit first, then follow the printed next-step commands.
install-systemd-service: ## Install (not start) cuttlefish serve as a systemd --user unit
	./scripts/install-systemd-service.sh
