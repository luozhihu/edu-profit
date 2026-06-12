.PHONY: doctor bootstrap dev test test-unit test-integration test-all lint

doctor:
	@command -v uv >/dev/null || (echo "ERROR: uv is required. Install: https://docs.astral.sh/uv/" && exit 1)
	@uv python find 3.11 >/dev/null || (echo "ERROR: Python 3.11 is required. Run: uv python install 3.11" && exit 1)
	@echo "OK: required local tools are available"

bootstrap: doctor
	@mkdir -p data uploads
	uv sync
	uv run python -m app.bootstrap

dev:
	.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

test: test-all

test-unit:
	.venv/bin/pytest tests/unit -q

test-integration:
	.venv/bin/pytest tests/integration -q

test-all:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .
