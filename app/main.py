import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .schemas import IngestRequest, WebhookRequest
from .tasks import scan_repo_task, celery_app
from celery.result import AsyncResult

app = FastAPI(title="repo-ingestor", version="0.1.0")

@app.post("/ingest/scan")
async def ingest_scan(req: IngestRequest):
    payload = req.dict()
    task = scan_repo_task.delay(payload)
    return {"task_id": task.id, "status": "queued"}

@app.post("/ingest/webhook")
async def ingest_webhook(req: WebhookRequest):
    payload = req.payload
    repo_url = payload.get("repository", {}).get("clone_url") or payload.get("repository", {}).get("html_url")
    branch = None
    ref = payload.get("ref")
    if ref and isinstance(ref, str) and ref.startswith("refs/heads/"):
        branch = ref.split("/", 2)[-1]
    if not repo_url:
        raise HTTPException(status_code=400, detail="repository url not found in webhook payload")
    task = scan_repo_task.delay({"repo_url": repo_url, "branch": branch})
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
