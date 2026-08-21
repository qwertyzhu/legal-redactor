# Contributing

Thanks for helping improve local-first legal redaction tooling.

## Rules

1. **Fictional data only** in tests, examples, demos, and screenshots.
2. Do not commit `*.ledger.json`, client paths, or live matter files.
3. Keep the agent/CLI split: judgment in docs/skill; deterministic replace/verify in Python.
4. New structural detectors need tests and a clear false-positive note.
5. PDF work must not silently claim support for scanned pages.
6. Keep software versions aligned: `pyproject.toml`, `src/legal_redactor/__init__.py`, `.codex-plugin/plugin.json`.

## Dev setup

```console
python -m pip install -e ".[dev]"
pytest
python scripts/run_demo.py --clean
python scripts/pack_skill.py --output-dir dist
```

## Pull requests

- Small, reviewable diffs
- Update README / CHANGELOG when behavior changes
- CI must pass on Ubuntu, Windows, and macOS across Python 3.10–3.12

## Releasing

1. Bump the three version fields together and add a CHANGELOG section.
2. Push to `main`, then tag:

```console
git tag -a v0.4.0 -m "legal-redactor v0.4.0"
git push origin v0.4.0
```

3. The Release workflow packs `legal-document-redactor.skill`, writes `SHA256SUMS.txt`, and publishes a GitHub Release.
4. Do not rewrite published tags.
