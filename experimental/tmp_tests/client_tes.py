import pytest
from fastapi.testclient import TestClient
from experimental.tmp_tests.client2 import Client
from api.app import app
import json
import time

# api_client = TestClient(app)

def test_client_conn():
    with TestClient(app) as api_client: # Trigger lifespan
        res = api_client.post("/transcribe")
        data = json.loads(res.json())

        assert data["ready"], "Server not ready"
        client = Client(ws_url=data["ws_url"])
        client.interact()
    