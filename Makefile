# Electricity Planner — developer Makefile
# Run `make help` for the colored target list.

PYTHON ?= python
PIP ?= pip
PKG := custom_components/electricity_planner

# Colors
CYAN  := \033[36m
BOLD  := \033[1m
DIM   := \033[2m
RESET := \033[0m

.DEFAULT_GOAL := help

.PHONY: help install test test-coverage test-file test-name lint format format-check \
        typecheck docs docs-serve clean pre-commit commit update-reqs

help: ## Show this help
	@printf "$(BOLD)Electricity Planner — make targets$(RESET)\n\n"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^[a-zA-Z_-]+:.*?## / { printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2 } \
		/^##@/ { printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@printf "\n$(DIM)Examples:$(RESET)\n"
	@printf "  make test-file f=tests/test_coordinator.py\n"
	@printf "  make test-name n=test_solar_allocation\n\n"

install: ## Install dev dependencies (editable)
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e . || true

test: ## Run pytest
	pytest tests/

test-coverage: ## Run pytest with coverage (term + html)
	pytest --cov=$(PKG) tests/ --cov-report=term --cov-report=html

test-file: ## Run a single test file: make test-file f=tests/test_x.py
	@if [ -z "$(f)" ]; then echo "Usage: make test-file f=tests/test_x.py"; exit 1; fi
	pytest $(f)

test-name: ## Run tests matching a name pattern: make test-name n=pattern
	@if [ -z "$(n)" ]; then echo "Usage: make test-name n=pattern"; exit 1; fi
	pytest tests/ -k "$(n)"

lint: ## Run flake8
	flake8 $(PKG) tests/

format: ## Apply black + isort
	black .
	isort .

format-check: ## Check formatting without writing
	black --check .
	isort --check-only .

typecheck: ## Run mypy on the package
	mypy $(PKG)

docs: ## Build docs (mkdocs)
	mkdocs build

docs-serve: ## Serve docs locally on :8000
	mkdocs serve -a 0.0.0.0:8000

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov site build dist *.egg-info

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

commit: ## Conventional commit via commitizen
	cz commit

update-reqs: ## Refresh requirements-dev.txt from current env
	$(PIP) freeze > requirements-dev.lock
	@echo "Wrote requirements-dev.lock — review and merge into requirements-dev.txt as needed."
