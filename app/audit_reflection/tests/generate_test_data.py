"""
测试数据生成器
模拟4个审计组对论文的评审结果
支持两种模式：
1. file模式：生成JSON文件到prompts目录
2. database模式：插入数据到PostgreSQL的agent_audit_result表

Week3更新：
- 审计组从5个改为4个（FMT/REF/EXP/LOG，去掉代码审计）
- 数据库表从agent_audits改为agent_audit_result
- 使用真实的rule_id和规则名称
- result_json格式匹配新的agent_audit_result表结构
"""
import hashlib
import json
import random
import uuid
import asyncio
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# 添加项目根目录到sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import DatabaseManager

logger = logging.getLogger(__name__)


class TestDataGenerator:
    """测试数据生成器"""

    # 4个审计组配置（agent_code -> 中文名）
    AUDIT_AGENTS = {
        "FMT": "格式审计智能体",
        "REF": "文献审计智能体",
        "EXP": "实验数据智能体",
        "LOG": "逻辑审计智能体",
    }

    # 每个agent_code对应的规则（rule_id -> rule_name_cn, full_score, severity）
    AGENT_RULES = {
        "FMT": [
            {"rule_id": "FMT-001", "name": "论文总字数达标", "full_score": 7, "severity": "CRITICAL"},
            {"rule_id": "FMT-002", "name": "核心章节字数占比达标", "full_score": 6, "severity": "CRITICAL"},
            {"rule_id": "FMT-003", "name": "排版自闭环规范", "full_score": 3, "severity": "WARNING"},
            {"rule_id": "FMT-004", "name": "图表公式引用/格式规范", "full_score": 4, "severity": "WARNING"},
        ],
        "REF": [
            {"rule_id": "REF-001", "name": "参考文献总数达标", "full_score": 6, "severity": "CRITICAL"},
            {"rule_id": "REF-002", "name": "近3年文献占比达标", "full_score": 5, "severity": "CRITICAL"},
            {"rule_id": "REF-003", "name": "选题贴合领域热点/难点", "full_score": 5, "severity": "CRITICAL"},
            {"rule_id": "REF-004", "name": "英文/CCF文献占比达标", "full_score": 4, "severity": "WARNING"},
        ],
        "EXP": [
            {"rule_id": "EXP-001", "name": "必须报告显著性P值", "full_score": 6, "severity": "CRITICAL"},
            {"rule_id": "EXP-002", "name": "多组比较需要检验方法", "full_score": 4, "severity": "CRITICAL"},
            {"rule_id": "EXP-003", "name": "小样本需正态性检验", "full_score": 3, "severity": "WARNING"},
            {"rule_id": "EXP-004", "name": "均值必须配STD/SEM", "full_score": 3, "severity": "CRITICAL"},
            {"rule_id": "EXP-005", "name": "图表应包含误差棒", "full_score": 3, "severity": "WARNING"},
            {"rule_id": "EXP-006", "name": "正文与图表数值一致", "full_score": 4, "severity": "CRITICAL"},
            {"rule_id": "EXP-007", "name": "至少对比2种近3年SOTA基线", "full_score": 4, "severity": "CRITICAL"},
            {"rule_id": "EXP-008", "name": "训练测试严格分离", "full_score": 3, "severity": "CRITICAL"},
        ],
        "LOG": [
            {"rule_id": "LOG-001", "name": "摘要五段式结构完整", "full_score": 5, "severity": "CRITICAL"},
            {"rule_id": "LOG-002", "name": "全文三级逻辑闭环", "full_score": 6, "severity": "CRITICAL"},
            {"rule_id": "LOG-003", "name": "软件架构UML视图达标", "full_score": 5, "severity": "CRITICAL"},
            {"rule_id": "LOG-004", "name": "全文核心术语一致性", "full_score": 4, "severity": "CRITICAL"},
            {"rule_id": "LOG-005", "name": "相关技术章节闭环衔接", "full_score": 3, "severity": "WARNING"},
            {"rule_id": "LOG-006", "name": "实验分析回应研究问题", "full_score": 3, "severity": "CRITICAL"},
            {"rule_id": "LOG-007", "name": "创新点数量达标", "full_score": 4, "severity": "CRITICAL"},
        ],
    }

    # 问题描述模板
    DESCRIPTIONS = {
        "Critical": [
            "发现严重问题：{}",
            "存在关键缺陷：{}",
            "致命错误：{}"
        ],
        "Warning": [
            "需要注意：{}",
            "建议改进：{}",
            "存在问题：{}"
        ],
        "Info": [
            "符合规范：{}",
            "表现良好：{}",
            "基本合格：{}"
        ]
    }

    SUGGESTIONS = {
        "Critical": ["必须立即修正{}", "强烈建议重新审查{}", "需要彻底修改{}"],
        "Warning": ["建议补充{}", "建议优化{}", "建议完善{}"],
        "Info": ["可以进一步提升{}", "保持当前水平", "继续保持"]
    }

    @staticmethod
    def generate_audit_result(
        paper_id: str,
        agent_code: str,
        paper_name: str = "测试论文"
    ) -> Dict[str, Any]:
        """
        生成单个审计组的结果（符合agent_audit_result.result_json格式）

        Args:
            paper_id: 论文ID
            agent_code: 审计组编码（FMT/REF/EXP/LOG）
            paper_name: 论文题目

        Returns:
            包含result_json和行级字段的审计结果
        """
        rules = TestDataGenerator.AGENT_RULES[agent_code]
        audit_items = []

        for rule in rules:
            # 随机决定是否合规
            is_compliant = random.choices([1, 0], weights=[0.6, 0.4])[0]

            if is_compliant:
                level = "Info"
                score = rule["full_score"]
            else:
                level = "Critical" if rule["severity"] == "CRITICAL" else "Warning"
                # 不合规时扣分
                score = random.randint(0, rule["full_score"] - 1)

            desc_template = random.choice(TestDataGenerator.DESCRIPTIONS[level])
            sugg_template = random.choice(TestDataGenerator.SUGGESTIONS[level])
            description = desc_template.format(rule["name"])
            suggestion = sugg_template.format(rule["name"])

            section = f"{random.randint(1, 8)}.{random.randint(1, 5)}"
            evidence_quote = f"原文第{section}节提到：'{rule['name']}相关内容...'"

            # result_id必须<=32字符，paper_id可能是UUID(36字符)
            # 固定部分: "RES-" + agent_code(3) + "-" + "-" + rule_num(3) = 12字符
            # paper_id部分最多20字符
            pid_short = paper_id if len(paper_id) <= 20 else hashlib.md5(paper_id.encode()).hexdigest()[:8]
            result_id = f"RES-{agent_code}-{pid_short}-{rule['rule_id'].split('-')[1]}"

            audit_items.append({
                "result_id": result_id,
                "paper_id": paper_id,
                "point": rule["name"],
                "rule_id": rule["rule_id"],
                "score": score,
                "level": level,
                "description": description,
                "evidence_quote": evidence_quote,
                "location": {"section": section, "line_start": random.randint(1, 500)},
                "suggestion": suggestion,
                "is_compliant": is_compliant,
                "actual_value": str(random.randint(1, 100)),
            })

        # 构建result_json（符合work_week3格式）
        result_json = {
            "agent_code": agent_code,
            "audit_results": audit_items
        }

        return {
            "agent_code": agent_code,
            "paper_id": paper_id,
            "paper_name": paper_name,
            "result_json": result_json,
            "audit_items": audit_items,  # 行级数据
        }

    @staticmethod
    def generate_paper_audits(
        paper_id: Optional[str] = None,
        paper_name: str = "测试论文"
    ) -> List[Dict[str, Any]]:
        """
        生成一篇论文的4个审计组结果

        Args:
            paper_id: 论文ID，如果为None则自动生成
            paper_name: 论文题目

        Returns:
            4个审计组的结果列表
        """
        if paper_id is None:
            paper_id = f"P{random.randint(100, 999)}"

        results = []
        for agent_code in TestDataGenerator.AUDIT_AGENTS.keys():
            result = TestDataGenerator.generate_audit_result(
                paper_id=paper_id,
                agent_code=agent_code,
                paper_name=paper_name
            )
            results.append(result)

        return results

    @staticmethod
    def save_to_files(
        paper_audits: List[Dict[str, Any]],
        output_dir: str = "prompts"
    ):
        """将审计结果保存为JSON文件"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        paper_id = paper_audits[0]["paper_id"]

        for audit in paper_audits:
            agent_code = audit["agent_code"]
            filename = f"{paper_id}_{agent_code}.json"
            filepath = output_path / filename

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(audit["result_json"], f, ensure_ascii=False, indent=2)

            print(f"已生成: {filepath}")

    @staticmethod
    async def save_to_database(
        paper_audits: List[Dict[str, Any]],
        db_manager: DatabaseManager
    ):
        """
        将审计结果保存到PostgreSQL的agent_audit_result表

        每条规则对应一行记录，result_json存储该agent_code的完整审计结果。

        Args:
            paper_audits: 审计结果列表（4个审计组）
            db_manager: 数据库管理器实例
        """
        paper_id = paper_audits[0]["paper_id"]
        paper_name = paper_audits[0].get("paper_name", "测试论文")

        try:
            async with db_manager.acquire() as conn:
                for audit in paper_audits:
                    agent_code = audit["agent_code"]
                    result_json = audit["result_json"]
                    audit_items = audit["audit_items"]

                    for item in audit_items:
                        result_id = item["result_id"]
                        rule_id = item["rule_id"]
                        is_compliant = item.get("is_compliant", 0)
                        actual_value = item.get("actual_value", "")
                        score_obtained = item.get("score", 0)
                        audit_suggestion = item.get("suggestion", "")

                        query = """
                            INSERT INTO agent_audit_result (
                                result_id, paper_id, paper_name,
                                agent_code, rule_id,
                                is_compliant, actual_value, score_obtained,
                                audit_suggestion, audit_time, result_json
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), $10::jsonb)
                        """

                        await conn.execute(
                            query,
                            result_id,
                            paper_id,
                            paper_name,
                            agent_code,
                            rule_id,
                            is_compliant,
                            actual_value,
                            score_obtained,
                            audit_suggestion,
                            json.dumps(result_json, ensure_ascii=False),
                        )

                    total_score = sum(i["score"] for i in audit_items)
                    print(f"已插入: {paper_id}, {agent_code} ({len(audit_items)}条规则, 总分={total_score})")

        except Exception as e:
            print(f"数据库插入失败: {e}")
            raise

    @staticmethod
    def generate_multiple_papers(
        num_papers: int = 3,
        output_dir: str = "prompts"
    ):
        """生成多篇论文的测试数据（文件模式）"""
        print(f"开始生成{num_papers}篇论文的测试数据...")

        for i in range(num_papers):
            paper_id = f"P{i+1:03d}"
            paper_name = f"基于深度学习的软件缺陷预测方法研究_{i+1}"
            print(f"\n生成论文 {i+1}/{num_papers}: {paper_id} - {paper_name}")

            paper_audits = TestDataGenerator.generate_paper_audits(
                paper_id=paper_id,
                paper_name=paper_name
            )
            TestDataGenerator.save_to_files(paper_audits, output_dir)

        print(f"\n完成！共生成{num_papers}篇论文，每篇4个审计组结果")
        print(f"文件保存在: {output_dir}/")

    @staticmethod
    async def generate_multiple_papers_to_db(
        num_papers: int = 3,
        db_manager: DatabaseManager = None,
        use_existing_papers: bool = False,
        paper_id: Optional[str] = None
    ):
        """
        生成多篇论文的测试数据（数据库模式）

        Args:
            num_papers: 论文数量
            db_manager: 数据库管理器实例
            use_existing_papers: 是否使用数据库中已存在的paper_id
            paper_id: 指定论文ID，指定后只为该paper_id生成测试数据
        """
        if db_manager is None:
            db_manager = DatabaseManager()

        await db_manager.connect()

        try:
            # 如果指定了paper_id，只为该paper_id生成数据
            if paper_id:
                print(f"为指定论文生成测试数据: {paper_id}")
                paper_name = f"测试论文_{paper_id[:8]}"
                paper_audits = TestDataGenerator.generate_paper_audits(
                    paper_id=paper_id,
                    paper_name=paper_name
                )
                await TestDataGenerator.save_to_database(paper_audits, db_manager)
                print(f"\n完成！已为论文{paper_id}生成4个审计组结果")
                print(f"数据已插入到agent_audit_result表")
                return

            print(f"开始生成{num_papers}篇论文的测试数据并插入数据库...")

            existing_paper_ids = []
            if use_existing_papers:
                async with db_manager.acquire() as conn:
                    try:
                        query = "SELECT DISTINCT paper_id FROM agent_audit_result LIMIT $1"
                        rows = await conn.fetch(query, num_papers)
                        existing_paper_ids = [str(row["paper_id"]) for row in rows]
                    except Exception:
                        try:
                            query = "SELECT paper_id FROM papers LIMIT $1"
                            rows = await conn.fetch(query, num_papers)
                            existing_paper_ids = [str(row["paper_id"]) for row in rows]
                        except Exception as e:
                            print(f"无法从数据库读取已有paper_id: {e}")

                if existing_paper_ids and len(existing_paper_ids) < num_papers:
                    print(f"数据库中只有{len(existing_paper_ids)}篇论文，将只生成{len(existing_paper_ids)}篇")
                    num_papers = len(existing_paper_ids)

            for i in range(num_papers):
                if use_existing_papers and i < len(existing_paper_ids):
                    paper_id = existing_paper_ids[i]
                else:
                    paper_id = f"P{i+1:03d}"

                paper_name = f"基于深度学习的软件缺陷预测方法研究_{i+1}"
                print(f"\n生成论文 {i+1}/{num_papers}: {paper_id}")

                paper_audits = TestDataGenerator.generate_paper_audits(
                    paper_id=paper_id,
                    paper_name=paper_name
                )
                await TestDataGenerator.save_to_database(paper_audits, db_manager)

            print(f"\n完成！共生成{num_papers}篇论文，每篇4个审计组结果")
            print(f"数据已插入到agent_audit_result表")

        finally:
            await db_manager.disconnect()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="测试数据生成器")
    parser.add_argument(
        "--mode", type=str, choices=["file", "database"], default="file",
        help="生成模式：file=生成JSON文件，database=插入数据库"
    )
    parser.add_argument(
        "--num-papers", type=int, default=3, help="生成的论文数量"
    )
    parser.add_argument(
        "--output-dir", type=str, default="prompts",
        help="输出目录（仅file模式有效）"
    )
    parser.add_argument(
        "--use-existing-papers", action="store_true",
        help="使用数据库中已存在的paper_id（仅database模式有效）"
    )
    parser.add_argument(
        "--paper-id", type=str, default=None,
        help="指定论文ID（仅database模式有效，指定后只为该paper_id生成测试数据）"
    )

    args = parser.parse_args()

    if args.mode == "file":
        TestDataGenerator.generate_multiple_papers(
            num_papers=args.num_papers,
            output_dir=args.output_dir
        )
    elif args.mode == "database":
        asyncio.run(
            TestDataGenerator.generate_multiple_papers_to_db(
                num_papers=args.num_papers,
                use_existing_papers=args.use_existing_papers,
                paper_id=args.paper_id
            )
        )


if __name__ == "__main__":
    main()
