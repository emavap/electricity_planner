# Contributing

## CI Checklist — before every commit

1. **Run tests** — `pytest --timeout=60 -o addopts='-ra'` (all must pass)
2. **Format code** — `black custom_components/ tests/` then `isort custom_components/ tests/`
3. **Lint** — `flake8 custom_components/ tests/` (no warnings)
4. **Version sync** — bump `const.py` `INTEGRATION_VERSION`, then sync:
   - `README.md` line 3
   - `info.md` line 3
   - `manifest.json` `"version"` field
5. **Check for stray artifacts** — `git status` should NOT show:
   - `site/` (mkdocs build output)
   - `.openclaw/` (workspace state)
   - `htmlcov/` (coverage report)
   - `AGENTS.md`, `TOOLS.md` (workspace files)

## Release process

1. Complete all changes and run the CI checklist above
2. Commit with message format `vX.Y.Z: <description>`
3. Tag: `git tag vX.Y.Z`
4. Push: `git push origin main --tags`
5. Create a GitHub Release from the tag with changelog
