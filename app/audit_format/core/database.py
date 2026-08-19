from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Integer, Text, TIMESTAMP, BigInteger, Enum as SAEnum, Boolean, Float, CheckConstraint, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from typing import AsyncGenerator
import enum
from datetime import datetime
from config import DATABASE_URL
import os
from contextlib import asynccontextmanager

from pgvector.sqlalchemy import Vector

Base = declarative_base()

class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"

class PaperSection(Base):
    """
    论文切片存储表 (paper_sections)
    """
    __tablename__ = "paper_sections"

    section_id = Column(Integer, primary_key=True, comment="章节ID")
    paper_id = Column(UUID(as_uuid=True), index=True, nullable=False, comment="论文ID")
    section_name = Column(String, comment="章节名称")
    section_content = Column(Text, comment="章节内容")
    content_vector = Column(Vector(768), nullable=True, comment="向量(768)")


class PaperParagraph(Base):
    __tablename__ = "paper_paragraphs"

    paragraph_id = Column(Integer, primary_key=True, comment="段落ID")
    paper_id = Column(UUID(as_uuid=True), index=True, nullable=False, comment="论文ID")
    paragraph_name = Column(String, comment="段落名称")
    paragraph_content = Column(Text, comment="段落内容")
    content_vector = Column(Vector(768), nullable=True, comment="向量(768)")


class Paper(Base):
    __tablename__ = "papers"

    paper_id = Column(UUID(as_uuid=True), primary_key=True, comment="论文ID")
    title = Column(String, comment="标题")
    abstract = Column(Text, comment="摘要")
    abstract_vector = Column(Vector(768), nullable=True, comment="向量(768)")


class Review(Base):
    __tablename__ = "reviews"

    review_id = Column(Integer, primary_key=True, comment="评审ID")
    section_id = Column(Integer, comment="章节ID")
    review_content = Column(Text, comment="评审内容")
    paper_id = Column(UUID(as_uuid=True), index=True, nullable=False, comment="论文ID")
    review_vector = Column(Vector(768), nullable=True, comment="向量(768)")

class ExpertComment(Base):
    """
    专家评语知识库 (expert_comments)
    用于存储量化规则和向量数据
    """
    __tablename__ = "expert_comments"
    __table_args__ = (
        CheckConstraint("weight IS NULL OR (weight >= 0 AND weight <= 1)", name="expert_comments_weight_check"),
    )

    comment_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="评语唯一ID")
    rule_code = Column(String, comment="规则编码")
    rule_category = Column(String, comment="规则分类")
    rule_title = Column(String, nullable=False, server_default=text("'默认规则标题'"), comment="规则标题")
    rule_text = Column(Text, comment="规则正文")
    indicator_name = Column(String, nullable=False, server_default=text("'默认指标名'"), comment="指标名")
    operator = Column(String, comment="比较算子")
    threshold_value = Column(Float, comment="阈值主值")
    threshold_secondary = Column(Float, comment="阈值次值")
    threshold_unit = Column(String, comment="阈值单位")
    severity = Column(String, comment="严重级别")
    weight = Column(Float, comment="权重")
    is_hard_rule = Column(Boolean, nullable=False, server_default=text("false"), comment="是否硬规则")
    evidence_pattern = Column(Text, comment="证据匹配模式")
    embedding = Column(Vector(768), nullable=True, comment="向量(768)")
    source = Column(String, comment="来源")
    active = Column(Boolean, nullable=False, server_default=text("true"), comment="是否启用")
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"), comment="创建时间")
    updated_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"), comment="更新时间")
    metric_id = Column(String, index=True, comment="关联指标/规则ID")
    text = Column(Text, comment="专家原始评语内容")

class AgentRule(Base):
    __tablename__ = "agent_rules"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_id = Column(String, index=True, nullable=False, comment="规则ID")
    content = Column(Text, nullable=False, comment="规则内容(YAML/JSON字符串)")
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

class GroundTruthIssue(Base):
    __tablename__ = "ground_truth_issues"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sample_id = Column(String, index=True, comment="样本ID（可选：用于回归样本）")
    paper_id = Column(UUID(as_uuid=True), index=True, comment="论文ID（可选：用于线上任务）")
    chunk_id = Column(String, index=True, comment="切片ID（可选）")
    issue_type = Column(String, index=True, nullable=False, comment="问题类型（与 issue.issue_type 对齐）")
    severity = Column(String, comment="严重程度（Info/Warning/Critical 等）")
    message = Column(Text, comment="问题描述（可选）")
    evidence = Column(Text, comment="证据片段（可选）")
    page_num = Column(Integer, comment="页码（可选）")
    bbox = Column(JSONB, comment="可选：边界框/区域信息")
    source = Column(String, comment="标注来源/标注者/批次（可选）")
    created_at = Column(TIMESTAMP, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

class ReviewTask(Base):
    """
    任务协同表 (review_tasks)
    严格遵循《开发规范》定义的核心数据表结构
    Ref: 开发规范 - 三、数据库设计规范 - 1. 核心数据表结构 - (3) 任务协同表
    """
    __tablename__ = "review_tasks"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    task_id = Column(String, index=True, nullable=False, comment="对应上传request_id")
    paper_id = Column(UUID(as_uuid=True), index=True, nullable=False, comment="关联论文ID")
    chunk_id = Column(String, nullable=False, comment="切片ID")

    agent_name = Column(String, nullable=False, comment="负责审计的Agent名称")
    agent_version = Column(String, nullable=False, comment="审计时的模型/逻辑版本")

    status = Column(SAEnum(TaskStatus, name="taskstatus", create_type=False), default=TaskStatus.PENDING, comment="任务状态")

    score = Column(Integer, comment="从result_json冗余的分数")
    audit_level = Column(String, comment="风险等级：Info/Warning/Critical")

    result_json = Column(JSONB, comment="Agent返回的完整原始数据")
    error_msg = Column(Text, comment="任务失败时，记录错误堆栈/原因")

    usage_tokens = Column(Integer, comment="统计单次任务Token消耗")
    latency_ms = Column(Integer, comment="记录任务耗时")

    created_at = Column(TIMESTAMP, default=datetime.utcnow, comment="任务创建时间")
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow, comment="任务最后一次状态变更时间")


class AgentAudit(Base):
    __tablename__ = "agent_audits"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    paper_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    chunk_id = Column(String)
    agent_name = Column(String)
    agent_version = Column(String)
    status = Column(SAEnum(TaskStatus, name="audit_status", create_type=False))
    score = Column(Integer)
    audit_level = Column(String)
    result_json = Column(JSONB)
    error_msg = Column(Text)
    usage_tokens = Column(Integer)
    latency_ms = Column(Integer)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

from sqlalchemy.pool import NullPool

class DatabaseManager:
    """
    负责管理数据库连接和会话 (Async SQLAlchemy)
    Ref: 开发规范 - 三、数据库设计规范 - 2. 数据库操作要求
    """
    def __init__(self):
        db_timeout = int(os.getenv("DB_CONNECT_TIMEOUT_SEC", "3"))
        self.engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"timeout": db_timeout},
        )
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.async_session() as session:
            yield session

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.async_session() as session:
            yield session

    async def close(self):
        await self.engine.dispose()

# 全局 DB 实例
db_manager = DatabaseManager()
