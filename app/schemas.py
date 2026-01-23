"""数据模型定义模块

该模块使用Pydantic定义了API请求和响应的数据模型，用于数据验证和序列化。
"""

from pydantic import BaseModel, HttpUrl  # 导入Pydantic基础类和HTTP URL验证器
from typing import Optional, Dict, Any  # 导入类型注解


class IngestRequest(BaseModel):
    """仓库扫描请求模型
    
    定义了触发仓库扫描时需要的参数结构，用于验证和解析API请求。
    """
    repo_url: HttpUrl  # 仓库URL，必须是有效的HTTP/HTTPS URL
    branch: Optional[str] = None  # 要扫描的分支，可选，默认为None（使用仓库默认分支）
    commit: Optional[str] = None  # 特定的提交SHA，可选，默认为None（使用最新提交）
    depth: Optional[int] = 1  # 克隆深度，可选，默认为1（只克隆最新提交）


class WebhookRequest(BaseModel):
    """Webhook请求模型
    
    定义了接收外部webhook通知时的请求结构。
    """
    payload: Dict[str, Any]  # Webhook负载，包含外部系统发送的事件数据，格式为字典
