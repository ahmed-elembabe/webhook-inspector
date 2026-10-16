from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import uuid

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_DELAY_SECONDS = 300
RETRY_AFTER_SECONDS = 30
MAX_IDEMPOTENCY_KEYS = 1000

app = FastAPI(title="Request Inspector")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500"],  # by name, not "*"
    allow_methods=["*"],
    allow_headers=["*"],
)
REQUESTS: list[dict] = []          # تخزين مؤقت في الذاكرة — مقصود في المشروع ده
# Idempotency-Key -> saved first response, or None while that first request is still running.
IDEMPOTENCY: OrderedDict[str, dict | None] = OrderedDict()


@app.get("/", include_in_schema=False)
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
        response = await record_and_respond(path, request)
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


async def record_and_respond(path: str, request: Request) -> Response:
    body = await request.body()
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


@app.get("/api/requests")
def list_requests(limit: int = 50):
    return REQUESTS[:limit]
