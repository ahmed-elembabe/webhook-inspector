import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import main


@pytest.mark.parametrize("path", ["/", "/api/requests", "/api/stream"])
@pytest.mark.parametrize("params", [{}, {"token": "wrong"}, {"token": "é"}], ids=["missing", "wrong", "non-ascii"])
def test_dashboard_and_api_need_the_token(client, path, params):
    response = client.get(path, params=params)

    assert response.status_code == 401


def test_right_token_opens_the_dashboard_and_api(client, auth):
    page = client.get("/", params=auth)
    api = client.get("/api/requests", params=auth)

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert api.status_code == 200


def test_hook_and_health_need_no_token(client):
    assert client.get("/hook/anything").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_body_of_exactly_1mb_is_accepted(client, stored):
    response = client.post("/hook/size", content=b"x" * main.MAX_BODY_BYTES)

    assert response.status_code == 200
    assert len(stored()) == 1


def test_body_over_1mb_gets_413_and_is_not_recorded(client, stored):
    response = client.post("/hook/size", content=b"x" * (main.MAX_BODY_BYTES + 1))

    assert response.status_code == 413
    assert stored() == []


def test_chunked_body_over_1mb_gets_413(client, stored):
    # A generator body is sent without Content-Length, so the limit must be enforced while reading.
    chunks = (b"x" * 600_000 for _ in range(2))

    response = client.post("/hook/size", content=chunks)

    assert response.status_code == 413
    assert stored() == []


def test_app_refuses_to_start_without_a_token():
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "INSPECTOR_TOKEN": ""},  # set but empty, so .env can't fill it in either
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Set INSPECTOR_TOKEN" in result.stderr


def test_cors_preflight_allows_only_the_named_origin(client):
    def preflight(origin):
        return client.options(
            "/hook/x",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
        )

    allowed = preflight("http://localhost:5500")
    other = preflight("http://evil.example")

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5500"
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers


def test_demo_cookie_flags(client):
    set_cookie = client.get("/cookie/set").headers["set-cookie"].lower()

    assert set_cookie.startswith("demo_session=")
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
