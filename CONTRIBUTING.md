# Contributing

Thank you for your interest in improving `a6700_rawsmith`.

## Setup

```bash
git clone https://github.com/Test2049micro/a6700-raw-developer.git
cd a6700-raw-developer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Run tests

```bash
pytest -q
```

## Code style

- Follow existing patterns in `a6700_rawsmith.py` and `lut.py`.
- Keep functions small and typed.
- Add tests for new behavior in `tests/test_a6700_rawsmith.py`.

## Commit messages

Use Conventional Commits, e.g. `feat:`, `fix:`, `test:`, `docs:`.
