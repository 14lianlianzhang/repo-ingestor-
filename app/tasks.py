import os
from celery import Celery
from celery.result import AsyncResult
from .ingestor import clone_and_scan
from .db import SessionLocal, IngestTask, init_db
import datetime

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

celery_app = Celery("repo_ingestor", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# ensure db tables exist
try:
    init_db()
except Exception:
    pass

@celery_app.task(bind=True)
def scan_repo_task(self, payload):
    repo_url = payload.get("repo_url")
    branch = payload.get("branch")
    depth = payload.get("depth", 1)
    prev_commit = None
    # if repo was processed before, find last commit in DB
    db = SessionLocal()
    try:
        repo = db.execute("SELECT id FROM repos WHERE remote_url = :url", {'url': repo_url}).fetchone()
        if repo:
            row = db.execute("SELECT commit_sha FROM commits WHERE repo_id = :repo_id ORDER BY created_at DESC LIMIT 1", {'repo_id': repo[0]}).fetchone()
            if row:
                prev_commit = row[0]
    finally:
        db.close()

    task_db = SessionLocal()
    try:
        t = IngestTask(external_task_id=self.request.id, repo_id=None, status='started', payload=payload, started_at=datetime.datetime.utcnow())
        task_db.add(t)
        task_db.commit()
        task_db.refresh(t)
    finally:
        task_db.close()

    try:
        result = clone_and_scan(repo_url, branch=branch, depth=depth, prev_commit=prev_commit)
        # update task result
        task_db = SessionLocal()
        try:
            dbtask = task_db.query(IngestTask).filter(IngestTask.external_task_id == self.request.id).first()
            if dbtask:
                dbtask.result = result
                dbtask.status = 'success'
                dbtask.finished_at = datetime.datetime.utcnow()
                task_db.commit()
        finally:
            task_db.close()
        return result
    except Exception as e:
        task_db = SessionLocal()
        try:
            dbtask = task_db.query(IngestTask).filter(IngestTask.external_task_id == self.request.id).first()
            if dbtask:
                dbtask.result = {'error': str(e)}
                dbtask.status = 'failure'
                dbtask.finished_at = datetime.datetime.utcnow()
                task_db.commit()
        finally:
            task_db.close()
        raise