import sys
import os
import re
import itertools
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.semantic.semantic_modeling import SemanticModeling
from src.slicer.paper_slicer import PaperSlicer


class LogicAuditor:
    """逻辑审计智能体：矛盾检测、逻辑跳跃、证据追溯"""

    # 逻辑衔接关键词（用于论证跳跃检测）
    TRANSITION_WORDS = {"因此", "所以", "因而", "从而", "导致", "造成",
                        "使得", "由此可见", "综上所述", "基于此", "于是",
                        "结果", "由此", "据此", "故", "因此可见"}

    def __init__(self):
        self.semantic_model = SemanticModeling()  # 复用语义建模模块
        self.slicer = PaperSlicer()

    @staticmethod
    def _extract_numbers(text: str):
        """提取文本中的数字（包括百分数）"""
        return re.findall(r'(\d+(?:\.\d+)?%?)', text)

    @staticmethod
    def _compare_numbers(nums1, nums2, threshold=5.0):
        """比较两组数字，若差值超过阈值则认为矛盾"""
        if not nums1 or not nums2:
            return False
        try:
            v1 = float(re.sub(r'[^0-9.]', '', nums1[0]))
            v2 = float(re.sub(r'[^0-9.]', '', nums2[0]))
            return abs(v2 - v1) > threshold
        except ValueError:
            return False

    @staticmethod
    def _is_title(text: str) -> bool:
        """判断文本是否为标题（启发式）"""
        text = text.strip()
        if text.startswith('#') or text.startswith('图') or text.startswith('表'):
            return True
        if re.match(r'^[1-9]\d*\.[\d.]*', text):
            return True
        if len(text) < 20 and (text.endswith('章') or text.endswith('节') or text.endswith('小结')):
            return True
        return False

    def audit_chunk(self, abstract: str, content: str, context_before: str, chunk_id: str):
        """审计单个论文切片（用于原 /audit/logic 接口）"""
        issues = []

        # 全局锚点注入
        abstract_props = self.semantic_model.extract_propositions(abstract, "abstract")
        global_claims = [p for p in abstract_props if p['type'] == 'core_claim']

        # 当前切片命题
        current_props = self.semantic_model.extract_propositions(content, "current")

        # 前文命题
        prev_props = []
        if context_before.strip():
            prev_props = self.semantic_model.extract_propositions(context_before, "prev")

        # 矛盾检测
        for prop in current_props:
            for anchor in global_claims:
                # 数值矛盾
                anchor_nums = self._extract_numbers(anchor['content'])
                prop_nums = self._extract_numbers(prop['content'])
                if self._compare_numbers(anchor_nums, prop_nums, threshold=5.0):
                    issues.append({
                        "chunk_id": chunk_id,
                        "evidence_quote": prop['content'],
                        "issue_type": "Contradictory_Claim",
                        "comment": f"数值矛盾：摘要声称 {anchor_nums[0]}，正文声称 {prop_nums[0]}",
                        "suggestion": "请核实实验数据，确保前后一致"
                    })
                    continue

                # NLI 矛盾
                rel, conf = self.semantic_model.model_nli_inference(anchor['content'], prop['content'])
                if rel == 'contradiction' and conf > 0.6:
                    issues.append({
                        "chunk_id": chunk_id,
                        "evidence_quote": prop['content'],
                        "issue_type": "Contradictory_Claim",
                        "comment": f"与摘要中的声明语义矛盾：{anchor['content'][:50]}...",
                        "suggestion": "请检查实验数据或修正声明"
                    })

        # 逻辑跳跃检测
        if prev_props and current_props:
            last_prev = prev_props[-1]
            first_curr = current_props[0]
            has_transition = any(word in first_curr['content'] for word in self.TRANSITION_WORDS)
            if not has_transition:
                issues.append({
                    "chunk_id": chunk_id,
                    "evidence_quote": f"{last_prev['content'][:30]}... → {first_curr['content'][:30]}...",
                    "issue_type": "Logic_Leap",
                    "comment": "上下文之间缺乏逻辑衔接（缺少过渡词）",
                    "suggestion": "添加“因此”“所以”等过渡词，使论证连贯"
                })

        # 证据追溯
        for prop in current_props:
            if prop['type'] == 'core_claim':
                has_evidence = False
                for prev in prev_props:
                    if prev['type'] == 'evidence':
                        sim = self.semantic_model.calculate_semantic_similarity(prev['content'], prop['content'])
                        if sim > 0.3:
                            has_evidence = True
                            break
                if not has_evidence:
                    issues.append({
                        "chunk_id": chunk_id,
                        "evidence_quote": prop['content'],
                        "issue_type": "Unsupported_Arg",
                        "comment": "论点缺乏前置证据支撑",
                        "suggestion": "在前文补充实验数据或理论依据"
                    })

        return issues

    def audit_paper(self, paper_json: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        审计整篇论文（基于语义建模生成的 JSON 数据）
        :param paper_json: 包含 nodes 和 edges 的字典
        :return: 问题列表，每个问题为字典格式
        """
        issues = []
        nodes = paper_json.get("nodes", [])
        edges = paper_json.get("edges", [])  # 保留 edges 但不用于矛盾检测

        # 分离摘要节点和正文节点
        summary_nodes = [n for n in nodes if n.get("section") == "summary"]
        body_nodes = [n for n in nodes if n.get("section") in ("experiment", "conclusion")]

        # 对正文节点按 position 排序
        def position_key(node):
            pos = node.get("position", "")
            match = re.match(r'^chunk_(\d+):line_(\d+)$', pos)
            if match:
                return (int(match.group(1)), int(match.group(2)))
            return (float('inf'), 0)  # 无法解析的放最后

        body_nodes_sorted = sorted(body_nodes, key=position_key)

        # ---------- 矛盾检测（仅保留数值矛盾）----------
        summary_claims = [n for n in summary_nodes if n.get("type") == "core_claim"]
        if not summary_claims:
            summary_claims = summary_nodes

        for body_node in body_nodes_sorted:
            body_content = body_node["content"]

            for sum_node in summary_claims:
                sum_content = sum_node["content"]

                # 数值矛盾
                sum_nums = self._extract_numbers(sum_content)
                body_nums = self._extract_numbers(body_content)
                if self._compare_numbers(sum_nums, body_nums, threshold=5.0):
                    issues.append({
                        "chunk_id": body_node.get("position", "unknown"),
                        "evidence_quote": body_content,
                        "issue_type": "Contradictory_Claim",
                        "comment": f"数值矛盾：摘要声称 {sum_nums[0]}，正文声称 {body_nums[0]}",
                        "suggestion": "请核实实验数据，确保前后一致"
                    })
                    # 已标记矛盾，继续下一个摘要节点

        # ---------- 逻辑跳跃检测（仅在同一个 chunk 内）----------
        # 按 chunk 分组
        def chunk_key(node):
            pos = node.get("position", "")
            match = re.match(r'^chunk_(\d+)', pos)
            return match.group(1) if match else None

        body_nodes_sorted.sort(key=lambda n: (chunk_key(n), position_key(n)[1]))
        for chunk_id, group in itertools.groupby(body_nodes_sorted, key=chunk_key):
            if chunk_id is None:
                continue
            nodes_in_chunk = list(group)
            for i in range(len(nodes_in_chunk) - 1):
                node1 = nodes_in_chunk[i]
                node2 = nodes_in_chunk[i + 1]

                # 跳过标题
                if self._is_title(node2["content"]):
                    continue

                # 检查过渡词
                if not any(word in node2["content"] for word in self.TRANSITION_WORDS):
                    issues.append({
                        "chunk_id": node2.get("position", "unknown"),
                        "evidence_quote": node2["content"],
                        "issue_type": "Logic_Leap",
                        "comment": "同一段落内句子之间缺乏逻辑衔接（缺少过渡词）",
                        "suggestion": "添加“因此”“所以”等过渡词，使论证连贯"
                    })

        # ---------- 循环论证检测（保留原有启发式示例）----------
        for node in body_nodes_sorted:
            content = node["content"]
            if "本文证明" in content and "有效" in content:
                issues.append({
                    "chunk_id": node.get("position", "unknown"),
                    "evidence_quote": content,
                    "issue_type": "Circular_Reasoning",
                    "comment": "可能包含循环论证（自我证明）",
                    "suggestion": "检查论证逻辑，避免用结论证明结论"
                })

        return issues
