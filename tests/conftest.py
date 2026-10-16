import os

import pytest

# The app refuses to start without a token, so set one before it is imported.
# load_dotenv() never overrides a variable that is already set, so .env is ignored here.
TOKEN = "test-token"
os.environ["INSPECTOR_TOKEN"] = TOKEN

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Each test starts with no stored requests, idempotency keys or open streams."""
    main.REQUESTS.clear()
    main.IDEMPOTENCY.clear()
    main.SUBSCRIBERS.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def auth():
    return {"token": TOKEN}


@pytest.fixture
def stored(client, auth):
    """Returns a function that fetches everything the inspector has recorded, newest first."""
    return lambda: client.get("/api/requests", params={**auth, "limit": 1000}).json()
