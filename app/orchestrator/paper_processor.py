# paper_processor.py (修改版：删去本地模型加载支持)
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'  # 设置Hugging Face国内镜像
import json
import re
import uuid
import numpy as np
import psycopg2
import torch
from transformers import AutoTokenizer, AutoModel
from bs4 import BeautifulSoup
from pgvector.psycopg2 import register_vector
from typing import List, Optional, Dict, Any
import gc
from datetime import datetime

# PostgreSQL连接设置（密码等见 orchestrator/.env）
def get_db_connection():
    """获取数据库连接"""
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
    )

class BertVectorGenerator:
    """基于BERT的向量生成器"""

    def __init__(self, model_name: str = "sentence-transformers/all-mpnet-base-v2"):
        """
        初始化BERT向量生成器

        参数:
            model_name: Hugging Face模型名称
        """
        # 关键修改：直接将国内镜像地址写入代码

        self.model_name = model_name
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"使用设备: {self.device}")
        print(f"模型下载镜像: {os.environ.get('HF_ENDPOINT')}")

        # 从Hugging Face加载分词器和模型（将通过上述镜像地址下载）
        print(f"从镜像站加载模型: {model_name}")
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModel.from_pretrained(model_name)
        except Exception as e:
            raise RuntimeError(f"模型加载失败: {e}\n提示：当前使用镜像地址 {os.environ.get('HF_ENDPOINT')}")


        self.model.to(self.device)
        self.model.eval()

        # 获取向量维度
        with torch.no_grad():
            dummy_input = self.tokenizer("test", return_tensors="pt", truncation=True, padding=True)
            dummy_input = {k: v.to(self.device) for k, v in dummy_input.items()}
            outputs = self.model(**dummy_input)
            self.embedding_dim = outputs.last_hidden_state.size(-1)

        print(f"模型加载完成，向量维度: {self.embedding_dim}")

    def generate_embedding(self, text: str, max_length: int = 512) -> Optional[np.ndarray]:
        if not text or text.strip() == "":
            return None

        text = text.strip()

        try:
            inputs = self.tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=max_length
            )

            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.model(**inputs)

                if "sentence-transformers" in self.model_name:
                    embeddings = outputs.last_hidden_state[:, 0, :]
                else:
                    attention_mask = inputs['attention_mask']
                    token_embeddings = outputs.last_hidden_state

                    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()

                    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
                    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
                    embeddings = sum_embeddings / sum_mask

                embedding = embeddings.cpu().numpy()[0].astype(np.float32)
                embedding = embedding / np.linalg.norm(embedding)

                return embedding

        except Exception as e:
            print(f"生成BERT嵌入时出错: {e}")
            return None

    def __del__(self):
        if hasattr(self, 'model'):
            try:
                del self.model
            except Exception:
                pass
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass

def sanitize_for_postgres(text) -> str:
    """PostgreSQL 文本参数不能含 NUL（\\x00）；PDF/Office 转 MD 偶尔带入，会导致 psycopg2 报错。"""
    if text is None:
        return ""
    return str(text).replace("\x00", "")


# 辅助函数 (clean_text, split_into_blocks, extract_summary, truncate_text_for_bert) 保持不变
def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<sup.*?>.*?</sup>', '', text)
    text = BeautifulSoup(text, "html.parser").get_text()
    return text.strip()

def split_into_blocks(text):
    blocks = []
    lines = text.split("\n")
    block = []
    section_name = None

    for line in lines:
        if line.startswith("#"):
            if block:
                blocks.append({
                    'section_name': section_name,
                    'section_content': "\n".join(block)
                })
                block = []
            section_name = line.lstrip("#").strip()
        else:
            block.append(line)

    if block:
        blocks.append({
            'section_name': section_name,
            'section_content': "\n".join(block)
        })

    return blocks

def extract_summary(content_blocks):
    """从章节中提取摘要"""
    for block in content_blocks:
        section_name = block['section_name']
        section_content = block['section_content']
        if section_name and "摘" in section_name and "要" in section_name:
            return section_content
    return None

def truncate_text_for_bert(text, max_chars=3000):
    if not text:
        return text
    if len(text) > max_chars:
        return text[:max_chars] + "...[截断]"
    return text

class PaperProcessor:
    """论文处理器，集成到调度器中"""

    def __init__(self, model_name: str = "sentence-transformers/all-mpnet-base-v2"):
        """
        初始化论文处理器（延迟加载 BERT 与数据库连接，避免阻塞调度器进程启动与 /health）
        """
        self._model_name = model_name
        self._bert_generator = None
        self._conn = None

    def _ensure_db(self):
        if self._conn is None:
            self._conn = get_db_connection()
            register_vector(self._conn)

    def _ensure_bert(self):
        if self._bert_generator is None:
            self._bert_generator = BertVectorGenerator(self._model_name)

    @property
    def bert_generator(self):
        self._ensure_bert()
        return self._bert_generator

    @property
    def conn(self):
        self._ensure_db()
        return self._conn

    def process_paper_from_content(self, title: str, content: str) -> str:
        """
        直接从论文内容处理，生成paper_id
        参数:
            title: 论文标题
            content: Markdown格式的论文内容
        返回:
            paper_id: 生成的唯一标识符
        """
        title = sanitize_for_postgres(title)
        content = sanitize_for_postgres(content)
        print(f"[paper] 开始处理论文: {title}")

        conn = None
        try:
            conn = get_db_connection()
            register_vector(conn)

            # 1. 清洗和分割内容
            cleaned_text = clean_text(content)
            content_blocks = split_into_blocks(cleaned_text)

            # 2. 提取摘要
            abstract = extract_summary(content_blocks)
            if not abstract and content_blocks:
                abstract = content_blocks[0]['section_content']

            # 3. 生成唯一paper_id
            short_uuid = str(uuid.uuid4())
            paper_id = f"{short_uuid}"

            # 4. 生成摘要向量
            abstract_vector = None
            if abstract:
                truncated_abstract = truncate_text_for_bert(abstract)
                abstract_vector = self.bert_generator.generate_embedding(truncated_abstract)
                if abstract_vector is not None:
                    print(f"[ok] 摘要向量生成完成，维度: {abstract_vector.shape}")

            # 5. 插入论文数据到数据库
            cursor = conn.cursor()
            if abstract_vector is not None:
                cursor.execute("""
                    INSERT INTO papers (paper_id, title, abstract, abstract_vector)
                    VALUES (%s, %s, %s, %s)
                """, (paper_id, title, abstract, abstract_vector))
            else:
                cursor.execute("""
                    INSERT INTO papers (paper_id, title, abstract)
                    VALUES (%s, %s, %s)
                """, (paper_id, title, abstract))

            # 6. 处理章节
            for idx, block in enumerate(content_blocks):
                section_name = block['section_name'] or f"section_{idx}"
                section_content = block['section_content']

                # 生成章节向量
                content_vector = None
                if section_content and len(section_content.strip()) > 0:
                    truncated_section = truncate_text_for_bert(section_content)
                    content_vector = self.bert_generator.generate_embedding(truncated_section)

                if content_vector is not None:
                    cursor.execute("""
                        INSERT INTO paper_sections (paper_id, section_name, section_content, content_vector)
                        VALUES (%s, %s, %s, %s)
                    """, (paper_id, section_name, section_content, content_vector))
                else:
                    cursor.execute("""
                        INSERT INTO paper_sections (paper_id, section_name, section_content)
                        VALUES (%s, %s, %s)
                    """, (paper_id, section_name, section_content))

            conn.commit()
            cursor.close()

            print(f"[ok] 论文处理完成，paper_id: {paper_id}")
            return paper_id

        except Exception as e:
            if conn:
                conn.rollback()
            print(f"[err] 处理论文失败: {e}")
            # 不得返回随机 UUID：该 ID 不在 papers 表中，会导致各 Agent 写 agent_audits 外键失败
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def get_paper_info(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """根据paper_id获取论文信息"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT paper_id, title, abstract
                FROM papers
                WHERE paper_id = %s
            """, (paper_id,))

            result = cursor.fetchone()
            cursor.close()

            if result:
                return {
                    'paper_id': result[0],
                    'title': result[1],
                    'abstract': result[2],
                }
            return None

        except Exception as e:
            print(f"获取论文信息失败: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self):
        """关闭连接"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._bert_generator is not None:
            try:
                del self._bert_generator
            except Exception:
                pass
            self._bert_generator = None

    def __del__(self):
        self.close()
