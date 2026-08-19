import os, json, time, sys

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.semantic.semantic_modeling import SemanticModeling
from src.slicer.paper_slicer import PaperSlicer
from database_connector import DatabaseConnector

# 初始化组件
semantic_modeler = SemanticModeling()
slicer = PaperSlicer(max_slice_length=200)

# 数据库连接配置
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# 文件路径配置
# 获取当前脚本（main.py）的绝对路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录是脚本目录的上两级（从 src/main/ 回到 DeepLogicAuditorAgent2）
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
# 基于项目根目录拼接正确的相对路径
PAPER_MD_PATH = os.path.join(PROJECT_ROOT, "papers-reviews-mineru10篇处理", "2018203215", "paper", "hybrid_auto", "paper.md")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "output", "test_contractory.json")
def judge_section(position: str):
    """
    根据 position 判断所属论文部分
    """
    if not position:
        return "unknown"

    # 摘要
    if position.startswith("abstract"):
        return "summary"

    # 正文默认实验
    if position.startswith("chunk"):
        return "experiment"

    return "unknown"


def refine_section_by_content(content, current_section):
    """
    根据内容进一步修正章节类型
    """
    if not content:
        return current_section

    # 第7章：总结 / 展望 / 结论
    if ("总结" in content
        or "展望" in content
        or "第七章" in content
        or "7 总结" in content):
        return "conclusion"

    return current_section

def main():
    print("开始处理论文...")
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    # 选择处理方式：从数据库或本地文件
    use_database = True  # 设置为True从数据库获取，False从本地文件获取

    if use_database:
        print("从数据库获取论文...")
        # 连接数据库
        db_connector = DatabaseConnector(**DB_CONFIG)
        db_connector.connect()

        try:
            # 选择论文ID（从数据库中获取的第一个论文）
            paper_id = "c757eff2-76cd-4833-bc5d-51f2a162c4a0"
            print(f"获取论文 ID: {paper_id}")

            # 从数据库读取论文内容
            paper_content = slicer.read_paper_from_db(paper_id, db_connector)

            # 处理论文内容
            print("处理论文内容...")
            semantic_input = slicer.generate_semantic_modeling_input_from_content(paper_content)
        finally:
            db_connector.close()
    else:
        # 使用切片器处理本地文件
        print("使用论文切片器处理本地论文...")
        semantic_input = slicer.generate_semantic_modeling_input(PAPER_MD_PATH)

    print(f"摘要命题数量: {len(semantic_input['abstract']['propositions'])}")
    print(f"正文切片数量: {len(semantic_input['chunks'])}")

    # 收集所有命题
    all_propositions = []
    all_propositions.extend(semantic_input["abstract"]["propositions"])
    print("收集正文命题...")
    for i, chunk in enumerate(semantic_input["chunks"]):
        if i % 5 == 0:
            print(f"处理切片 {i+1}/{len(semantic_input['chunks'])}")
        all_propositions.extend(chunk["propositions"])
    print(f"总命题数量: {len(all_propositions)}")

    # 计算命题间关系
    print("计算命题之间的关系...")
    if len(all_propositions) > 1:
        relations = semantic_modeler.identify_semantic_relations(all_propositions, context_window=20)
        graph = semantic_modeler.build_semantic_graph(all_propositions, relations)
        edges = [
            {"source": u, "target": v, "relation": data["relation_type"], "confidence": data["confidence"]}
            for u, v, data in graph.edges(data=True)
        ]
        # 如果没有检测到关系，使用顺序规则生成
        if not edges and len(all_propositions) > 1:
            print("添加基于规则的关系...")
            for i in range(len(all_propositions) - 1):
                edges.append({
                    "source": all_propositions[i]["prop_id"],
                    "target": all_propositions[i+1]["prop_id"],
                    "relation": "entailment",
                    "confidence": 0.7
                })
    else:
        edges = []

    # 构建节点列表
    nodes = []

    for prop in all_propositions:
        position = prop.get("position", "unknown")
        content = prop.get("content", "")

        # 初步判断
        section = judge_section(position)

        # 根据内容细化
        section = refine_section_by_content(content, section)

        node = {
            "id": prop["prop_id"],
            "content": content,
            "type": prop["type"],
            "position": position,
            "section": section  # ⭐ 新增字段
        }

        nodes.append(node)

    print(f"生成的边数量: {len(edges)}")
    print(f"生成的节点数量: {len(nodes)}")

    # 构建最终结果
    final_results = {
        "metadata": {"total_propositions": len(all_propositions), "total_edges": len(edges), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")},
        "nodes": nodes,
        "edges": edges
    }

    # 保存结果
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    print(f"结果保存到 {OUTPUT_PATH}")
    print(f"总命题数量: {len(all_propositions)}")
    print(f"总边数量: {len(edges)}")
    print("论文处理完成！")

if __name__ == "__main__":
    main()
