import asyncio

import httpx

from app import main


def post(client, key, **kwargs):
    return client.post("/hook/payments?status=201", headers={"Idempotency-Key": key}, json={"amount": 150}, **kwargs)


def test_same_key_replays_the_first_response_and_records_once(client, stored):
    first, second, third = (post(client, "pay_123") for _ in range(3))

    assert [r.status_code for r in (first, second, third)] == [201, 201, 201]
    assert first.json()["id"] == second.json()["id"] == third.json()["id"]
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert third.headers["idempotent-replayed"] == "true"
    assert len(stored()) == 1


def test_different_keys_are_separate_requests(client, stored):
    a = post(client, "pay_a")
    b = post(client, "pay_b")

    assert a.json()["id"] != b.json()["id"]
    assert len(stored()) == 2


def test_retry_that_arrives_while_the_first_is_still_running_gets_409(stored):
    # The real race: the sender times out and retries while our first answer is still being prepared.
    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Idempotency-Key": "pay_race"}
            first = asyncio.create_task(ac.post("/hook/payments?delay=0.3", headers=headers))
            await asyncio.sleep(0.05)  # the first request is now inside its delay
            second = await ac.post("/hook/payments?delay=0.3", headers=headers)
            return await first, second

    first, second = asyncio.run(scenario())

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(stored()) == 1


def test_rejected_oversized_request_does_not_burn_the_key(client):
    too_big = client.post("/hook/x", headers={"Idempotency-Key": "pay_big"}, content=b"x" * (main.MAX_BODY_BYTES + 1))
    retry = post(client, "pay_big")

    assert too_big.status_code == 413
    assert retry.status_code == 201
    assert "idempotent-replayed" not in retry.headers
