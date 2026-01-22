import os
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, JSON, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.dialects.postgresql import UUID
import datetime
import uuid

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@postgres:5432/repo_ingestor')

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Repo(Base):
    __tablename__ = 'repos'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner = Column(String, nullable=True)
    name = Column(String, nullable=True)
    full_name = Column(String, unique=True, nullable=False)
    remote_url = Column(Text, nullable=False)
    default_branch = Column(String, nullable=True)
    metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Commit(Base):
    __tablename__ = 'commits'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id', ondelete='CASCADE'))
    commit_sha = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    committed_at = Column(DateTime, nullable=True)
    metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class File(Base):
    __tablename__ = 'files'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id', ondelete='CASCADE'))
    commit_id = Column(UUID(as_uuid=True), ForeignKey('commits.id', ondelete='CASCADE'))
    path = Column(Text, nullable=False)
    size = Column(BigInteger)
    language = Column(String)
    content_hash = Column(String, nullable=False)
    metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class IngestTask(Base):
    __tablename__ = 'ingest_tasks'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_task_id = Column(String, nullable=True)
    repo_id = Column(UUID(as_uuid=True), ForeignKey('repos.id'))
    status = Column(String, nullable=True)
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)