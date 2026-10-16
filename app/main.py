from fastapi import FastAPI, Request
from datetime import datetime, timezone
import uuid

app = FastAPI(title="Request Inspector")
REQUESTS: list[dict] = []          # تخزين مؤقت في الذاكرة — مقصود في المشروع ده


@app.get("/health")
def health():
    return {"status": "ok"}


@app.api_route("/hook/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def capture(path: str, request: Request):
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
    return {"received": True, "id": item["id"]}


@app.get("/api/requests")
def list_requests(limit: int = 50):
    return REQUESTS[:limit]
