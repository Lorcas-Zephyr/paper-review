# main.py
# 协议与Mock组 - 完整五组审计API服务
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import random
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

# 关键：从统一的models.py导入所有模型
from models import (
    AgentInfo, Usage,
    ProtocolRequest, ProtocolResponse,
    CodeReviewRequest, CodeReviewResponse, CodeReviewResult,
    Group6AuditRequest, Group6AuditResponse, GenericAuditResult, AuditResultItem, AuditLevelGroup6,
    FormatAuditResponse, FormatResult, FormatIssueItem,
    LogicAuditResponse, LogicAuditResult, LogicAuditDetailItem,
    PaperAuditRequest, PaperAuditNode, PaperAuditEdge,
    CitationAuditResponse, CitationAuditResult
)

app = FastAPI(
    title="论文评审系统 Mock API - 完整五组审计服务版",
    description="包含逻辑组、实验组、代码组、格式组、文献组五个完整审计组的Mock服务",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模拟数据存储
mock_papers = []
mock_agent_audits = []

# ========== 1. 实验数据审计组接口 ==========
@app.post("/audit", response_model=Group6AuditResponse, summary="实验数据审计组")
async def group6_audit(request: Group6AuditRequest):
    time.sleep(random.uniform(1, 3))
    experiment_specific_result = AuditResultItem(
        point="统计学显著性检验",
        score=85,
        level=AuditLevelGroup6.WARNING,
        description="缺少 P-value 报告，无法证明结果显著性。",
        evidence_quote="我们提出的模型准确率...显著优于 SOTA。",
        location={"section": "实验与结果"},
        suggestion="请补充 T-test 或 Wilcoxon 检验的 P 值。"
    )

    generic_result = GenericAuditResult(
        score=experiment_specific_result.score,
        audit_level=experiment_specific_result.level.value,
        comment=experiment_specific_result.description,
        suggestion=experiment_specific_result.suggestion,
        tags=[experiment_specific_result.point],
        details=experiment_specific_result.dict()
    )

    return Group6AuditResponse(
        request_id=request.request_id,
        agent_info=AgentInfo(name="Experiment_Agent", version="v1.0"),
        result=generic_result,
        usage=Usage(tokens=random.randint(100, 300), latency_ms=int(random.uniform(1, 3) * 1000))
    )

# ========== 2. 代码审计组接口 ==========
@app.post("/api/review", response_model=CodeReviewResponse, summary="代码审计组接口")
async def code_review(request: CodeReviewRequest):
    time.sleep(random.uniform(1, 3))
    return CodeReviewResponse(
        request_id=request.request_id,
        agent_info=AgentInfo(name="Code_Review_Agent", version="v1.0"),
        result=CodeReviewResult(
            score=random.randint(70, 95),
            audit_level="HIGH",
            comment="代码结构清晰，但存在潜在性能瓶颈。",
            suggestion="建议使用缓存优化循环计算。",
            tags=["Performance", "Readability"],
            location="Line 42-58"
        ),
        usage=Usage(tokens=random.randint(100, 300), latency_ms=int(random.uniform(1, 3) * 1000))
    )

# ========== 3. 格式审计组接口 ==========
@app.post("/audit_format", response_model=FormatAuditResponse, summary="格式审计组接口")
async def format_audit(request: CodeReviewRequest):
    time.sleep(random.uniform(1, 2))
    return FormatAuditResponse(
        request_id=request.request_id,
        agent_info=AgentInfo(name="Standardization_Auditor_Agent", version="v1.1"),
        result=FormatResult(
            score=85,
            audit_level="Warning",
            comment="发现 3 个格式问题。",
            suggestion="建议修正图表标号及错别字。",
            tags=["Citation_Inconsistency", "Label_Missing"],
            issues=[
                FormatIssueItem(
                    issue_type="Label_Missing",
                    severity="Warning",
                    page_num=5,
                    bbox=[100.5, 200.3, 300.7, 250.9],
                    evidence="图1 实验结果",
                    message="图表缺少编号",
                    location={"section": "4.2", "line_start": 45},
                    anchor_id="anchor_001",
                    highlight=[100.5, 200.3, 300.7, 250.9],
                    suggestion="为图表添加编号"
                )
            ]
        ),
        usage=Usage(tokens=120, latency_ms=1500)
    )

# ========== 4. 逻辑审计组接口 ==========
@app.post("/audit/logic", response_model=LogicAuditResponse, summary="逻辑审计单切片接口")
async def logic_audit_single_chunk(request: ProtocolRequest):
    time.sleep(random.uniform(1, 3))
    details = [
        LogicAuditDetailItem(
            chunk_id=request.metadata.get("chunk_id", "chunk_1"),
            evidence_quote="实验结果显示准确率达到95%",
            issue_type="Contradictory_Claim",
            comment="数值矛盾：摘要声称 90%，正文声称 95%",
            suggestion="请核实实验数据，确保前后一致"
        ),
        LogicAuditDetailItem(
            chunk_id=request.metadata.get("chunk_id", "chunk_1"),
            evidence_quote="因此，该方法有效",
            issue_type="Logic_Leap",
            comment="上下文之间缺乏逻辑衔接（缺少过渡词）",
            suggestion="添加'因此''所以'等过渡词，使论证连贯"
        )
    ]
    return LogicAuditResponse(
        request_id=request.request_id,
        agent_info=AgentInfo(name="DeepLogicAuditor", version="v0.1"),
        result=LogicAuditResult(
            score=random.randint(60, 80),
            audit_level="Critical" if random.random() > 0.7 else "Warning",
            comment=f"发现 {len(details)} 处逻辑问题",
            suggestion="请根据具体问题修改",
            tags=["Contradictory_Claim", "Logic_Leap", "Unsupported_Arg"],
            details=details
        ),
        usage=Usage(tokens=random.randint(100, 300), latency_ms=int(random.uniform(1, 3) * 1000))
    )

@app.post("/audit/paper", response_model=LogicAuditResponse, summary="逻辑审计整篇论文接口")
async def logic_audit_whole_paper(request: PaperAuditRequest):
    time.sleep(random.uniform(2, 5))
    details = [
        LogicAuditDetailItem(
            chunk_id="chunk_1:line_5",
            evidence_quote="通过对比实验，本文方法准确率达到95%",
            issue_type="Contradictory_Claim",
            comment="数值矛盾：摘要声称 90%，正文声称 95%",
            suggestion="请核实实验数据，确保前后一致"
        ),
        LogicAuditDetailItem(
            chunk_id="chunk_2:line_10",
            evidence_quote="本文证明该方法有效",
            issue_type="Circular_Reasoning",
            comment="可能包含循环论证（自我证明）",
            suggestion="检查论证逻辑，避免用结论证明结论"
        )
    ]
    return LogicAuditResponse(
        request_id="paper_audit_" + str(uuid.uuid4())[:8],
        agent_info=AgentInfo(name="DeepLogicAuditor", version="v0.1"),
        result=LogicAuditResult(
            score=random.randint(50, 75),
            audit_level="Critical" if random.random() > 0.6 else "Warning",
            comment=f"发现 {len(details)} 处逻辑问题",
            suggestion="请根据具体问题修改",
            tags=["Contradictory_Claim", "Logic_Leap", "Unsupported_Arg", "Circular_Reasoning"],
            details=details
        ),
        usage=Usage(tokens=random.randint(200, 500), latency_ms=int(random.uniform(2, 5) * 1000))
    )

# ========== 5. 文献审计组接口 ==========
@app.post("/audit/citation", response_model=CitationAuditResponse, summary="文献审计接口")
async def citation_audit(request: ProtocolRequest):
    time.sleep(random.uniform(1, 4))
    chunk_id = request.metadata.get("chunk_id", "")
    content = request.payload.get("content", "")

    if chunk_id.startswith("_ref_"):
        ref_number = chunk_id.replace("_ref_", "").replace("_", "")
        comment = f"已验证第 {ref_number} 条参考文献"
        tags = ["Citation_OK"] if random.random() > 0.3 else ["Fake_Reference"]
    elif chunk_id == "_references_":
        comment = "参考文献整体质量良好，近3年文献占比 60%"
        tags = ["Citation_OK", "Recent_References"]
    else:
        if "reference" in content.lower() or "参考文献" in content:
            comment = "参考文献段落格式正确，但存在2条未经验证的引用"
            tags = ["Citation_NotChecked", "Format_Correct"]
        else:
            comment = "正文片段包含相关引用，经检索基本真实"
            tags = ["Citation_OK", "Relevant_Content"]

    score = random.randint(60, 83)
    if "Fake_Reference" in tags:
        score -= 20
    if "Citation_NotChecked" in tags:
        score -= 10
    final_score = max(0, min(100, score))

    return CitationAuditResponse(
        request_id=request.request_id,
        agent_info=AgentInfo(name="Citation_Agent", version="v1.0"),
        result=CitationAuditResult(
            score=final_score,
            audit_level="Critical" if final_score < 60 else ("Warning" if final_score < 70 else "Info"),
            comment=comment,
            suggestion="建议补充近3年高被引文献" if final_score < 70 else None,
            tags=tags
        ),
        usage={"tokens": random.randint(50, 150), "latency_ms": int(random.uniform(1, 3) * 1000)}
    )

# ========== 健康检查 ==========
@app.get("/")
async def root():
    return {"message": "论文评审系统 Mock API (五组完整版) 运行正常"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
