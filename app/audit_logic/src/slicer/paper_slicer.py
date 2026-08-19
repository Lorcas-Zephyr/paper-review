import re, os, json

class PaperSlicer:
    """论文切片模块，负责将论文内容分割成有意义的切片"""

    def __init__(self, max_slice_length=200):
        self.max_slice_length = max_slice_length

    def read_paper(self, paper_path):
        """读取论文内容"""
        with open(paper_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content

    def extract_content(self, content):
        """提取摘要和正文内容，排除目录、参考文献等"""
        # 提取摘要部分
        abstract_match = re.search(r'# 摘\s*要\n\n(.*?)(?=\n# Abstract|\n# 目\s*录|\n关键词：)', content, re.DOTALL)
        abstract = abstract_match.group(1).strip() if abstract_match else ""
        # 提取正文部分（从第一章开始到参考文献之前）
        body_match = re.search(r'# 1 绪论.*?(?=\n# 参考文献|\n致谢)', content, re.DOTALL)
        body = body_match.group(0).strip() if body_match else ""
        # 常见 MD 标题变体（第一章 / 绪论 / 引言）
        if not body.strip():
            body_match = re.search(
                r'(?:^|\n)#\s*(?:第\s*[一二三四五六七八九十百千万零〇两]+\s*章|第\s*\d+\s*章|'
                r'1[\s、．.\-]+绪论|绪\s*论|引\s*言|Introduction)\b.*',
                content,
                re.DOTALL | re.IGNORECASE,
            )
            body = body_match.group(0).strip() if body_match else ""
        # 仍无法切出「正文」时回退为整篇，避免语义建模只吃到摘要、丢失大部内容
        if not body.strip() and (content or "").strip():
            body = content.strip()
        return abstract, body

    def clean_content(self, text):
        """清理文本，移除表格、图片、公式等无意义内容"""
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)  # 移除图片
        text = re.sub(r'<table>.*?</table>', '', text, flags=re.DOTALL)  # 移除表格
        text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.DOTALL)  # 移除块公式
        text = re.sub(r'\$.*?\$', '', text)  # 移除行内公式
        text = re.sub(r'\s+', ' ', text)  # 移除多余空白
        return text

    def split_into_sentences(self, text):
        """将文本分割成句子"""
        sentences = re.split(r'[。！？]\s*', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences

    def slice_content(self, sentences):
        """将句子切片成有意义的小段"""
        slices = []
        current_slice = []
        current_length = 0
        for sentence in sentences:
            sentence_length = len(sentence)
            # 如果当前切片超过最大长度，保存并开始新切片
            if current_length + sentence_length > self.max_slice_length:
                if current_slice:
                    slices.append(' '.join(current_slice))
                    current_slice = []
                    current_length = 0
            current_slice.append(sentence)
            current_length += sentence_length
        # 添加最后一个切片
        if current_slice:
            slices.append(' '.join(current_slice))
        return slices

    def _identify_proposition_type(self, sentence):
        """基于语言逻辑识别命题类型"""
        sentence_lower = sentence.lower()
        # 检查核心声明关键词
        for keyword in self.type_keywords.get("core_claim", []):
            if keyword in sentence_lower:
                return "core_claim"
        # 检查证据关键词
        for keyword in self.type_keywords.get("evidence", []):
            if keyword in sentence_lower:
                return "evidence"
        # 检查子声明关键词
        for keyword in self.type_keywords.get("sub_claim", []):
            if keyword in sentence_lower:
                return "sub_claim"
        return "sub_claim"

    def process_paper(self, paper_path):
        """处理论文并生成切片"""
        content = self.read_paper(paper_path)
        abstract, body = self.extract_content(content)
        clean_abstract = self.clean_content(abstract)
        clean_body = self.clean_content(body)
        abstract_sentences = self.split_into_sentences(clean_abstract)
        body_sentences = self.split_into_sentences(clean_body)
        abstract_slices = self.slice_content(abstract_sentences)
        body_slices = self.slice_content(body_sentences)
        result = {
            "abstract": clean_abstract,
            "abstract_slices": abstract_slices,
            "body_slices": body_slices,
            "abstract_sentences": abstract_sentences,
            "body_sentences": body_sentences,
            "total_abstract_slices": len(abstract_slices),
            "total_body_slices": len(body_slices)
        }
        return result

    def read_paper_from_db(self, paper_id: str, db_connector):
        """从数据库读取论文内容"""
        paper_data = db_connector.get_paper_by_id(paper_id)
        if not paper_data:
            raise ValueError(f"论文 ID {paper_id} 不存在")
        return paper_data["content"]

    def process_content(self, content: str):
        """处理字符串形式的论文内容"""
        abstract, body = self.extract_content(content)
        clean_abstract = self.clean_content(abstract)
        clean_body = self.clean_content(body)
        abstract_sentences = self.split_into_sentences(clean_abstract)
        body_sentences = self.split_into_sentences(clean_body)
        abstract_slices = self.slice_content(abstract_sentences)
        body_slices = self.slice_content(body_sentences)
        result = {
            "abstract": clean_abstract,
            "abstract_slices": abstract_slices,
            "body_slices": body_slices,
            "abstract_sentences": abstract_sentences,
            "body_sentences": body_sentences,
            "total_abstract_slices": len(abstract_slices),
            "total_body_slices": len(body_slices)
        }
        return result

    def generate_semantic_modeling_input(self, paper_path):
        """生成语义建模的输入格式"""
        result = self.process_paper(paper_path)
        return self._generate_semantic_input_from_result(result)

    def generate_semantic_modeling_input_from_content(self, content: str):
        """从字符串内容生成语义建模输入"""
        result = self.process_content(content)
        return self._generate_semantic_input_from_result(result)

    def _generate_semantic_input_from_result(self, result):
        """从处理结果生成语义建模输入"""
        semantic_input = {
            "abstract": {
                "propositions": [],
                "length": len(result["abstract"])
            },
            "chunks": []
        }

        # 添加摘要命题
        abstract_proposition_count = len(result["abstract_sentences"])
        for i, sentence in enumerate(result["abstract_sentences"], 1):
            semantic_input["abstract"]["propositions"].append({
                "prop_id": f"prop_{i}",
                "content": sentence,
                "type": "evidence" if i == 1 else "core_claim" if i == 2 else "sub_claim",
                "position": f"abstract:line_{i}",
                "global_id": f"abstract_{i}"
            })

        # 基于语言逻辑的命题类型识别关键词
        self.type_keywords = {
            "core_claim": [
                "提出", "认为", "表明", "发现", "证明", "显示", "说明", "指出", "阐述", "论证", "主张", "断言", "宣称", "确认", "验证", "揭示",
                "提出了", "认为是", "表明了", "发现了", "证明了", "显示出", "说明了", "指出了", "阐述了", "论证了", "主张了", "断言了", "宣称了", "确认了", "验证了", "揭示了",
                "证实", "确认", "验证", "支持", "证实了", "确认了", "验证了", "支持了"
            ],
            "evidence": [
                "数据显示", "实验表明", "根据", "基于", "通过", "研究发现", "结果显示", "实验结果", "数据分析", "统计显示", "调查结果", "案例表明", "事实证明", "实践证明", "临床研究", "现场测试",
                "数据表明", "实验结果表明", "根据研究", "基于数据", "通过实验", "研究结果显示", "数据分析表明", "统计结果显示", "调查数据显示", "案例研究表明", "事实证明了", "实践验证", "临床实验", "现场实验",
                "通过分析", "通过研究", "根据分析", "基于研究", "实验验证", "数据验证", "统计验证", "调查验证"
            ],
            "sub_claim": [
                "此外", "另外", "同时", "而且", "并且", "不仅", "而且", "除了", "之外", "还有", "同时也", "另外还", "此外还", "除此之外还", "同样", "与此同时",
                "然而", "但是", "可是", "不过", "却", "反而", "截然不同", "完全相反",
                "因此", "所以", "因而", "从而", "导致", "造成", "使得", "结果", "结果是", "导致了", "造成了", "使得", "结果是", "引起", "引发", "促使", "带来", "产生"
            ]
        }

        # 添加正文切片
        start_id = abstract_proposition_count + 1
        for i, slice_text in enumerate(result["body_slices"], 1):
            chunk = {
                "request_id": f"request_{i}",
                "metadata": {
                    "paper_id": "2018203215",
                    "paper_title": "基于混合式设备指纹的物联网身份认证机制",
                    "chunk_id": f"chunk_{i}",
                    "structure": "discussion",
                    "length": len(slice_text),
                    "hierarchy_level": 2,
                    "is_title": False,
                    "timestamp": "2026-02-12"
                },
                "propositions": [],
                "relations": []
            }
            # 分割切片内的句子并生成命题
            slice_sentences = self.split_into_sentences(slice_text)
            for j, sentence in enumerate(slice_sentences):
                prop_id = start_id + j
                prop_type = self._identify_proposition_type(sentence)
                chunk["propositions"].append({
                    "prop_id": f"prop_{prop_id}",
                    "content": sentence,
                    "type": prop_type,
                    "position": f"chunk_{i}:line_{j+1}",
                    "global_id": f"body_{prop_id}"
                })
            start_id += len(slice_sentences)
            semantic_input["chunks"].append(chunk)

        return semantic_input
