run:
	uv run fastapi run src/gravai/api/app.py --host 0.0.0.0 --port 8000

test:
	uv run pytest -s tests

test-realtime:
	uv run pytest -s tests/realtime_test.py