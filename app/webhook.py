"""Webhook处理模块

该模块负责接收和处理GitHub、GitLab等代码托管平台的webhook事件，
验证事件的合法性，去重，然后触发仓库扫描任务。
"""

import os
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException, Header  # 导入FastAPI相关组件
from redis import Redis  # 导入Redis客户端，用于事件去重
from .tasks import scan_repo_task  # 导入仓库扫描Celery任务
from .db import SessionLocal, Repo as RepoModel, IngestTask  # 导入数据库会话和模型
from typing import Optional  # 导入类型注解
import datetime  # 用于处理日期时间

# 创建FastAPI路由器实例
router = APIRouter()

# Redis连接URL，从环境变量获取，默认使用本地Redis
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

# 创建Redis客户端实例，用于webhook事件去重
redis_client = Redis.from_url(redis_url)

# GitHub webhook密钥，用于验证签名
GITHUB_SECRET = os.getenv("WEBHOOK_SECRET_GITHUB")

# GitLab webhook密钥，用于验证令牌
GITLAB_SECRET = os.getenv("WEBHOOK_SECRET_GITLAB")

# Webhook事件去重的TTL（生存时间），默认86400秒（24小时）
DELIVERY_TTL = int(os.getenv("WEBHOOK_DEDUP_TTL", "86400"))


def verify_github_signature(body: bytes, header_signature: Optional[str]) -> bool:
    """验证GitHub webhook签名
    
    使用HMAC算法验证GitHub webhook请求的真实性，支持SHA1和SHA256两种算法。
    
    Args:
        body: 请求体的原始字节数据
        header_signature: 请求头中的签名，格式为"algorithm=signature"
        
    Returns:
        bool: 签名验证是否成功
    """
    if not header_signature:  # 如果没有签名头，验证失败
        return False
    
    try:
        alg, signature = header_signature.split("=", 1)  # 分割算法和签名
    except Exception:
        return False  # 格式错误，验证失败
    
    secret = GITHUB_SECRET  # 获取GitHub密钥
    if not secret:  # 如果没有配置密钥，验证失败
        return False
    
    # 根据算法生成摘要
    if alg == "sha256":
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    elif alg == "sha1":
        digest = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    else:
        return False  # 不支持的算法，验证失败
    
    # 使用安全的比较方法验证摘要和签名
    return hmac.compare_digest(digest, signature)


def verify_gitlab_token(header_token: Optional[str]) -> bool:
    """验证GitLab webhook令牌
    
    验证GitLab webhook请求的令牌是否匹配配置的密钥。
    
    Args:
        header_token: 请求头中的GitLab令牌
        
    Returns:
        bool: 令牌验证是否成功
    """
    if not header_token:  # 如果没有令牌头，验证失败
        return False
    
    if not GITLAB_SECRET:  # 如果没有配置密钥，验证失败
        return False
    
    # 使用安全的比较方法验证令牌
    return hmac.compare_digest(header_token, GITLAB_SECRET)


@router.post("/ingest/webhook")
async def handle_webhook(request: Request,
                         x_hub_signature: Optional[str] = Header(None),
                         x_hub_signature_256: Optional[str] = Header(None),
                         x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
                         x_github_delivery: Optional[str] = Header(None, alias="X-GitHub-Delivery"),
                         x_gitlab_event: Optional[str] = Header(None, alias="X-Gitlab-Event"),
                         x_gitlab_token: Optional[str] = Header(None, alias="X-Gitlab-Token")):
    """处理webhook事件
    
    接收来自GitHub或GitLab的webhook事件，验证其合法性，去重，然后触发仓库扫描任务。
    
    Args:
        request: FastAPI请求对象
        x_hub_signature: GitHub的SHA1签名头
        x_hub_signature_256: GitHub的SHA256签名头
        x_github_event: GitHub事件类型头
        x_github_delivery: GitHub传递ID头
        x_gitlab_event: GitLab事件类型头
        x_gitlab_token: GitLab令牌头
        
    Returns:
        包含处理结果的字典
    """
    # 读取请求体
    body = await request.body()
    
    # 限制请求体大小，防止过大的请求
    if len(body) > (1024 * 1024 * 5):  # 5MB限制
        raise HTTPException(status_code=413, detail="Payload too large")
    
    verified = False  # 验证状态
    provider = None  # 平台提供商（github/gitlab）
    delivery_id = None  # 传递ID，用于去重
    
    # 处理GitHub webhook
    if x_github_event is not None:
        provider = "github"
        # 优先使用SHA256签名，其次使用SHA1签名
        header_sig = x_hub_signature_256 or x_hub_signature
        
        if not header_sig:  # 缺少签名头
            raise HTTPException(status_code=401, detail="Missing GitHub signature")
            
        if not verify_github_signature(body, header_sig):  # 签名验证失败
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")
            
        verified = True  # 验证成功
        delivery_id = x_github_delivery  # 获取传递ID
    
    # 处理GitLab webhook
    elif x_gitlab_event is not None:
        provider = "gitlab"
        
        if not x_gitlab_token or not verify_gitlab_token(x_gitlab_token):  # 令牌验证失败
            raise HTTPException(status_code=401, detail="Invalid GitLab token")
            
        verified = True  # 验证成功
        payload_json = await request.json()  # 解析请求体获取传递ID
        # 尝试从不同字段获取传递ID
        delivery_id = payload_json.get("object_attributes", {}).get("id") or payload_json.get("after") or None
    
    # 不支持的平台
    else:
        raise HTTPException(status_code=400, detail="Unsupported webhook provider or missing event header")
    
    # 事件去重处理
    if delivery_id:
        # 生成去重键，格式为"webhook:delivery:provider:delivery_id"
        key = f"webhook:delivery:{provider}:{delivery_id}"
        # 使用setnx命令实现原子性检查和设置
        added = redis_client.setnx(key, "1")
        
        if not added:  # 如果键已存在，说明是重复事件
            return {"status": "duplicate", "delivery_id": delivery_id}
        
        # 设置键的过期时间，防止内存泄漏
        redis_client.expire(key, DELIVERY_TTL)
    
    # 解析请求体为JSON格式
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # 提取仓库信息
    repo_info = payload.get("repository") or payload.get("project") or {}  # GitHub使用repository，GitLab使用project
    repo_clone = repo_info.get("clone_url") or repo_info.get("git_http_url") or repo_info.get("http_url")  # 仓库克隆URL
    # 仓库全名，格式为owner/name
    repo_full_name = repo_info.get("full_name") or f"{repo_info.get('owner', {}).get('login','')}/{repo_info.get('name','')}"
    
    # 提取分支信息
    ref = payload.get("ref")  # 引用，格式为refs/heads/branch_name
    branch = None
    if ref and isinstance(ref, str) and ref.startswith("refs/heads/"):
        branch = ref.split("/", 2)[-1]  # 提取分支名
    
    # 检查仓库是否在允许列表中
    allowed = os.getenv("WEBHOOK_ALLOWLIST")
    if allowed:
        # 解析允许列表，格式为逗号分隔的仓库全名
        allowed_set = {s.strip() for s in allowed.split(",") if s.strip()}
        if repo_full_name not in allowed_set:  # 不在允许列表中
            raise HTTPException(status_code=403, detail="Repository not allowed")
    
    # 只处理GitHub的push事件
    event = x_github_event or x_gitlab_event
    if provider == "github" and event != "push":
        return {"status": "ignored", "reason": f"event {event} not handled"}
    
    # 在数据库中创建ingest任务记录
    db = SessionLocal()
    try:
        # 确保仓库记录存在
        repo_row = db.query(RepoModel).filter(RepoModel.remote_url == repo_clone).first()
        if not repo_row:
            # 创建新仓库记录
            repo_row = RepoModel(remote_url=repo_clone, full_name=repo_full_name)
            db.add(repo_row)
            db.commit()
            db.refresh(repo_row)  # 刷新获取新生成的ID
        
        # 创建ingest任务记录
        t = IngestTask(
            external_task_id=None,  # 后续更新为Celery任务ID
            repo_id=repo_row.id,  # 关联的仓库ID
            status='queued',  # 初始状态为排队
            payload=payload,  # 完整的webhook payload
            created_at=datetime.datetime.now(datetime.UTC)  # 创建时间
        )
        db.add(t)
        db.commit()
        db.refresh(t)  # 刷新获取新生成的ID
    finally:
        db.close()  # 确保数据库连接关闭
    
    # 触发仓库扫描Celery任务
    task = scan_repo_task.delay({"repo_url": repo_clone, "branch": branch, "depth": 1})
    
    # 更新数据库中的ingest任务记录，添加Celery任务ID
    db = SessionLocal()
    try:
        dbt = db.query(IngestTask).filter(IngestTask.id == t.id).first()
        if dbt:
            dbt.external_task_id = task.id  # 更新为Celery任务ID
            db.commit()
    finally:
        db.close()  # 确保数据库连接关闭
    
    # 返回处理结果
    return {"status": "queued", "task_id": task.id, "delivery_id": delivery_id}