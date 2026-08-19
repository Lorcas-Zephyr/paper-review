import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field, validator


# ====================== 数据模型定义 ======================
class AuditResult(BaseModel):
    """审计组输出结果模型"""
    audit_agent: str = Field(..., description="审计组标识，如Logic_Audit/Code_Audit")
    result_id: str = Field(..., description="审计结果唯一ID")
    audit_point: str = Field(..., description="论文审核点")
    problem_level: str = Field(..., description="问题等级：Critical/Major/Minor/None")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度（0-1）")
    evidence: str = Field(..., description="问题证据")
    impact_scope: str = Field(default="无明确范围", description="影响范围：核心部分/非核心部分/无明确范围")
    audit_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @validator("problem_level")
    def validate_problem_level(cls, v):
        valid_levels = ["Critical", "Major", "Minor", "None"]
        if v not in valid_levels:
            raise ValueError(f"问题等级必须是{valid_levels}中的一种")
        return v

    @validator("impact_scope")
    def validate_impact_scope(cls, v):
        valid_scopes = ["核心部分", "非核心部分", "无明确范围"]
        if v not in valid_scopes:
            raise ValueError(f"影响范围必须是{valid_scopes}中的一种")
        return v


class ReviewMarkResult(BaseModel):
    """人工复核标记结果"""
    review_mark_id: str = Field(
        default_factory=lambda: f"RM-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}")
    audit_point: str = Field(...)
    related_agents: List[str] = Field(...)
    mark_type: str = Field(..., description="Conf_Low/Agent_Conflict/Evid_Missing")
    trigger_reason: str = Field(...)
    confidence_scores: Dict[str, float] = Field(...)
    mark_priority: str = Field(...)
    related_result_ids: List[str] = Field(...)
    generate_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class SortedAuditResult(BaseModel):
    """排序后的审计结果"""
    result_id: str = Field(...)
    audit_agent: str = Field(...)
    audit_point: str = Field(...)
    problem_level: str = Field(...)
    sort_score: float = Field(..., description="排序得分，保留2位小数")
    mark_status: str = Field(..., description="是否标记复核：是/否")
    priority_rank: int = Field(...)


# ====================== 核心引擎类 ======================
class ReviewDecisionEngine:
    def __init__(self, config_path: str = "config/rule_config.json"):
        """初始化规则引擎，加载配置文件"""
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)
        self.weight_config = self.config["weight_config"]
        self.review_config = self.config["review_trigger"]
        self.field_mapping = self.config["field_mapping"]

    def _normalize_audit_fields(self, raw_audit_data: Dict[str, Any]) -> Dict[str, Any]:
        """字段标准化：统一不同审计组的字段命名"""
        normalized_data = {}
        for old_field, new_field in self.field_mapping["audit_result"].items():
            value = raw_audit_data.get(old_field, raw_audit_data.get(new_field, ""))

            # 特殊处理：level字段映射
            if new_field == "problem_level" and value:
                level_mapping = {
                    "Info": "None",
                    "Pass": "None",
                    "Warning": "Minor",
                    "Critical": "Critical",
                    "Major": "Major",
                    "Minor": "Minor",
                    "None": "None"
                }
                value = level_mapping.get(value, value)

            normalized_data[new_field] = value
        # 补充其他必要字段
        for key in ["audit_agent", "result_id", "impact_scope"]:
            normalized_data[key] = raw_audit_data.get(key, "")
        return normalized_data

    def calculate_sort_score(self, problem_level: str, impact_scope: str) -> float:
        """计算排序得分：基础权重 × (1 + 附加权重)"""
        base_weight = self.weight_config["problem_level"].get(problem_level, 0.0)
        additional_weight = self.weight_config["impact_scope"].get(impact_scope, 0.0)
        sort_score = base_weight * (1 + additional_weight)
        return round(sort_score, 2)

    def check_review_trigger(self, audit_result: AuditResult) -> Tuple[bool, str, str]:
        """检查是否触发人工复核"""
        # 1. 检查置信度阈值（按审计组类型适配）
        audit_type = audit_result.audit_agent
        threshold = self.review_config["audit_type_threshold"].get(audit_type,
                                                                   self.review_config["confidence_threshold"])

        # 忽略对 confidence == 0 的低置信度报警，因为有些Agent没有提供这个字段，默认转换后可能为 0
        if 0 < audit_result.confidence < threshold:
            return True, "Conf_Low", f"置信度{audit_result.confidence}低于阈值{threshold}"

        # 2. 证据缺失已由统一的防幻觉机制(evidence_enforcement)在冲突裁决阶段处理，
        # 这里不再单独抛出黄框警告，以免造成页面上的重复提示干扰用户。

        return False, "", ""

    def detect_agent_conflict(self, audit_results: List[AuditResult]) -> List[ReviewMarkResult]:
        """检测多组审计结果冲突"""
        conflict_marks = []
        # 按审核点分组
        audit_point_groups = {}
        for result in audit_results:
            if result.audit_point not in audit_point_groups:
                audit_point_groups[result.audit_point] = []
            audit_point_groups[result.audit_point].append(result)

        # 检查每组是否存在冲突
        for audit_point, results in audit_point_groups.items():
            if len(results) < self.review_config["conflict_group_threshold"]:
                continue

            # 提取不同审计组的问题等级
            problem_levels = {r.audit_agent: r.problem_level for r in results}
            if len(set(problem_levels.values())) > 1:
                # 存在冲突，生成标记
                related_agents = list(problem_levels.keys())
                confidence_scores = {r.audit_agent: r.confidence for r in results}
                related_result_ids = [r.result_id for r in results]

                mark = ReviewMarkResult(
                    audit_point=audit_point,
                    related_agents=related_agents,
                    mark_type="Agent_Conflict",
                    trigger_reason=f"{len(results)}个审计组结论冲突：{problem_levels}",
                    confidence_scores=confidence_scores,
                    mark_priority="高",
                    related_result_ids=related_result_ids
                )
                conflict_marks.append(mark)

        return conflict_marks

    def process_audit_results(self, raw_audit_list: List[Dict[str, Any]]) -> Tuple[
        List[SortedAuditResult], List[ReviewMarkResult]]:
        """处理审计结果：排序+复核标记"""
        # 1. 字段标准化
        normalized_results = []
        for raw_data in raw_audit_list:
            normalized_data = self._normalize_audit_fields(raw_data)
            try:
                audit_result = AuditResult(**normalized_data)
                normalized_results.append(audit_result)
            except Exception as e:
                print(f"数据标准化失败：{e}，跳过该条数据")
                continue

        # 2. 检查单条记录的复核触发条件
        single_review_marks = []
        for result in normalized_results:
            trigger, mark_type, reason = self.check_review_trigger(result)
            if trigger:
                mark = ReviewMarkResult(
                    audit_point=result.audit_point,
                    related_agents=[result.audit_agent],
                    mark_type=mark_type,
                    trigger_reason=reason,
                    confidence_scores={result.audit_agent: result.confidence},
                    mark_priority="中",
                    related_result_ids=[result.result_id]
                )
                single_review_marks.append(mark)

        # 3. 检测多组冲突
        conflict_marks = self.detect_agent_conflict(normalized_results)
        all_review_marks = single_review_marks + conflict_marks

        # 4. 计算排序得分并排序
        sorted_results = []
        for idx, result in enumerate(normalized_results):
            sort_score = self.calculate_sort_score(result.problem_level, result.impact_scope)
            # 判断是否被标记复核
            mark_status = "是" if any(
                mark.audit_point == result.audit_point and result.audit_agent in mark.related_agents
                for mark in all_review_marks
            ) else "否"

            sorted_result = SortedAuditResult(
                result_id=result.result_id,
                audit_agent=result.audit_agent,
                audit_point=result.audit_point,
                problem_level=result.problem_level,
                sort_score=sort_score,
                mark_status=mark_status,
                priority_rank=0  # 后续统一排序
            )
            sorted_results.append(sorted_result)

        # 按得分降序排序，分配排名
        sorted_results.sort(key=lambda x: x.sort_score, reverse=True)
        for idx, res in enumerate(sorted_results):
            res.priority_rank = idx + 1

        return sorted_results, all_review_marks
