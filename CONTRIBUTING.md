# Contributing

## Development setup
1. Create venv and install dependencies:
   - `python -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
2. Start app:
   - `PORT=8000 ./scripts/restart_server.sh`

## Quality gates
- Lint/format:
  - `python -m ruff check app tests scripts`
  - `python -m ruff format app tests scripts`
- Test:
  - `python -m pytest -q tests/test_v2_pipeline.py`

## Commit guidance
- Keep commits focused and small.
- Include tests for behavior changes.
- Do not commit generated data files, local caches, or secrets.

## Project constraints (must keep)
- Region scope: Hefei only.
- Model scope: QingTian large model only.
- Human score profile: 16-dim attention (0~10).
- Construction plan total score: 100.
- Calibration gate: MAE must improve and ranking correlation must not decrease.
