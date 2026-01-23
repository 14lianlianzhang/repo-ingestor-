"""数据库模型定义模块

该模块负责定义所有数据库模型、连接配置和初始化函数，
使用SQLAlchemy ORM进行数据库操作，支持PostgreSQL数据库。
"""

import os
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, JSON, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.dialects.postgresql import UUID
import datetime
import uuid

# 数据库连接URL，从环境变量获取，默认使用本地PostgreSQL配置
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@postgres:5432/repo_ingestor')

# SQLAlchemy数据库引擎，用于管理数据库连接池
engine = create_engine(DATABASE_URL, pool_pre_ping=True)  # pool_pre_ping=True用于检查连接是否有效

# 会话工厂，用于创建数据库会话
SessionLocal = sessionmaker(bind=engine)  # 绑定到engine，用于创建会话实例

# 声明性基类，所有模型类都继承自该类
Base = declarative_base()


class Repo(Base):
    """仓库模型类

    用于存储Git仓库的基本信息和元数据
    """
    __tablename__ = 'repos'  # 数据库表名
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 主键，UUID类型，自动生成
    owner = Column(String, nullable=True)  # 仓库所有者
    name = Column(String, nullable=True)  # 仓库名称
    full_name = Column(String, unique=True, nullable=False)  # 仓库全名，格式为owner/name，唯一约束
    remote_url = Column(Text, nullable=False)  # 仓库远程URL
    default_branch = Column(String, nullable=True)  # 仓库默认分支
    repo_metadata = Column(JSON, nullable=True)  # 仓库元数据，JSON格式存储额外信息
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))  # 创建时间，默认UTC时间


class Commit(Base):
    """提交模型类

    用于存储Git提交的信息和元数据
    """
    __tablename__ = 'commits'  # 数据库表名
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 主键，UUID类型，自动生成
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id', ondelete='CASCADE'))  # 关联仓库ID，级联删除
    commit_sha = Column(String, nullable=False)  # 提交SHA哈希值
    message = Column(Text, nullable=True)  # 提交信息
    committed_at = Column(DateTime, nullable=True)  # 提交时间
    commit_metadata = Column(JSON, nullable=True)  # 提交元数据，JSON格式存储额外信息
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))  # 创建时间，默认UTC时间


class File(Base):
    """文件模型类

    用于存储仓库文件的信息和元数据
    """
    __tablename__ = 'files'  # 数据库表名
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 主键，UUID类型，自动生成
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id', ondelete='CASCADE'))  # 关联仓库ID，级联删除
    commit_id = Column(UUID(as_uuid=True), ForeignKey('commits.id', ondelete='CASCADE'))  # 关联提交ID，级联删除
    path = Column(Text, nullable=False)  # 文件路径
    size = Column(BigInteger)  # 文件大小，单位字节
    language = Column(String)  # 文件编程语言
    content_hash = Column(String, nullable=False)  # 文件内容哈希值，用于检测文件变化
    file_metadata = Column(JSON, nullable=True)  # 文件元数据，JSON格式存储额外信息
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))  # 创建时间，默认UTC时间


class IngestTask(Base):
    """ ingestion任务模型类

    用于存储仓库数据摄取任务的状态和结果
    """
    __tablename__ = 'ingest_tasks'  # 数据库表名
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 主键，UUID类型，自动生成
    external_task_id = Column(String, nullable=True)  # 外部任务ID，用于关联Celery等任务队列
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id'))  # 关联仓库ID
    status = Column(String, nullable=True)  # 任务状态，如pending、running、completed、failed
    payload = Column(JSON, nullable=True)  # 任务负载，JSON格式存储任务参数
    result = Column(JSON, nullable=True)  # 任务结果，JSON格式存储任务执行结果
    started_at = Column(DateTime, nullable=True)  # 任务开始时间
    finished_at = Column(DateTime, nullable=True)  # 任务结束时间
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))  # 创建时间，默认UTC时间


def init_db():
    """初始化数据库

    创建所有定义的数据库表，如果表已存在则跳过
    """
    Base.metadata.create_all(bind=engine)  # 创建所有模型对应的数据库表