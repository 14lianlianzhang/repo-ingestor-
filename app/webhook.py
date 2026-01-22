import os
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException, Header
from redis import Redis
from .tasks import scan_repo_task
from .db import SessionLocal, Repo as RepoModel, IngestTask
from typing import Optional
import datetime

router = APIRouter()
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = Redis.from_url(redis_url)

GITHUB_SECRET = os.getenv("WEBHOOK_SECRET_GITHUB")
GITLAB_SECRET = os.getenv("WEBHOOK_SECRET_GITLAB")
DELIVERY_TTL = int(os.getenv("WEBHOOK_DEDUP_TTL", "86400"))

def verify_github_signature(body: bytes, header_signature: Optional[str]) -> bool:
    if not header_signature:
        return False
    try:
        alg, signature = header_signature.split("=", 1)
    except Exception:
        return False
    secret = GITHUB_SECRET
    if not secret:
        return False
    if alg == "sha256":
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    elif alg == "sha1":
        digest = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    else:
        return False
    return hmac.compare_digest(digest, signature)

def verify_gitlab_token(header_token: Optional[str]) -> bool:
    if not header_token:
        return False
    if not GITLAB_SECRET:
        return False
    return hmac.compare_digest(header_token, GITLAB_SECRET)

@router.post("/ingest/webhook")
async def handle_webhook(request: Request,
                         x_hub_signature: Optional[str] = Header(None),
                         x_hub_signature_256: Optional[str] = Header(None),
                         x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
                         x_github_delivery: Optional[str] = Header(None, alias="X-GitHub-Delivery"),
                         x_gitlab_event: Optional[str] = Header(None, alias="X-Gitlab-Event"),
                         x_gitlab_token: Optional[str] = Header(None, alias="X-Gitlab-Token")):

    body = await request.body()
    if len(body) > (1024 * 1024 * 5):
        raise HTTPException(status_code=413, detail="Payload too large")

    verified = False
    provider = None
    delivery_id = None

    if x_github_event is not None:
        provider = "github"
        header_sig = x_hub_signature_256 or x_hub_signature
        if not header_sig:
            raise HTTPException(status_code=401, detail="Missing GitHub signature")
        if not verify_github_signature(body, header_sig):
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")
        verified = True
        delivery_id = x_github_delivery
    elif x_gitlab_event is not None:
        provider = "gitlab"
        if not x_gitlab_token or not verify_gitlab_token(x_gitlab_token):
            raise HTTPException(status_code=401, detail="Invalid GitLab token")
        verified = True
        payload_json = await request.json()
        delivery_id = payload_json.get("object_attributes", {}).get("id") or payload_json.get("after") or None
    else:
        raise HTTPException(status_code=400, detail="Unsupported webhook provider or missing event header")

    if delivery_id:
        key = f"webhook:delivery:{provider}:{delivery_id}"
        added = redis_client.setnx(key, "1")
        if not added:
            return {"status": "duplicate", "delivery_id": delivery_id}
        redis_client.expire(key, DELIVERY_TTL)

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    repo_info = payload.get("repository") or payload.get("project") or {}
    repo_clone = repo_info.get("clone_url") or repo_info.get("git_http_url") or repo_info.get("http_url")
    repo_full_name = repo_info.get("full_name") or f"{repo_info.get('owner', {}).get('login','')}/{repo_info.get('name','')}"
    ref = payload.get("ref")
    branch = None
    if ref and isinstance(ref, str) and ref.startswith("refs/heads/"):
        branch = ref.split("/", 2)[-1]

    allowed = os.getenv("WEBHOOK_ALLOWLIST")
    if allowed:
        allowed_set = {s.strip() for s in allowed.split(",") if s.strip()}
        if repo_full_name not in allowed_set:
            raise HTTPException(status_code=403, detail="Repository not allowed")

    event = x_github_event or x_gitlab_event
    if provider == "github" and event != "push":
        return {"status": "ignored", "reason": f"event {event} not handled"}

    # create DB ingest task record
    db = SessionLocal()
    try:
        # ensure repo exists
        repo_row = db.query(RepoModel).filter(RepoModel.remote_url == repo_clone).first()
        if not repo_row:
            repo_row = RepoModel(remote_url=repo_clone, full_name=repo_full_name)
            db.add(repo_row)
            db.commit()
            db.refresh(repo_row)
        t = IngestTask(external_task_id=None, repo_id=repo_row.id, status='queued', payload=payload, created_at=datetime.datetime.utcnow())
        db.add(t)
        db.commit()
        db.refresh(t)
    finally:
        db.close()

    task = scan_repo_task.delay({"repo_url": repo_clone, "branch": branch, "depth": 1})
    # update external_task_id
    db = SessionLocal()
    try:
        dbt = db.query(IngestTask).filter(IngestTask.id == t.id).first()
        if dbt:
            dbt.external_task_id = task.id
            db.commit()
    finally:
        db.close()

    return {"status": "queued", "task_id": task.id, "delivery_id": delivery_id}