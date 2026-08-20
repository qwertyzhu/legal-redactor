# Contributing

Thanks for helping improve local-first legal redaction tooling.

## Rules

1. **Fictional data only** in tests, examples, demos, and screenshots.
2. Do not commit `*.ledger.json`, client paths, or live matter files.
3. Keep the agent/CLI split: judgment in docs/skill; deterministic replace/verify in Python.
4. New structural detectors need tests and a clear false-positive note.
5. PDF work must not silently claim support for scanned pages.

## Dev setup

```console
python -m pip install -e ".[dev]"
pytest
python scripts/run_demo.py --clean
```

## Pull requests

- Small, reviewable diffs
- Update README / CHANGELOG when behavior changes
- CI must pass on Ubuntu and Windows
