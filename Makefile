# Makefile — developer tasks for the Declarative Agent Platform.
#
# Run `make install` once to set up the environment (editable install + dev
# tools), then use `make check` as the quality gate. AGENTS.md, CLAUDE.md, and
# the .ai/ skills and tasks refer to these targets.

PYTHON ?= python3
SRC := src
TESTS := tests
FLAGSHIP := examples/enterprise-knowledge-assistant/agents.yaml

.DEFAULT_GOAL := help
.PHONY: help install generate-ai sync-ai sync-skills test lint format typecheck check clean validate inspect

help: ## Show available targets.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: generate-ai ## Install the package (editable) with dev dependencies.
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

generate-ai: ## Generate adapters from .ai/. Use TARGET=claude|codex to limit scope.
	$(PYTHON) -m tools.skills $(if $(TARGET),--target $(TARGET),)

sync-ai: generate-ai ## Alias for generate-ai (older name, kept for docs compatibility).

sync-skills: generate-ai ## Alias for generate-ai (older name, kept for docs compatibility).

format: ## Auto-format the codebase (ruff format).
	ruff format $(SRC) $(TESTS)

lint: ## Lint the codebase (ruff check).
	ruff check $(SRC) $(TESTS)

typecheck: ## Type-check the codebase (mypy).
	mypy $(SRC) $(TESTS)

test: ## Run the test suite (pytest).
	pytest

check: lint typecheck test ## Quality gate: lint + typecheck + test.

validate: ## Validate the flagship example offline (no LLM calls, no network, no API keys).
	agentctl validate $(FLAGSHIP)

inspect: ## Inspect the flagship example offline (agents, MCPs, hooks, plugins, tags).
	agentctl inspect $(FLAGSHIP)

clean: ## Remove caches and build artifacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
