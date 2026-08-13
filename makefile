run:
	uv run fastapi run api/app.py --host 0.0.0.0 --port 8000

test:
	uv run pytest -s tests