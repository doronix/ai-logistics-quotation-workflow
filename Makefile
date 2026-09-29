.PHONY: run test

run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port $${APP_PORT:-18000}

test:
	uv run pytest
