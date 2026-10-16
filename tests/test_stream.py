import asyncio

import pytest

from app import main


def test_new_request_is_pushed_to_every_open_stream(client):
    viewers = [asyncio.Queue(), asyncio.Queue()]
    main.SUBSCRIBERS.update(viewers)

    client.get("/hook/live")

    for queue in viewers:
        assert queue.get_nowait()["path"] == "/live"


def test_stream_sends_request_events_and_ends_on_shutdown():
    async def scenario():
        stream = main.stream_requests()
        next_event = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)  # let the stream subscribe
        [queue] = main.SUBSCRIBERS

        queue.put_nowait({"id": "req_1", "path": "/live"})
        event = await asyncio.wait_for(next_event, timeout=1)
        assert event.event == "request"
        assert event.id == "req_1"

        # What the SIGINT/SIGTERM handler does; without it uvicorn waits forever on shutdown.
        main.close_streams()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), timeout=1)
        assert main.SUBSCRIBERS == set()

    asyncio.run(scenario())
