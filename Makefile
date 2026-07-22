.DEFAULT_GOAL := help

.PHONY: help test lint

help:  ## Show this help
	@echo "judb — available make targets:"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

test:  ## Run all tests
	uv run pytest

lint:  ## Run all linting and formatting
	uv run pre-commit run --all-files
