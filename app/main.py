import os
from fastapi import FastAPI
from .schemas import IngestRequest
from .tasks import scan_repo_task, celery_app
from celery.result import AsyncResult
from .db import init_db
from .webhook import router as webhook_router

app = FastAPI(title="repo-ingestor", version="0.2.0")

# init DB tables on startup
try:
    init_db()
except Exception:
    pass

app.include_router(webhook_router)

@app.post("/ingest/scan")
async def ingest_scan(req: IngestRequest):
    payload = req.dict()
    task = scan_repo_task.delay(payload)
    return {"task_id": task.id, "status": "queued"}

@app.get("/ingest/status/{task_id}")
def ingest_status(task_id: str):
    async_res = AsyncResult(task_id, app=celery_app)
    state = async_res.state
    res = {"task_id": task_id, "state": state}
    if state == "SUCCESS":
        res["result"] = async_res.result
    elif state == "FAILURE":
        res["error"] = str(async_res.result)
    return res
