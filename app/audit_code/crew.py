from crewai import Agent, Task, Crew, Process
from schemas import AuditResult
import time
import config
def add_line_numbers(code_content:str) -> str:
    # 给代码文本的每一行添加行号前缀，例如 '1 | def foo():'
    lines = code_content.splitlines()
    # 动态计算行号对齐的宽度，让代码更整齐
    max_digits = len(str(len(lines)))
    numbered_lines = [
        f"{i + 1:>{max_digits}} | {line}" for i, line in enumerate(lines)
    ]
    return "\n".join(numbered_lines)

def retrieve_expert_comments(code_content:str) -> str:
    """
    模拟语义检索：从 expert_comments 表中检索专家知识 。
    将来可替换为真实的 pgvector SBERT 检索逻辑 。
    """
    return "专家历史评语参考：对于除零风险，必须要求在运算前增加分母平滑项（如 epsilon=1e-8）。"
def run_code_review(code_content: str) -> dict:
    start_time = time.time()
    #获取专家知识
    expert_context = retrieve_expert_comments(code_content)
    # 预处理：生成带行号的代码
    numbered_code = add_line_numbers(code_content)

    # 1. 定义审查智能体
    reviewer_agent = Agent(
        role='资深代码审查员 (Senior Code Reviewer)',
        goal='全面审查提供的代码或者伪代码片段，找出语法、算法和注释上的缺陷。',
        backstory=("你是一位拥有多年经验的顶级软件工程师，专注于代码质量审查。你一丝不苟，追求编写整洁、可维护和安全的代码。"
                   "现在，你需要审查论文中的伪代码或代码片段。"
                   "请重点关注：1. 语法与规范性：识别代码片段中的拼写错误、命名不规范。；"
                   "2. 算法合理性：检查算法实现是否符合论文描述的公式，是否存在无限循环或显存溢出风险。；"
                    "3.注释匹配度：代码注释是否与实际逻辑一致"
                    "请具体给出出错的行号"),
        verbose=True,
        allow_delegation=False,
        llm = config.llm
    )

    # 2. 定义审查任务
    review_task = Task(
        description=(
            f"请审查以下代码片段：\n\n"
            f"```text\n{code_content}\n```\n\n"
            f"参考专家历史评语：\n{expert_context}\n\n"
            f"任务要求：\n"
            f"1. 审查语法与规范性、算法合理性、注释匹配度。\n"
            f"2. 输出必须包含 location 字段，格式如 '第 12 行：x = x / 0 存在除零风险。'"
        ),
        expected_output="符合 Pydantic 规范的 JSON 审查结果，包含 tags 和 location。",
        output_json=AuditResult, # 核心：强制输出为符合 Pydantic 模型的 JSON 字典
        agent=reviewer_agent
    )

    # 3. 组建 Crew 并执行
    crew = Crew(
        agents=[reviewer_agent],
        tasks=[review_task],
        process=Process.sequential,
        verbose=True
    )

    result = crew.kickoff()
    latency = int((time.time() - start_time) * 1000)

    # 返回结构化的 JSON 字典
    return {
        "result":result.json_dict,
        "usage": {
            "tokens": result.token_usage.total_tokens if hasattr(result, 'token_usage') else 0, "latency_ms": latency
        }
    }
