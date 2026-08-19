import os

import psycopg2

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

def get_db_connection():
    """建立 PostgreSQL 数据库连接"""
    return psycopg2.connect(**DB_CONFIG)

def get_paper_code_chunk(paper_id: str, section_name:str,chunk_id: str) -> str:
    """
    根据 paper_id 和 chunk_id 从 paper_sections 表中精准提取代码段落。
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 严格按照你定义的逻辑进行联合查询
            cur.execute(
                """
                SELECT content
                FROM paper_sections
                WHERE paper_id = %s
                  AND section_name = %s
                  AND chunk_id = %s
                LIMIT 1;
                """,
                (paper_id, section_name, chunk_id)
            )
            result = cur.fetchone()

            # 如果查到了结果，返回第一列（content）的数据
            if result:
                return result[0]

            # 没查到则返回空字符串
            return ""
    finally:
        # 无论成功或报错，务必释放数据库连接
        conn.close()

# 这里的 get_expert_comments 保持之前的 SBERT 向量检索逻辑不变
# def get_expert_comments(code_content: str, limit: int = 3) -> str:
# ...
