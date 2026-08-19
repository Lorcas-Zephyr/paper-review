import psycopg2
from typing import Dict, List, Optional

class DatabaseConnector:
    def __init__(self, host, port, database, user, password):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.conn = None
        self.cur = None

    def connect(self):
        """连接到数据库"""
        self.conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password
        )
        self.cur = self.conn.cursor()

    def close(self):
        """关闭数据库连接"""
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def get_paper_by_id(self, paper_id: str) -> Dict:
        """根据paper_id获取论文信息"""
        # 获取论文基本信息
        self.cur.execute("SELECT paper_id, title, abstract FROM papers WHERE paper_id = %s", (paper_id,))
        paper_info = self.cur.fetchone()
        if not paper_info:
            return None

        # 获取论文所有章节
        self.cur.execute("""
            SELECT section_id, section_name, section_content
            FROM paper_sections
            WHERE paper_id = %s
            ORDER BY section_id
        """, (paper_id,))
        sections = self.cur.fetchall()

        # 构建完整论文内容
        full_content = []
        for section in sections:
            section_id, section_name, section_content = section
            # 添加章节标题和内容
            if section_name:
                full_content.append(f"# {section_name}")
            if section_content:
                full_content.append(section_content)

        return {
            "paper_id": paper_info[0],
            "title": paper_info[1],
            "abstract": paper_info[2],
            "content": "\n\n".join(full_content)
        }

    def get_all_papers(self) -> List[Dict]:
        """获取所有论文的基本信息"""
        self.cur.execute("SELECT paper_id, title, abstract FROM papers")
        papers = self.cur.fetchall()
        return [
            {"paper_id": paper[0], "title": paper[1], "abstract": paper[2]}
            for paper in papers
        ]
