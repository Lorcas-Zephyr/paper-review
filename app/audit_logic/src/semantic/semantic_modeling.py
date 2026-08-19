import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from local_model_config import HF_HUB_DIR, MODEL_IDS, enable_offline_model_mode, require_local_model

enable_offline_model_mode()

import re, networkx as nx
from typing import List, Dict, Any, Optional
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch

class SemanticModeling:
    """语义建模组件，负责命题提取和关系识别"""

    def __init__(self):
        # 中英文关键词词典
        self.chinese_keywords = {
            "core_claim": ["提出", "认为", "表明", "发现", "证明", "显示", "说明", "指出", "阐述", "论证", "主张", "断言", "宣称", "确认", "验证", "揭示", "提出了", "认为是", "表明了", "发现了", "证明了", "显示出", "说明了", "指出了", "阐述了", "论证了", "主张了", "断言了", "宣称了", "确认了", "验证了", "揭示了", "证实", "确认", "验证", "支持", "证实了", "确认了", "验证了", "支持了"],
            "evidence": ["数据显示", "实验表明", "根据", "基于", "通过", "研究发现", "结果显示", "实验结果", "数据分析", "统计显示", "调查结果", "案例表明", "事实证明", "实践证明", "临床研究", "现场测试", "数据表明", "实验结果表明", "根据研究", "基于数据", "通过实验", "研究结果显示", "数据分析表明", "统计结果显示", "调查数据显示", "案例研究表明", "事实证明了", "实践验证", "临床实验", "现场实验", "通过分析", "通过研究", "根据分析", "基于研究", "实验验证", "数据验证", "统计验证", "调查验证"],
            "entailment": ["表明", "说明", "意味着", "体现", "反映", "暗示", "预示", "代表", "象征", "因此", "所以", "因而", "从而", "导致", "造成", "使得", "结果", "结果是", "导致了", "造成了", "使得", "结果是", "引起", "引发", "促使", "使得", "带来", "产生", "造成了", "导致了", "引起了", "引发了", "促使了", "带来了", "产生了", "由此可见", "据此可知", "综上所述", "基于此", "由此得出", "因此可以", "所以能够", "因而可以", "从而可以", "据此可以", "由此能够", "因此能够", "基于以上分析", "基于上述研究", "根据以上结果", "由此推断", "据此推断", "因此推断", "所以推断", "因而推断", "从而推断", "据此可得", "由此可得", "因此可得", "所以可得", "因而可得", "从而可得", "进一步", "更进一步", "更重要的是", "值得注意的是", "特别值得注意的是", "尤其重要的是", "更关键的是", "更重要的", "值得注意的", "特别值得注意的", "尤其重要的", "更关键的", "更进一步的是", "更为重要的是", "尤为重要的是", "更为关键的是", "尤为关键的是", "总之", "总而言之", "综上所述", "概括来说", "归纳起来", "总的来说", "总体而言", "总的来看", "概括地说", "归纳地说", "总的来讲", "总体来说", "总体上看", "综上所述，", "总的来说，", "总体而言，", "概括来说，", "如果", "假如", "假设", "要是", "若", "只要", "只有", "除非", "如果是", "假如是", "假设是", "要是是", "若是", "只要是", "只有是", "除非是", "如果...那么", "假如...那么", "假设...那么", "要是...那么", "若...则", "只要...就", "只有...才", "除非...否则", "为了", "以便", "以求", "目的是", "旨在", "为了", "以便于", "以求", "目的在于", "旨在", "为的是", "为了", "以便于", "以求", "目的在于", "旨在", "为的是"],
            "contradiction": ["不是", "没有", "并非", "并不", "不", "未", "无", "非", "没", "未", "无", "非", "没", "不会", "不能", "不可", "不是", "没有", "并非", "并不", "不", "未", "无", "非", "没", "不会", "不能", "不可", "相反", "然而", "但是", "却", "而", "反而", "截然不同", "完全相反", "不过", "可是", "只是", "但", "然而", "可是", "不过", "只是", "但", "然而", "可是", "不过", "只是", "但", "然而", "可是", "不过", "只是", "但", "然而", "可是", "不过", "只是", "但", "不同于", "区别于", "有别于", "不同于", "区别于", "有别于", "与...不同", "与...有别", "不同于", "区别于", "有别于", "与...不同", "与...有别", "不同于", "区别于", "有别于", "与...不同", "与...有别", "反对", "反驳", "否定", "质疑", "挑战", "反对", "反驳", "否定", "质疑", "挑战", "反对", "反驳", "否定", "质疑", "挑战", "反对", "反驳", "否定", "质疑", "挑战"],
            "neutral": ["同时", "另外", "此外", "除此之外", "同样", "与此同时", "同理", "相应地", "类似地", "与此类似", "同时也", "另外还", "此外还", "除此之外还", "同样地", "与此同时也", "同时", "另外", "此外", "除此之外", "同样", "与此同时", "同理", "相应地", "类似地", "与此类似", "同时也", "另外还", "此外还", "除此之外还", "同样地", "与此同时也", "另外", "此外", "除此之外", "再者", "另外一方面", "此外还有", "除此之外还有", "再者", "另外一方面", "此外还有", "除此之外还有", "另外", "此外", "除此之外", "再者", "另外一方面", "此外还有", "除此之外还有", "再者", "另外一方面", "此外还有", "除此之外还有", "并且", "而且", "和", "与", "及", "以及", "并", "且", "和", "与", "及", "以及", "并", "且", "和", "与", "及", "以及", "并", "且"],
            "paper_structure": {
                "abstract": ["摘 要", "摘要", "摘要：", "摘 要："],
                "introduction": ["引言", "前言", "绪论", "引言部分", "前言部分", "绪论部分", "1. 引言", "1. 前言", "1. 绪论"],
                "method": ["方法", "实验方法", "研究方法", "方法论", "方法部分", "实验方法部分", "研究方法部分", "2. 方法", "2. 实验方法", "2. 研究方法"],
                "results": ["结果", "实验结果", "研究结果", "结果部分", "实验结果部分", "研究结果部分", "3. 结果", "3. 实验结果", "3. 研究结果"],
                "discussion": ["讨论", "分析", "讨论部分", "分析部分", "4. 讨论", "4. 分析"],
                "conclusion": ["结论", "总结", "结论部分", "总结部分", "5. 结论", "5. 总结"]
            },
            "domain_specific": {
                "computer_science": ["算法", "模型", "系统", "方法", "技术", "框架", "架构", "协议", "标准", "规范", "算法", "模型", "系统", "方法", "技术", "框架", "架构", "协议", "标准", "规范"],
                "medicine": ["临床", "病例", "治疗", "诊断", "症状", "病因", "病理", "生理", "生化", "免疫", "临床", "病例", "治疗", "诊断", "症状", "病因", "病理", "生理", "生化", "免疫"],
                "physics": ["定理", "定律", "原理", "公式", "方程", "模型", "理论", "实验", "观测", "数据", "定理", "定律", "原理", "公式", "方程", "模型", "理论", "实验", "观测", "数据"],
                "economics": ["理论", "模型", "数据", "分析", "指标", "指数", "政策", "效应", "影响", "趋势", "理论", "模型", "数据", "分析", "指标", "指数", "政策", "效应", "影响", "趋势"]
            }
        }
        self.english_keywords = {
            "core_claim": ["propose", "suggest", "indicate", "find", "prove", "show", "demonstrate", "argue", "state", "assert", "claim", "affirm", "confirm", "verify", "reveal"],
            "evidence": ["data show", "experiment indicates", "according to", "based on", "through", "research finds", "results show", "experimental results", "data analysis", "statistics show", "survey results", "case study", "facts prove", "practice shows", "clinical research", "field test"],
            "entailment": ["indicates", "suggests", "implies", "shows", "demonstrates", "signifies", "represents", "symbolizes", "means", "therefore", "thus", "hence", "consequently", "result in", "lead to"],
            "contradiction": ["not", "never", "no", "none", "instead", "however", "but", "yet", "whereas", "conversely", "on the contrary", "in contrast"],
            "neutral": ["meanwhile", "simultaneously", "also", "likewise", "similarly", "correspondingly", "in the same way", "equally"]
        }
        # 多层次关系词体系
        self.relation_hierarchy = {
            "entailment": {
                "causal": ["导致", "造成", "使得", "结果", "因此", "所以", "因而", "从而", "引起", "引发", "促使", "带来", "产生"],
                "deductive": ["由此可见", "据此可知", "综上所述", "基于此", "由此得出", "因此可以", "所以能够", "基于以上分析", "根据以上结果"],
                "progressive": ["进一步", "更进一步", "更重要的是", "值得注意的是", "特别值得注意的是", "尤其重要的是", "更关键的是"],
                "summative": ["总之", "总而言之", "综上所述", "概括来说", "归纳起来", "总的来说", "总体而言", "总的来看"],
                "conditional": ["如果", "假如", "假设", "要是", "若", "只要", "只有", "除非"],
                "purposive": ["为了", "以便", "以求", "目的是", "旨在", "为的是"]
            },
            "contradiction": {
                "negation": ["不是", "没有", "并非", "并不", "不", "未", "无", "非", "没", "不会", "不能", "不可"],
                "转折": ["相反", "然而", "但是", "却", "而", "反而", "截然不同", "完全相反", "不过", "可是", "只是"],
                "contrast": ["不同于", "区别于", "有别于", "与...不同", "与...有别"],
                "opposition": ["反对", "反驳", "否定", "质疑", "挑战"]
            },
            "neutral": {
                "coordinate": ["同时", "另外", "此外", "除此之外", "同样", "与此同时", "同理", "相应地", "类似地", "与此类似", "并且", "而且", "和", "与", "及", "以及", "并", "且"],
                "supplementary": ["另外", "此外", "除此之外", "再者", "另外一方面", "此外还有", "除此之外还有"]
            }
        }

        # 初始化NLI模型
        self.nli_model = None
        self.tokenizer = None
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
            print(f"CUDA is available, using GPU: {torch.cuda.get_device_name(0)}")
            print(f"CUDA memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f} GB")
        else:
            self.device = torch.device("cpu")
            print("CUDA is not available, using CPU")

        # 推理结果缓存
        self.inference_cache = {}

        # 加载预训练模型
        try:
            project_cache_dir = str(HF_HUB_DIR)
            print(f"Using project cache directory: {project_cache_dir}")
            print(f"Cache directory exists: {os.path.exists(project_cache_dir)}")
            print(f"Cache directory contents: {os.listdir(project_cache_dir) if os.path.exists(project_cache_dir) else 'None'}")

            model_name = MODEL_IDS["logic_nli"]
            local_model_path = require_local_model(model_name)
            print(f"Trying to load best NLI model: {model_name}")

            # 尝试从缓存加载tokenizer
            try:
                print("Trying to load tokenizer from cache...")
                self.tokenizer = AutoTokenizer.from_pretrained(local_model_path, local_files_only=True)
                print("Tokenizer loaded from cache successfully")
            except Exception as e:
                print(f"Failed to load tokenizer from cache: {e}")
                print("Retrying tokenizer from local cache...")
                self.tokenizer = AutoTokenizer.from_pretrained(local_model_path, local_files_only=True)
                print("Tokenizer loaded successfully")

            # 尝试从缓存加载模型
            try:
                print("Trying to load model from cache...")
                self.nli_model = AutoModelForSequenceClassification.from_pretrained(local_model_path, num_labels=3, local_files_only=True)
                print("Model loaded from cache successfully")
            except Exception as e:
                print(f"Failed to load model from cache: {e}")
                print("Retrying model from local cache...")
                self.nli_model = AutoModelForSequenceClassification.from_pretrained(local_model_path, num_labels=3, local_files_only=True)
                print("Model loaded successfully")

            # 将模型移至指定设备
            self.nli_model.to(self.device)
            if self.device.type == "cuda":
                print(f"Model {model_name} loaded successfully on GPU")
                print(f"CUDA device: {torch.cuda.get_device_name(0)}")
                print(f"CUDA memory allocated: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")
                print(f"CUDA memory total: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f} GB")
            else:
                print(f"Model {model_name} loaded successfully on CPU")
        except Exception as e:
            print(f"Failed to load NLI model: {e}")
            print("Trying fallback model...")
            # 尝试备选模型
            try:
                model_name = MODEL_IDS["logic_fallback"]
                local_model_path = require_local_model(model_name)
                print(f"Trying to load fallback model: {model_name}")
                self.tokenizer = AutoTokenizer.from_pretrained(local_model_path, local_files_only=True)
                print("Fallback tokenizer loaded successfully")
                self.nli_model = AutoModelForSequenceClassification.from_pretrained(local_model_path, num_labels=3, local_files_only=True, ignore_mismatched_sizes=True)
                print("Fallback model loaded successfully")
                self.nli_model.to(self.device)
                if self.device.type == "cuda":
                    print(f"Fallback model loaded on GPU")
                    print(f"CUDA device: {torch.cuda.get_device_name(0)}")
                    print(f"CUDA memory allocated: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")
                else:
                    print(f"Fallback model loaded on CPU")
            except Exception as fallback_e:
                print(f"Failed to load fallback model: {fallback_e}")
                print("\n=== 解决方案 ===")
                print("1. 检查网络连接是否正常")
                print("2. 确保可以访问 https://hf-mirror.com")
                print("3. 继续使用增强的规则-based NLI系统")
                print("\nFalling back to enhanced rule-based NLI")
                print("Enhancing rule-based NLI with additional semantic patterns...")

    def clean_text(self, text: str) -> str:
        """清洗文本，去除多余空格"""
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def split_sentences(self, text: str) -> list:
        """中英文分句"""
        sentences = re.split(r'[。！？；.!?]', text)
        return [s.strip() for s in sentences if s.strip()]

    def calculate_semantic_similarity(self, text1: str, text2: str) -> float:
        """计算文本相似度（基于词袋模型）"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        # 移除停用词
        stop_words = {'的', '了', '是', '在', '有', '和', '我', '你', '他', '她', '它', '们', '这', '那', '个', '我们', '你们', '他们', '她', '它', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        words1 = words1 - stop_words
        words2 = words2 - stop_words
        if not words1 and not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def model_nli_inference(self, premise: str, hypothesis: str) -> tuple:
        """使用预训练模型执行自然语言推理"""
        if not self.nli_model or not self.tokenizer:
            return "neutral", 0.8

        # 检查缓存
        cache_key = f"{premise}|||{hypothesis}"
        if cache_key in self.inference_cache:
            return self.inference_cache[cache_key]

        try:
            # 编码输入
            inputs = self.tokenizer(premise, hypothesis, return_tensors="pt", truncation=True, padding=True, max_length=512)
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            if next(self.nli_model.parameters()).device != self.device:
                self.nli_model.to(self.device)

            # 模型推理
            with torch.no_grad():
                outputs = self.nli_model(**inputs)
                logits = outputs.logits
                probs = torch.softmax(logits, dim=1)
                pred_class = torch.argmax(probs, dim=1).item()
                confidence = probs[0][pred_class].item()

            label_map = {0: "entailment", 1: "neutral", 2: "contradiction"}
            relation = label_map.get(pred_class, "neutral")
            self.inference_cache[cache_key] = (relation, confidence)
            return relation, confidence
        except Exception as e:
            print(f"NLI inference error: {e}")
            return "neutral", 0.5

    def rule_based_nli(self, premise: str, hypothesis: str) -> tuple:
        """基于规则的NLI推理（备选方案）"""
        premise_lower = premise.lower()
        hypothesis_lower = hypothesis.lower()

        # 检查矛盾关系
        for keyword in self.chinese_keywords.get("contradiction", []):
            if keyword in hypothesis_lower and keyword not in premise_lower:
                return "contradiction", 0.7

        # 检查蕴含关系
        for keyword in self.chinese_keywords.get("entailment", []):
            if keyword in hypothesis_lower:
                return "entailment", 0.7

        # 基于相似度判断
        similarity = self.calculate_semantic_similarity(premise, hypothesis)
        if similarity > 0.5:
            return "entailment", similarity
        elif similarity < 0.2:
            return "neutral", 0.6
        return "neutral", 0.5

    def extract_propositions(self, text: str, position: str = "unknown") -> List[Dict[str, Any]]:
        """从文本中提取命题"""
        propositions = []
        sentences = self.split_sentences(text)
        for i, sentence in enumerate(sentences):
            if len(sentence.strip()) < 5:
                continue
            prop_type = self._classify_proposition_type(sentence)
            prop = {
                "prop_id": f"prop_{len(propositions) + 1}",
                "content": sentence,
                "type": prop_type,
                "position": f"{position}:sentence_{i+1}"
            }
            propositions.append(prop)
        return propositions

    def _classify_proposition_type(self, sentence: str) -> str:
        """分类命题类型"""
        sentence_lower = sentence.lower()
        for keyword in self.chinese_keywords.get("core_claim", []):
            if keyword in sentence_lower:
                return "core_claim"
        for keyword in self.chinese_keywords.get("evidence", []):
            if keyword in sentence_lower:
                return "evidence"
        return "sub_claim"

    def identify_semantic_relations(self, propositions: List[Dict[str, Any]], context_window: int = 5) -> List[Dict[str, Any]]:
        """识别命题间的语义关系"""
        relations = []
        n = len(propositions)
        for i in range(n):
            for j in range(i + 1, min(i + context_window + 1, n)):
                prop_i = propositions[i]
                prop_j = propositions[j]
                # 优先使用模型推理，否则使用规则
                if self.nli_model and self.tokenizer:
                    relation, confidence = self.model_nli_inference(prop_i["content"], prop_j["content"])
                else:
                    relation, confidence = self.rule_based_nli(prop_i["content"], prop_j["content"])
                if relation != "neutral" or confidence > 0.6:
                    relations.append({
                        "source": prop_i["prop_id"],
                        "target": prop_j["prop_id"],
                        "relation_type": relation,
                        "confidence": round(confidence, 3)
                    })
        return relations

    def build_semantic_graph(self, propositions: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> nx.DiGraph:
        """构建语义关系图"""
        G = nx.DiGraph()
        for prop in propositions:
            G.add_node(prop["prop_id"], content=prop["content"], type=prop["type"], position=prop.get("position", "unknown"))
        for rel in relations:
            G.add_edge(rel["source"], rel["target"], relation_type=rel["relation_type"], confidence=rel["confidence"])
        return G

    def analyze_proposition_structure(self, proposition: Dict[str, Any]) -> Dict[str, Any]:
        """分析命题结构"""
        content = proposition.get("content", "")
        structure = {
            "has_causal_marker": False,
            "has_evidence_marker": False,
            "has_claim_marker": False,
            "complexity_score": 0.0
        }
        # 检查因果标记
        for keyword in self.chinese_keywords.get("entailment", {}).get("causal", []):
            if keyword in content:
                structure["has_causal_marker"] = True
                break
        # 检查证据标记
        for keyword in self.chinese_keywords.get("evidence", []):
            if keyword in content:
                structure["has_evidence_marker"] = True
                break
        # 检查声明标记
        for keyword in self.chinese_keywords.get("core_claim", []):
            if keyword in content:
                structure["has_claim_marker"] = True
                break

        # 计算复杂度分数
        complexity = 0.0
        if structure["has_causal_marker"]:
            complexity += 0.3
        if structure["has_evidence_marker"]:
            complexity += 0.3
        if structure["has_claim_marker"]:
            complexity += 0.4
        structure["complexity_score"] = complexity
        return structure

    def get_proposition_importance(self, proposition: Dict[str, Any], graph: nx.DiGraph) -> float:
        """计算命题重要性（基于图结构）"""
        prop_id = proposition.get("prop_id")
        if prop_id not in graph:
            return 0.0
        in_degree = graph.in_degree(prop_id)
        out_degree = graph.out_degree(prop_id)
        importance = (in_degree * 0.6 + out_degree * 0.4) / max(len(graph.nodes()), 1)
        return round(importance, 4)

    def export_graph_to_json(self, graph: nx.DiGraph) -> Dict[str, Any]:
        """导出图结构为JSON格式"""
        nodes = []
        for node_id, node_data in graph.nodes(data=True):
            nodes.append({
                "id": node_id,
                "content": node_data.get("content", ""),
                "type": node_data.get("type", ""),
                "position": node_data.get("position", "")
            })
        edges = []
        for source, target, edge_data in graph.edges(data=True):
            edges.append({
                "source": source,
                "target": target,
                "relation": edge_data.get("relation_type", ""),
                "confidence": edge_data.get("confidence", 0.0)
            })
        return {"nodes": nodes, "edges": edges}

    def process_paper_chunk(self, chunk_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理论文切片"""
        content = chunk_data.get("content", "")
        context_before = chunk_data.get("context_before", "")
        context_after = chunk_data.get("context_after", "")
        abstract = chunk_data.get("abstract", "")

        # 提取命题
        propositions = self.extract_propositions(content, position=chunk_data.get("chunk_id", "unknown"))
        if abstract:
            abstract_props = self.extract_propositions(abstract, position="abstract")
            propositions.extend(abstract_props)

        # 识别关系并构建图
        relations = self.identify_semantic_relations(propositions)
        graph = self.build_semantic_graph(propositions, relations)

        return {
            "propositions": propositions,
            "relations": relations,
            "graph": self.export_graph_to_json(graph)
        }
