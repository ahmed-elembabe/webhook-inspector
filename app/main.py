from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from collections import OrderedDict
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
import asyncio
import os
import secrets
import signal
import threading
import uuid

PROJECT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_DIR / "static"
MAX_DELAY_SECONDS = 300
RETRY_AFTER_SECONDS = 30
MAX_IDEMPOTENCY_KEYS = 1000
MAX_BODY_BYTES = 1024 * 1024

# Locally the token comes from .env; on Render it's an environment variable set in the dashboard.
load_dotenv(PROJECT_DIR / ".env")
INSPECTOR_TOKEN = os.environ.get("INSPECTOR_TOKEN", "")
if not INSPECTOR_TOKEN:
    raise RuntimeError("Set INSPECTOR_TOKEN (in .env locally, or as an environment variable when deployed)")

REQUESTS: list[dict] = []          # تخزين مؤقت في الذاكرة — مقصود في المشروع ده
# Idempotency-Key -> saved first response, or None while that first request is still running.
IDEMPOTENCY: OrderedDict[str, dict | None] = OrderedDict()
# One queue per open /api/stream connection; every new request is pushed to all of them.
SUBSCRIBERS: set[asyncio.Queue] = set()


def close_streams():
    for queue in SUBSCRIBERS:
        while True:
            try:
                queue.put_nowait(None)  # None tells stream_requests to finish
                break
            except asyncio.QueueFull:
                queue.get_nowait()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On shutdown (Ctrl+C, or SIGTERM from `fastapi dev` reloading) uvicorn waits for every open
    # connection to finish, and an SSE stream never does. So end the streams first, then let the
    # original handler run; EventSource reconnects on its own once the server is back.
    if threading.current_thread() is threading.main_thread():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(sig)

            def handler(signum, frame, previous=previous):
                loop.call_soon_threadsafe(close_streams)
                if callable(previous):
                    previous(signum, frame)
                else:
                    signal.signal(signum, previous)
                    signal.raise_signal(signum)

            signal.signal(sig, handler)
    yield


def require_token(token: str = ""):
    # compare_digest takes the same time whether the guess is close or not, so it can't be timed.
    if not secrets.compare_digest(token.encode(), INSPECTOR_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid token")


app = FastAPI(title="Request Inspector", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500"],  # by name, not "*"
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False, dependencies=[Depends(require_token)])
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cookie/set")
def set_cookie(response: Response):
    response.set_cookie("demo_session", uuid.uuid4().hex, httponly=True, secure=True, samesite="lax")
    return {"cookie": "demo_session", "flags": "HttpOnly; Secure; SameSite=Lax"}


@app.api_route("/hook/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def capture(path: str, request: Request):
    body = await read_body_limited(request)
    if body is None:
        return JSONResponse({"error": f"body is larger than {MAX_BODY_BYTES} bytes"}, status_code=413)

    key = request.headers.get("idempotency-key")
    if key:
        if key in IDEMPOTENCY:
            saved = IDEMPOTENCY[key]
            if saved is None:
                return JSONResponse(
                    {"error": "a request with this Idempotency-Key is still in progress"},
                    status_code=409,
                    headers={"Retry-After": "1"},
                )
            return Response(
                saved["body"],
                status_code=saved["status"],
                headers={**saved["headers"], "Idempotent-Replayed": "true"},
            )
        # Reserve the key before any await, so a retry that arrives mid-delay can't slip through.
        IDEMPOTENCY[key] = None
        while len(IDEMPOTENCY) > MAX_IDEMPOTENCY_KEYS:
            IDEMPOTENCY.popitem(last=False)

    try:
        response = await record_and_respond(path, request, body)
    except BaseException:
        if key:
            IDEMPOTENCY.pop(key, None)  # nothing was answered, so a retry should run for real
        raise

    if key:
        IDEMPOTENCY[key] = {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.body,
        }
    return response


async def read_body_limited(request: Request) -> bytes | None:
    """Return the body, or None if it's over MAX_BODY_BYTES (without reading the rest)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    # Chunked uploads have no Content-Length, so count while reading too.
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


async def record_and_respond(path: str, request: Request, body: bytes) -> Response:
    item = {
        "id": f"req_{uuid.uuid4().hex[:8]}",
        "method": request.method,
        "path": "/" + path,
        "query": {k: request.query_params.getlist(k) for k in request.query_params.keys()},
        "headers": dict(request.headers),
        "body": body.decode("utf-8", errors="replace"),
        "client_ip": request.client.host if request.client else None,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    REQUESTS.insert(0, item)
    del REQUESTS[200:]
    for queue in SUBSCRIBERS:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # that viewer is too far behind; it resyncs from /api/requests on reconnect

    # The request is already stored above, so it shows up on the page while the delay runs.
    try:
        status = int(request.query_params.get("status", 200))
        delay = float(request.query_params.get("delay", 0))
    except ValueError:
        return JSONResponse({"error": "status must be an integer and delay a number"}, status_code=400)
    if not 200 <= status <= 599:
        return JSONResponse({"error": "status must be between 200 and 599"}, status_code=400)
    if not 0 <= delay <= MAX_DELAY_SECONDS:
        return JSONResponse({"error": f"delay must be between 0 and {MAX_DELAY_SECONDS} seconds"}, status_code=400)

    if delay:
        await asyncio.sleep(delay)

    headers = {"Retry-After": str(RETRY_AFTER_SECONDS)} if status in (429, 503) else None
    if status in (204, 304):  # these statuses must not carry a body
        return Response(status_code=status, headers=headers)
    return JSONResponse({"received": True, "id": item["id"], "status": status}, status_code=status, headers=headers)


@app.get("/api/requests", dependencies=[Depends(require_token)])
def list_requests(limit: int = 50):
    return REQUESTS[:limit]


@app.get("/api/stream", response_class=EventSourceResponse, dependencies=[Depends(require_token)])
async def stream_requests() -> AsyncIterable[ServerSentEvent]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    SUBSCRIBERS.add(queue)
    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            yield ServerSentEvent(data=item, event="request", id=item["id"])
    finally:
        SUBSCRIBERS.discard(queue)
