# repo-ingestor

Minimal repo ingestor service:
- FastAPI web API
- Celery worker (Redis broker) to run cloning & scanning tasks
- Uses GitPython to clone repositories and compute file-level metadata

Endpoints:
- POST /ingest/scan  -> 启动一次 repo scan（返回 task_id）
- POST /ingest/webhook -> 接收 webhook 并异步触发处理
- GET /ingest/status/{task_id} -> 查询任务状态与结果

Run (dev):
- docker-compose up --build
- Web API: http://localhost:8000/docs

Environment variables:
- CELERY_BROKER_URL (default redis://redis:6379/0)
- CELERY_RESULT_BACKEND (default redis://redis:6379/0)
- CLONE_BASE_DIR (where to clone repos, default /tmp/repos)

License: MIT
