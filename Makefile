.DEFAULT_GOAL := help

.PHONY: help test lint frontend frontend-install frontend-check frontend-test frontend-e2e

help:  ## Show this help
	@echo "judb — available make targets:"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

test:  ## Run all Python tests
	uv run pytest

lint:  ## Run all linting and formatting
	uv run pre-commit run --all-files

frontend-install:  ## Install frontend deps (pnpm via corepack)
	cd frontend && pnpm install

frontend:  ## Build the frontend bundle into judb/static/
	cd frontend && pnpm run build

frontend-check:  ## Type-check the frontend (svelte-check)
	cd frontend && pnpm run check

frontend-test:  ## Run frontend unit tests (Vitest)
	cd frontend && pnpm run test

frontend-e2e:  ## Run browser e2e (Playwright; builds first, so always current)
	cd frontend && pnpm run e2e
