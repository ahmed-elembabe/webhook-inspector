from fastapi import FastAPI

app = FastAPI(title="Request Inspector")


@app.get("/health")
def health():
    return {"status": "ok"}
