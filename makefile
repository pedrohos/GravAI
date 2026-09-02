run:
	uv run fastapi run src/gravai/api/app.py --host 0.0.0.0 --port 8000

test:
	uv run pytest -s tests

test-realtime:
	uv run pytest -s tests/realtime_test.py

# The front end. A Next.js server that proxies /api to the API over the Docker
# network in the container; in dev it proxies to whatever GRAVAI_API_URL names,
# defaulting to the `run` target above.
frontend-dev:
	GRAVAI_API_URL=$${GRAVAI_API_URL:-http://localhost:8000} npm --prefix src/frontend run dev

frontend-build:
	npm --prefix src/frontend ci && npm --prefix src/frontend run build
