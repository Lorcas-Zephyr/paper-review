from unittest.mock import AsyncMock, patch

import pytest

from src.conflict_resolver import ConflictResolver, app
from src.schemas import ConflictType


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def resolver():
    """创建一个不连接真实DB/LLM的ConflictResolver"""
    with patch("src.conflict_resolver.DatabaseClient"), \
         patch("src.conflict_resolver.LLMClient"):
        r = ConflictResolver()
        r.db_client = AsyncMock()
        r.llm_client = AsyncMock()
        return r


@pytest.fixture
def agent_results_with_conflict():
    """包含关键词矛盾的Agent结果（高效 vs 缓慢）"""
    return [
        {
            "request_id": "req_001",
            "agent_info": {"name": "代码审计组", "version": "v1.0"},
            "result": {
                "score": 85,
                "audit_level": "Info",
                "comment": "算法实现高效，时间复杂度为O(n log n)",
                "suggestion": "代码结构清晰，无明显优化空间"
            },
            "usage": {"tokens": 100, "latency_ms": 500}
        },
        {
            "request_id": "req_002",
            "agent_info": {"name": "实验数据组", "version": "v1.0"},
            "result": {
                "score": 55,
                "audit_level": "Warning",
                "comment": "实验结果显示算法在大数据集上运行缓慢",
                "suggestion": "建议优化算法实现或考虑替代方案"
            },
            "usage": {"tokens": 120, "latency_ms": 600}
        }
    ]


@pytest.fixture
def agent_results_no_conflict():
    """无冲突的Agent结果"""
    return [
        {
            "request_id": "req_003",
            "agent_info": {"name": "格式审计组", "version": "v1.0"},
            "result": {
                "score": 90,
                "audit_level": "Info",
                "comment": "引用格式规范",
                "suggestion": "无需修改"
            },
            "usage": {"tokens": 80, "latency_ms": 400}
        },
        {
            "request_id": "req_004",
            "agent_info": {"name": "文献真实性组", "version": "v1.0"},
            "result": {
                "score": 88,
                "audit_level": "Info",
                "comment": "参考文献真实可信",
                "suggestion": "引用适当"
            },
            "usage": {"tokens": 90, "latency_ms": 450}
        }
    ]


@pytest.fixture
def agent_results_score_diff_only():
    """仅有分数差异（无关键词矛盾）的Agent结果"""
    return [
        {
            "request_id": "req_005",
            "agent_info": {"name": "AgentA", "version": "v1.0"},
            "result": {
                "score": 90,
                "audit_level": "Info",
                "comment": "论文结构合理",
                "suggestion": "无"
            },
            "usage": {"tokens": 50, "latency_ms": 200}
        },
        {
            "request_id": "req_006",
            "agent_info": {"name": "AgentB", "version": "v1.0"},
            "result": {
                "score": 60,
                "audit_level": "Warning",
                "comment": "论文结构需要改进",
                "suggestion": "重新组织章节"
            },
            "usage": {"tokens": 60, "latency_ms": 250}
        }
    ]


@pytest.fixture
def sample_conflict_request():
    """完整的冲突裁决请求"""
    return {
        "request_id": "test_req_001",
        "metadata": {
            "paper_id": "test_paper_001",
            "paper_title": "深度学习模型优化研究"
        },
        "payload": {
            "agent_results": [
                {
                    "request_id": "req_001",
                    "agent_info": {"name": "代码审计组", "version": "v1.0"},
                    "result": {
                        "score": 85,
                        "audit_level": "Info",
                        "comment": "算法实现高效，时间复杂度为O(n log n)",
                        "suggestion": "代码结构清晰"
                    },
                    "usage": {"tokens": 100, "latency_ms": 500}
                },
                {
                    "request_id": "req_002",
                    "agent_info": {"name": "实验数据组", "version": "v1.0"},
                    "result": {
                        "score": 55,
                        "audit_level": "Warning",
                        "comment": "实验结果显示算法在大数据集上运行缓慢",
                        "suggestion": "建议优化"
                    },
                    "usage": {"tokens": 120, "latency_ms": 600}
                }
            ]
        },
        "config": {
            "temperature": 0.3,
            "max_tokens": 800
        }
    }


# ── 冲突检测单元测试 ──────────────────────────────────────

class TestConflictDetection:

    def test_detects_keyword_conflict(self, resolver, agent_results_with_conflict):
        """关键词矛盾（高效 vs 缓慢）应被检测到"""
        conflicts = resolver.detect_conflicts(agent_results_with_conflict)

        assert len(conflicts) >= 1
        c = conflicts[0]
        assert c["agent1"] == "代码审计组"
        assert c["agent2"] == "实验数据组"
        assert c["conflict_type"] == ConflictType.DIRECT_CONTRADICTION

    def test_no_conflict_detected(self, resolver, agent_results_no_conflict):
        """无矛盾的结果不应产生冲突"""
        conflicts = resolver.detect_conflicts(agent_results_no_conflict)
        assert len(conflicts) == 0

    def test_score_difference_conflict(self, resolver, agent_results_score_diff_only):
        """分数差异>=20应被检测为MEASUREMENT_DIFFERENCE冲突"""
        conflicts = resolver.detect_conflicts(
            agent_results_score_diff_only,
            conflict_threshold=0.5  # 降低阈值以捕获分数差异冲突
        )

        assert len(conflicts) >= 1
        score_conflicts = [
            c for c in conflicts
            if c["conflict_type"] == ConflictType.MEASUREMENT_DIFFERENCE
        ]
        assert len(score_conflicts) >= 1
        assert abs(score_conflicts[0]["score1"] - score_conflicts[0]["score2"]) >= 20

    def test_empty_results(self, resolver):
        """空结果列表应返回空冲突"""
        assert resolver.detect_conflicts([]) == []

    def test_single_result(self, resolver, agent_results_with_conflict):
        """单个Agent结果不足以产生冲突"""
        assert resolver.detect_conflicts([agent_results_with_conflict[0]]) == []

    def test_missing_agent_info_skipped(self, resolver):
        """缺少agent_info的结果应被跳过"""
        results = [
            {"result": {"comment": "test", "score": 80}},
            {
                "agent_info": {"name": "AgentA"},
                "result": {"comment": "test2", "score": 70, "audit_level": "Info"}
            }
        ]
        conflicts = resolver.detect_conflicts(results)
        assert len(conflicts) == 0

    def test_configurable_conflict_threshold(self, resolver, agent_results_with_conflict):
        """高阈值应过滤掉低置信度冲突"""
        # 极高阈值 -> 可能过滤掉所有冲突
        conflicts_strict = resolver.detect_conflicts(
            agent_results_with_conflict, conflict_threshold=0.99
        )
        # 低阈值 -> 保留更多冲突
        conflicts_loose = resolver.detect_conflicts(
            agent_results_with_conflict, conflict_threshold=0.5
        )
        assert len(conflicts_loose) >= len(conflicts_strict)


# ── 反义词检测测试 ──────────────────────────────────────

class TestOpposites:

    def test_known_opposites(self, resolver):
        assert resolver._are_opposites("高效", "低效") is True
        assert resolver._are_opposites("低效", "高效") is True

    def test_non_opposites(self, resolver):
        assert resolver._are_opposites("高效", "准确") is False


# ── resolve_conflicts 集成测试（mock LLM & DB）──────────

class TestResolveConflicts:

    @pytest.mark.asyncio
    async def test_resolve_with_llm(self, resolver, sample_conflict_request):
        """有冲突时应调用LLM裁决"""
        from src.schemas import ConflictResolutionRequest

        mock_resolution = {
            "conflicts_resolved": True,
            "resolved_issues": [{
                "agent1_name": "代码审计组",
                "agent2_name": "实验数据组",
                "conflict_type": "direct_contradiction",
                "resolved_comment": "综合考虑，算法在小数据集上高效但大数据集上需优化",
                "resolved_suggestion": "建议增加大数据集基准测试",
                "final_level": "Warning",
                "confidence": 0.85
            }],
            "confidence_score": 0.85
        }
        mock_usage = {"tokens": 200, "latency_ms": 1500}

        resolver.llm_client.resolve_conflicts_with_llm = AsyncMock(
            return_value=(mock_resolution, mock_usage)
        )
        resolver.db_client.save_resolution_result = AsyncMock(return_value=True)
        resolver.db_client.disconnect = AsyncMock()

        request = ConflictResolutionRequest(**sample_conflict_request)
        response = await resolver.resolve_conflicts(request)

        assert response.request_id == "test_req_001"
        assert response.result["conflicts_resolved"] is True
        assert len(response.result["resolved_issues"]) == 1
        assert response.usage["tokens"] == 200

    @pytest.mark.asyncio
    async def test_resolve_no_conflicts(self, resolver):
        """无冲突时应直接返回，不调用LLM"""
        from src.schemas import ConflictResolutionRequest

        request_data = {
            "request_id": "test_req_no_conflict",
            "metadata": {"paper_id": "p1"},
            "payload": {
                "agent_results": [
                    {
                        "agent_info": {"name": "A"},
                        "result": {"score": 90, "audit_level": "Info", "comment": "引用格式规范"}
                    },
                    {
                        "agent_info": {"name": "B"},
                        "result": {"score": 88, "audit_level": "Info", "comment": "参考文献可信"}
                    }
                ]
            }
        }
        resolver.db_client.disconnect = AsyncMock()

        request = ConflictResolutionRequest(**request_data)
        response = await resolver.resolve_conflicts(request)

        assert response.result["conflicts_resolved"] is False
        resolver.llm_client.resolve_conflicts_with_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_empty_payload(self, resolver):
        """空payload应返回400"""
        from src.schemas import ConflictResolutionRequest

        request = ConflictResolutionRequest(
            request_id="test_empty",
            metadata={},
            payload={}
        )
        resolver.db_client.disconnect = AsyncMock()

        # payload为空dict但不是None，agent_results为空 -> 返回"无Agent结果"
        response = await resolver.resolve_conflicts(request)
        assert response.result["conflicts_resolved"] is False
        assert "无Agent结果" in response.result.get("message", "")


# ── API端点测试（使用httpx AsyncClient）──────────────────

class TestAPIEndpoints:

    def test_health_check(self):
        """健康检查端点"""
        from fastapi.testclient import TestClient
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_resolve_endpoint(self, sample_conflict_request):
        """API端点集成测试（mock LLM和DB）"""
        from httpx import AsyncClient, ASGITransport

        mock_resolution = {
            "conflicts_resolved": True,
            "resolved_issues": [{
                "agent1_name": "代码审计组",
                "agent2_name": "实验数据组",
                "conflict_type": "direct_contradiction",
                "resolved_comment": "综合裁决意见",
                "resolved_suggestion": "建议",
                "final_level": "Warning",
                "confidence": 0.8
            }],
            "confidence_score": 0.8
        }
        mock_usage = {"tokens": 150, "latency_ms": 1000}

        with patch("src.conflict_resolver.resolver") as mock_resolver_instance:
            from src.schemas import ConflictResolutionResponse
            mock_resolver_instance.resolve_conflicts = AsyncMock(
                return_value=ConflictResolutionResponse(
                    request_id="test_req_001",
                    result=mock_resolution,
                    usage=mock_usage
                )
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/resolve_conflicts",
                    json=sample_conflict_request
                )

            assert response.status_code == 200
            data = response.json()
            assert data["request_id"] == "test_req_001"
            assert data["result"]["conflicts_resolved"] is True
