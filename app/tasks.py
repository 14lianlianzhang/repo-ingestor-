"""Celery任务模块

该模块定义了Celery应用实例和仓库扫描任务，负责异步执行仓库克隆、文件扫描和数据持久化操作。
"""

import os
from celery import Celery  # 导入Celery类，用于创建任务队列应用
from celery.result import AsyncResult  # 用于获取任务结果
from .ingestor import clone_and_scan  # 导入仓库克隆和扫描的核心函数
from .db import SessionLocal, IngestTask, init_db  # 导入数据库会话、任务模型和初始化函数
import datetime  # 用于处理日期时间

# Celery代理URL，用于消息队列，默认使用Redis
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")

# Celery结果后端URL，用于存储任务结果，默认使用Redis
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

# 创建Celery应用实例
celery_app = Celery(
    "repo_ingestor",  # 应用名称
    broker=CELERY_BROKER_URL,  # 消息代理URL
    backend=CELERY_RESULT_BACKEND  # 结果后端URL
)

# 确保数据库表存在
try:
    init_db()  # 调用数据库初始化函数
    print("Database tables initialized in Celery worker")
except Exception as e:
    # 初始化失败时捕获异常，避免Celery worker启动失败
    print(f"Failed to initialize database tables in Celery worker: {e}")
    pass


@celery_app.task(bind=True)
def scan_repo_task(self, payload):
    """仓库扫描Celery任务
    
    异步执行仓库克隆、文件扫描和数据持久化操作，支持增量扫描。
    
    Args:
        self: 任务实例自身，用于访问任务上下文
        payload: 任务参数，包含仓库URL、分支等信息
        
    Returns:
        扫描结果字典，包含本地仓库路径、提交SHA、文件数量等
    """
    # 从payload中提取参数
    repo_url = payload.get("repo_url")  # 仓库URL
    branch = payload.get("branch")  # 要扫描的分支
    depth = payload.get("depth", 1)  # 克隆深度，默认为1
    prev_commit = None  # 上次扫描的提交SHA，用于增量扫描
    
    # 检查仓库是否曾经被处理过，获取上次的提交SHA
    db = SessionLocal()
    try:
        # 查找仓库ID
        repo = db.execute("SELECT id FROM repos WHERE remote_url = :url", {'url': repo_url}).fetchone()
        if repo:
            # 查找该仓库的最新提交SHA
            row = db.execute("SELECT commit_sha FROM commits WHERE repo_id = :repo_id ORDER BY created_at DESC LIMIT 1", 
                            {'repo_id': repo[0]}).fetchone()
            if row:
                prev_commit = row[0]  # 设置上次提交SHA，用于增量扫描
    finally:
        db.close()  # 确保数据库连接关闭
    
    # 记录任务开始信息到数据库
    task_db = SessionLocal()
    try:
        t = IngestTask(
            external_task_id=self.request.id,  # Celery任务ID
            repo_id=None,  # 仓库ID后续更新
            status='started',  # 任务状态为开始
            payload=payload,  # 任务参数
            started_at=datetime.datetime.now(datetime.UTC)  # 开始时间
        )
        task_db.add(t)  # 添加任务记录
        task_db.commit()  # 提交事务
        task_db.refresh(t)  # 刷新获取新生成的ID
    finally:
        task_db.close()  # 确保数据库连接关闭
    
    # 执行仓库克隆和扫描操作
    try:
        # 调用核心函数执行克隆和扫描
        result = clone_and_scan(repo_url, branch=branch, depth=depth, prev_commit=prev_commit)
        
        # 更新任务结果到数据库
        task_db = SessionLocal()
        try:
            # 查找任务记录
            dbtask = task_db.query(IngestTask).filter(
                IngestTask.external_task_id == self.request.id
            ).first()
            if dbtask:
                dbtask.result = result  # 设置任务结果
                dbtask.status = 'success'  # 更新状态为成功
                dbtask.finished_at = datetime.datetime.now(datetime.UTC)  # 设置结束时间
                task_db.commit()  # 提交事务
        finally:
            task_db.close()  # 确保数据库连接关闭
        
        return result  # 返回任务结果
    except Exception as e:
        # 处理任务执行过程中的异常
        task_db = SessionLocal()
        try:
            # 查找任务记录
            dbtask = task_db.query(IngestTask).filter(
                IngestTask.external_task_id == self.request.id
            ).first()
            if dbtask:
                dbtask.result = {'error': str(e)}  # 设置错误信息
                dbtask.status = 'failure'  # 更新状态为失败
                dbtask.finished_at = datetime.datetime.now(datetime.UTC)  # 设置结束时间
                task_db.commit()  # 提交事务
        finally:
            task_db.close()  # 确保数据库连接关闭
        
        raise  # 重新抛出异常，让Celery记录失败状态