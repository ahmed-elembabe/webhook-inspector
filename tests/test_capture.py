from datetime import datetime, timedelta

import pytest


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_any_method_on_any_path_is_recorded(client, stored, method):
    response = client.request(method, "/hook/a/b/c")

    assert response.status_code == 200
    [item] = stored()
    assert item["method"] == method
    assert item["path"] == "/a/b/c"


def test_records_query_headers_body_and_ip(client, stored):
    client.post(
        "/hook/orders?tag=a&tag=b&page=1",
        headers={"X-Test": "hello", "Content-Type": "application/json"},
        content='{"amount": 150}',
    )

    [item] = stored()
    assert item["query"] == {"tag": ["a", "b"], "page": ["1"]}  # repeated keys keep every value
    assert item["headers"]["x-test"] == "hello"
    assert item["body"] == '{"amount": 150}'  # raw, exactly as sent
    assert item["client_ip"] == "testclient"


def test_binary_body_does_not_crash(client, stored):
    response = client.post("/hook/bin", content=b"\xff\xfe\x00")

    assert response.status_code == 200
    assert "�" in stored()[0]["body"]


def test_received_at_is_stored_in_utc(client, stored):
    client.get("/hook/time")

    received_at = datetime.fromisoformat(stored()[0]["received_at"])
    assert received_at.utcoffset() == timedelta(0)


def test_keeps_only_the_latest_200_newest_first(client, stored):
    for i in range(205):
        client.get(f"/hook/{i}")

    items = stored()
    assert len(items) == 200
    assert items[0]["path"] == "/204"
    assert items[-1]["path"] == "/5"


def test_limit_param(client, auth):
    for i in range(5):
        client.get(f"/hook/{i}")

    assert len(client.get("/api/requests", params={**auth, "limit": 3}).json()) == 3
