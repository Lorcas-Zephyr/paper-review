import pytest
from core.review_engine import ReviewDecisionEngine

# 测试数据
TEST_AUDIT_DATA = [
    {
        "audit_agent": "Logic_Audit",
        "result_id": "RES001",
        "audit_point": "论文摘要逻辑一致性",
        "problem_level": "Critical",
        "confidence": 0.65,
        "evidence": "",  # 证据缺失
        "impact_scope": "核心部分"
    },
    {
        "audit_agent": "Code_Audit",
        "result_id": "RES002",
        "audit_point": "论文摘要逻辑一致性",
        "problem_level": "Minor",
        "confidence": 0.7,
        "evidence": "摘要中XX结论与实验数据不符",
        "impact_scope": "核心部分"
    },
    {
        "audit_agent": "Logic_Audit",
        "result_id": "RES003",
        "audit_point": "参考文献格式",
        "problem_level": "Minor",
        "confidence": 0.8,
        "evidence": "参考文献缺少DOI编号",
        "impact_scope": "非核心部分"
    }
]

@pytest.fixture
def review_engine():
    return ReviewDecisionEngine(config_path="config/rule_config.json")

def test_calculate_sort_score(review_engine):
    """测试排序得分计算"""
    score = review_engine.calculate_sort_score("Critical", "核心部分")
    assert score == 1.3  # 1.0*(1+0.3)
    score2 = review_engine.calculate_sort_score("Minor", "非核心部分")
    assert score2 == 0.22  # 0.2*(1+0.1)

def test_check_review_trigger(review_engine):
    """测试复核触发条件"""
    # 构造AuditResult对象
    from core.review_engine import AuditResult
    result = AuditResult(
        audit_agent="Logic_Audit",
        result_id="TEST001",
        audit_point="测试点",
        problem_level="Major",
        confidence=0.65,
        evidence="",
        impact_scope="核心部分"
    )
    trigger, mark_type, reason = review_engine.check_review_trigger(result)
    assert trigger is True
    assert mark_type in ["Conf_Low", "Evid_Missing"]

def test_process_audit_results(review_engine):
    """测试完整处理流程"""
    sorted_results, review_marks = review_engine.process_audit_results(TEST_AUDIT_DATA)
    # 验证冲突检测（同一审核点有不同结论）
    assert len([m for m in review_marks if m.mark_type == "Agent_Conflict"]) == 1
    # 验证排序（Critical的得分最高）
    assert sorted_results[0].sort_score == 1.3
    # 验证复核标记数量
    assert len(review_marks) >= 2  # 证据缺失 + 冲突

if __name__ == "__main__":
    pytest.main(["-v", "tests/test_review_engine.py"])
