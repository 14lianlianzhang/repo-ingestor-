from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, Any

class IngestRequest(BaseModel):
    repo_url: HttpUrl
    branch: Optional[str] = None
    commit: Optional[str] = None
    depth: Optional[int] = 1

class WebhookRequest(BaseModel):
    payload: Dict[str, Any]
