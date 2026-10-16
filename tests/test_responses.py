import time

import pytest


@pytest.mark.parametrize("status", [200, 201, 400, 401, 403, 404, 422, 500])
def test_status_param_sets_the_response_code(client, status):
    response = client.get(f"/hook/x?status={status}")

    assert response.status_code == status
    assert response.json()["status"] == status
    assert "retry-after" not in response.headers


@pytest.mark.parametrize("status", [429, 503])
def test_429_and_503_send_retry_after(client, status):
    response = client.get(f"/hook/x?status={status}")

    assert response.status_code == status
    assert response.headers["retry-after"] == "30"


def test_204_has_no_body(client):
    response = client.get("/hook/x?status=204")

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("query", ["status=abc", "status=999", "status=100", "delay=-1", "delay=301", "delay=soon"])
def test_invalid_values_get_400_and_are_still_recorded(client, stored, query):
    response = client.get(f"/hook/x?{query}")

    assert response.status_code == 400
    assert "error" in response.json()
    assert len(stored()) == 1


def test_delay_waits_before_replying(client):
    started = time.perf_counter()
    response = client.get("/hook/x?delay=0.3")

    assert response.status_code == 200
    assert time.perf_counter() - started >= 0.3
