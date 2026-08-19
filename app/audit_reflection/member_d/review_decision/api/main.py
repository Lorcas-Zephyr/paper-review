from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any
from core.review_engine import ReviewDecisionEngine, SortedAuditResult, ReviewMarkResult

app = FastAPI(title="复核决策引擎API", version="1.0")
engine = ReviewDecisionEngine(config_path="config/rule_config.json")

@app.post("/process-audit", response_model=Dict[str, Any])
async def process_audit_results(audit_list: List[Dict[str, Any]]):
    """
    处理审计结果接口
    :param audit_list: 审计组原始结果列表
    :return: 排序结果 + 复核标记结果
    """
    try:
        sorted_results, review_marks = engine.process_audit_results(audit_list)
        # 转换为字典返回
        return {
            "code": 200,
            "message": "处理成功",
            "data": {
                "sorted_audit_results": [r.dict() for r in sorted_results],
                "review_mark_results": [m.dict() for m in review_marks]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理失败：{str(e)}")

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "engine_version": "1.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
