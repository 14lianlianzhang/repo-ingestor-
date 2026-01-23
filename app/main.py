"""FastAPI应用主入口

该模块定义了FastAPI应用实例，初始化数据库，包含了仓库扫描和状态查询的API路由，
以及集成了webhook路由。
"""

import os
from fastapi import FastAPI
from .schemas import IngestRequest  # 导入数据模型
from .tasks import scan_repo_task, celery_app  # 导入Celery任务和应用实例
from celery.result import AsyncResult  # 用于获取Celery任务结果
from .db import init_db  # 导入数据库初始化函数
from .webhook import router as webhook_router  # 导入webhook路由

# 创建FastAPI应用实例
app = FastAPI(
    title="repo-ingestor",  # 应用标题
    version="0.2.0"  # 应用版本
)

# 应用启动时初始化数据库表
try:
    init_db()  # 调用数据库初始化函数，创建所有模型对应的表
    print("Database tables initialized successfully")
except Exception as e:
    # 初始化失败时捕获异常，避免应用启动失败
    print(f"Failed to initialize database tables: {e}")
    pass

# 包含webhook路由
app.include_router(webhook_router)  # 将webhook相关的路由添加到主应用


@app.post("/ingest/scan")
async def ingest_scan(req: IngestRequest):
    """触发仓库扫描任务
    
    接收仓库扫描请求，将任务提交到Celery队列，并返回任务ID和状态。
    
    Args:
        req: 包含仓库URL、分支等信息的请求体
        
    Returns:
        包含任务ID和初始状态的字典
    """
    payload = req.dict()  # 将Pydantic模型转换为字典
    task = scan_repo_task.delay(payload)  # 异步执行Celery任务，传递payload参数
    return {"task_id": task.id, "status": "queued"}  # 返回任务ID和初始状态


@app.get("/ingest/status/{task_id}")
def ingest_status(task_id: str):
    """查询扫描任务状态
    
    根据任务ID查询Celery任务的执行状态和结果。
    
    Args:
        task_id: Celery任务ID
        
    Returns:
        包含任务ID、状态和结果（如果成功）或错误信息（如果失败）的字典
    """
    async_res = AsyncResult(task_id, app=celery_app)  # 获取任务结果对象
    state = async_res.state  # 获取任务当前状态
    
    # 构建响应字典
    res = {"task_id": task_id, "state": state}
    
    if state == "SUCCESS":  # 如果任务成功完成
        res["result"] = async_res.result  # 添加任务执行结果
    elif state == "FAILURE":  # 如果任务失败
        res["error"] = str(async_res.result)  # 添加错误信息
    
    return res
