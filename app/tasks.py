import os
from celery import Celery
from celery.result import AsyncResult
from .ingestor import clone_and_scan

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

celery_app = Celery("repo_ingestor", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

@celery_app.task(bind=True)
def scan_repo_task(self, payload):
    repo_url = payload.get("repo_url")
    branch = payload.get("branch")
    depth = payload.get("depth", 1)
    result = clone_and_scan(repo_url, branch=branch, depth=depth)
    return result
