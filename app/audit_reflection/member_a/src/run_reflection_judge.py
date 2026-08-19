import asyncio
import json
import logging
import sys
from pathlib import Path

# 添加项目根目录到Python路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conflict_resolver import ConflictResolver
from src.mock_data_generator import MockDataGenerator
from src.schemas import ConflictResolutionRequest

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_conflict_resolution_demo():
    """运行冲突裁决演示"""
    print("=== 反思评估组冲突裁决演示 ===\n")

    # 创建模拟数据生成器
    generator = MockDataGenerator()

    # 创建冲突裁决器
    resolver = ConflictResolver()

    # 场景1: 有冲突的情况
    print("\n--- 场景1: 有冲突的情况 ---")
    conflict_request_data = generator.generate_conflict_resolution_request(
        paper_title="基于深度学习的图像识别算法优化研究",
        with_conflict=True
    )

    print("\n输入数据 (Agent结果):")
    for i, agent_result in enumerate(conflict_request_data["payload"]["agent_results"], 1):
        agent_name = agent_result["agent_info"]["name"]
        score = agent_result["result"]["score"]
        audit_level = agent_result["result"]["audit_level"]
        comment = agent_result["result"]["comment"]
        print(f"{i}. {agent_name} (评分:{score}, 级别:{audit_level}): {comment}")

    # 转换为请求对象
    conflict_request = ConflictResolutionRequest(**conflict_request_data)

    # 执行冲突裁决
    print("\n执行冲突裁决...")
    try:
        response = await resolver.resolve_conflicts(conflict_request)

        print("\n裁决结果:")
        print(f"- 冲突是否已解决: {response.result['conflicts_resolved']}")
        print(f"- 置信度: {response.result.get('confidence_score', 'N/A')}")

        if response.result.get('final_verdict'):
            verdict = response.result['final_verdict']
            print(f"- 最终结论: {verdict.get('verdict', 'N/A')}")
            print(f"- 平均分: {verdict.get('average_score', 'N/A')}")

        if response.result.get('resolved_issues'):
            print("\n解决的冲突详情:")
            for i, issue in enumerate(response.result['resolved_issues'], 1):
                agent1 = issue.get('agent1_name', 'N/A')
                agent2 = issue.get('agent2_name', 'N/A')
                conflict_type = issue.get('conflict_type', 'N/A')
                final_level = issue.get('final_level', 'N/A')
                resolved_comment = issue.get('resolved_comment', 'N/A')
                resolved_suggestion = issue.get('resolved_suggestion', 'N/A')

                print(f"\n  冲突 {i}:")
                print(f"    - 冲突双方: {agent1} vs {agent2}")
                print(f"    - 冲突类型: {conflict_type}")
                print(f"    - 最终级别: {final_level}")
                print(f"    - 裁决意见: {resolved_comment}")
                print(f"    - 改进建议: {resolved_suggestion}")

        if response.result.get('markdown_report'):
            print("\n--- Markdown报告 ---")
            print(response.result['markdown_report'])

    except Exception as e:
        logger.error(f"冲突裁决失败: {str(e)}")
        print(f"错误: {str(e)}")

    # 场景2: 无冲突的情况
    print("\n\n--- 场景2: 无冲突的情况 ---")
    no_conflict_request_data = generator.generate_conflict_resolution_request(
        paper_title="微服务架构在大型企业中的应用研究",
        with_conflict=False
    )

    print("\n输入数据 (Agent结果):")
    for i, agent_result in enumerate(no_conflict_request_data["payload"]["agent_results"], 1):
        agent_name = agent_result["agent_info"]["name"]
        score = agent_result["result"]["score"]
        audit_level = agent_result["result"]["audit_level"]
        comment = agent_result["result"]["comment"]
        print(f"{i}. {agent_name} (评分:{score}, 级别:{audit_level}): {comment}")

    # 转换为请求对象
    no_conflict_request = ConflictResolutionRequest(**no_conflict_request_data)

    # 执行冲突裁决
    print("\n执行冲突裁决...")
    try:
        response = await resolver.resolve_conflicts(no_conflict_request)

        print("\n裁决结果:")
        print(f"- 冲突是否已解决: {response.result['conflicts_resolved']}")
        print(f"- 置信度: {response.result.get('confidence_score', 'N/A')}")

        if response.result.get('final_verdict'):
            verdict = response.result['final_verdict']
            print(f"- 最终结论: {verdict.get('verdict', 'N/A')}")
            print(f"- 平均分: {verdict.get('average_score', 'N/A')}")

    except Exception as e:
        logger.error(f"冲突裁决失败: {str(e)}")
        print(f"错误: {str(e)}")


async def run_new_format_demo():
    """运行新格式数据演示"""
    print("=== 反思评估组新格式数据演示 ===\n")

    from src.conflict_resolver import ConflictResolver
    from src.mock_data_generator import MockDataGenerator
    from src.schemas import ConflictResolutionRequest

    # 创建模拟数据生成器
    generator = MockDataGenerator()

    # 创建冲突裁决器
    resolver = ConflictResolver()

    # 生成所有组的新格式数据
    print("--- 生成所有审计组的新格式数据 ---")
    all_groups_data = generator.generate_all_groups_new_format(with_conflict=True)

    # 合并所有组数据为一个payload（模拟多个组同时提交）
    combined_payload = {
        "agent_results": []
    }

    for group_data in all_groups_data:
        # 将新格式数据添加到payload中（冲突裁决器会自动转换）
        combined_payload["agent_results"].append(group_data)

    # 创建请求
    request_data = {
        "request_id": f"req_new_format_{len(all_groups_data)}",
        "metadata": {
            "paper_id": "paper_new_format_001",
            "paper_title": "基于Transformer的深度学习模型优化研究"
        },
        "payload": combined_payload,
        "config": {
            "temperature": 0.3,
            "max_tokens": 1500,
            "conflict_threshold": 0.7
        }
    }

    print(f"共生成 {len(all_groups_data)} 个审计组的数据")
    for group_data in all_groups_data:
        group_id = group_data.get("group_id", 0)
        audit_results = group_data.get("audit_results", [])
        print(f"  组{group_id}: {len(audit_results)} 条评审意见")

    # 转换为请求对象
    request = ConflictResolutionRequest(**request_data)

    # 执行冲突裁决
    print("\n--- 执行冲突裁决（包含幻觉过滤和加权评分）---")
    try:
        response = await resolver.resolve_conflicts(request)

        print("\n裁决结果:")
        print(f"- 冲突是否已解决: {response.result['conflicts_resolved']}")
        print(f"- 置信度: {response.result.get('confidence_score', 'N/A')}")
        print(f"- 证据验证分数: {response.result.get('evidence_validation', {}).get('validation_score', 'N/A'):.2%}")

        if response.result.get('final_verdict'):
            verdict = response.result['final_verdict']
            print(f"- 最终结论: {verdict.get('verdict', 'N/A')}")
            print(f"- 加权平均分: {verdict.get('average_score', 'N/A')}")
            if verdict.get('original_average_score'):
                print(f"- 原始平均分: {verdict.get('original_average_score', 'N/A')}")
            if verdict.get('evidence_validation_score'):
                print(f"- 证据验证分数: {verdict.get('evidence_validation_score', 'N/A'):.2%}")

        if response.result.get('evidence_validation'):
            validation = response.result['evidence_validation']
            print(f"\n证据验证结果:")
            print(f"  有效引用: {validation.get('valid_count', 0)} / 总引用: {validation.get('total_quotes', 0)}")
            print(f"  无效引用: {validation.get('invalid_count', 0)}")
            if validation.get('invalid_count', 0) > 0:
                print(f"  无效引用详情:")
                for invalid in validation.get('invalid_results', [])[:3]:
                    agent = invalid.get('agent_name', '未知')
                    quote = invalid.get('clean_quote', '')
                    if len(quote) > 50:
                        quote = quote[:50] + "..."
                    print(f"    - {agent}: \"{quote}\"")

        if response.result.get('markdown_report_path'):
            print(f"\nMarkdown报告已保存: {response.result['markdown_report_path']}")

        # 显示报告摘要
        if response.result.get('markdown_report'):
            report_lines = response.result['markdown_report'].split('\n')
            print("\n--- 报告摘要 (前20行) ---")
            for line in report_lines[:20]:
                print(line)

    except Exception as e:
        logger.error(f"冲突裁决失败: {str(e)}")
        print(f"错误: {str(e)}")


async def run_api_server():
    """启动API服务器"""
    import uvicorn
    from src.conflict_resolver import app

    print("\n=== 启动反思评估组API服务器 ===")
    print("API文档将在 http://localhost:8000/docs 可用")
    print("健康检查: http://localhost:8000/health")
    print("冲突裁决端点: http://localhost:8000/api/resolve_conflicts")

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """主函数"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="反思评估组冲突裁决系统")
    parser.add_argument(
        "mode",
        choices=["demo", "server", "generate-data", "new-format-demo"],
        default="demo",
        help="运行模式: demo(演示冲突裁决), server(启动API服务器), generate-data(生成模拟数据), new-format-demo(新格式数据演示)"
    )

    args = parser.parse_args()

    if args.mode == "demo":
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(run_conflict_resolution_demo())
    elif args.mode == "new-format-demo":
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(run_new_format_demo())
    elif args.mode == "server":
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(run_api_server())
    elif args.mode == "generate-data":
        generator = MockDataGenerator()

        # 生成有冲突的Agent结果JSON字符串
        conflict_json = generator.generate_json_string()
        print("=== 有冲突的Agent结果JSON字符串 ===")
        print(conflict_json)

        # 生成无冲突的Agent结果JSON字符串
        no_conflict_json = generator.generate_json_string(generator.generate_agent_results(with_conflict=False))
        print("\n=== 无冲突的Agent结果JSON字符串 ===")
        print(no_conflict_json)

        # 生成新格式数据
        print("\n=== 新格式数据（组2-6）===")
        for group_id in range(2, 7):
            new_format_data = generator.generate_new_format_data(group_id=group_id, num_results=2, with_conflict=(group_id in [4, 5]))
            print(f"\n组{group_id}数据:")
            print(json.dumps(new_format_data, ensure_ascii=False, indent=2))

        # 生成完整的冲突裁决请求
        conflict_request = generator.generate_conflict_resolution_request()
        print("\n=== 完整的冲突裁决请求 ===")
        print(json.dumps(conflict_request, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
