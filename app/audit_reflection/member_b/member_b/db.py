from typing import List

from .models import Section


class DBClient:
    def __init__(self, dsn: str):
        self.dsn = dsn

    async def fetch_sections(self, paper_id: str) -> List[Section]:
        try:
            import asyncpg  # type: ignore
        except Exception as exc:
            raise RuntimeError("需要安装 asyncpg 才能访问数据库") from exc

        sql = """
        SELECT section_id, title, paragraph_index, paragraph_text
        FROM paper_sections
        WHERE paper_id = $1
        ORDER BY section_id, paragraph_index;
        """

        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch(sql, paper_id)
        finally:
            await conn.close()

        sections_map = {}
        for row in rows:
            section_id = row["section_id"]
            title = row["title"] or ""
            sections_map.setdefault(section_id, {"title": title, "paragraphs": []})
            sections_map[section_id]["paragraphs"].append(row["paragraph_text"])

        sections = []
        for section_id, data in sections_map.items():
            sections.append(Section(section_id=section_id, title=data["title"], paragraphs=data["paragraphs"]))

        return sections
