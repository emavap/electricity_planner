# Development

## Setup

### Option A — Devcontainer (recommended)

1. Open the repo in VS Code.
2. "Reopen in Container" — the `.devcontainer` config builds a Python 3.12 image with all dev deps and pre-commit hooks installed.
3. The image works on **amd64** and **arm64** (Raspberry Pi 5).

> **Docker group on the Pi host:** the `marco` user was added to the `docker` group. Existing shells don't pick up the new group membership — open a fresh login shell, run `newgrp docker`, or prefix commands with `sg docker -c "..."` until the next login.

### Option B — Local virtualenv

> **Python version matters.** The pinned `pytest-homeassistant-custom-component==0.12.49` and friends require **Python 3.10–3.12**. The Pi host ships Python 3.13, which will fail to build numpy/pyyaml wheels — use the devcontainer instead, or install Python 3.12 via `pyenv`/`uv` first.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make install
pre-commit install
```

## Common commands

```bash
make help               # colored target list
make test               # run pytest
make test-coverage      # coverage with HTML report in htmlcov/
make test-file f=tests/test_coordinator.py
make test-name n=test_solar_allocation
make lint               # flake8
make format             # black + isort
make format-check       # CI-style check
make typecheck          # mypy
make docs-serve         # mkdocs at :8000
make pre-commit         # run all hooks
make commit             # commitizen conventional commit
```

## Git workflow

- **Conventional commits**: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`. Use `make commit` for a guided prompt.
- **Feature branches**: `feat/<short-name>`. Open a PR against `main`.
- **Pre-commit** runs black, isort, flake8 and basic file hygiene on every commit. Run `pre-commit run --all-files` before pushing if you reshuffled a lot of files.
- CI runs lint, typecheck, tests, coverage, and a docs build on every PR (`.github/workflows/ci.yml`).

## Testing patterns

- Tests live under `tests/` and use **pytest-asyncio** plus **pytest-homeassistant-custom-component** to spin up a minimal HA core.
- Coordinator tests build a snapshot via the helpers in `tests/test_helpers.py`.
- Decision engine tests are split per concern (`test_decision_engine_power.py`, `test_decision_engine_car.py`, `test_decision_engine_errors.py`, …).
- Add a smoke test in `test_strategies_smoke.py` whenever you add a new strategy.
- Aim for ≥ 80% coverage (enforced by `[tool.coverage.report] fail_under`).

## Docs

- Authored in Markdown under `docs/`, built with `mkdocs-material`.
- Run `make docs-serve` and open <http://localhost:8000>.
- Update `architecture.md` or `decision-engine.md` whenever the pipeline shape changes.
