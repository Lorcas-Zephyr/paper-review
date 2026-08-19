import json

from member_b.member_b_module import run_dedup, run_evidence_validation
from member_b.models import DedupItem, DedupRequest, EvidenceItem, EvidenceValidationRequest, Section


def demo_evidence():
    sections = [
        Section(section_id="3.2", title="方法", paragraphs=["我们在数据集Y上采用方法X提升12%。"])
    ]
    items = [
        EvidenceItem(
            item_id="item-001",
            agent="method_agent",
            claim_text="方法X在数据集Y上提升12%",
            quote=None,
        )
    ]
    req = EvidenceValidationRequest(
        paper_id="paper-001",
        paper_field="软件工程",
        sections=sections,
        items=items,
        config={"semantic_threshold": 0.86},
    )
    res = run_evidence_validation(req)
    print(json.dumps([r.__dict__ for r in res], ensure_ascii=True, indent=2))


def demo_dedup():
    items = [
        DedupItem(item_id="i1", agent="a", text="补充实验设置细节以增强可复现性"),
        DedupItem(item_id="i2", agent="b", text="建议补充实验细节提升可复现性"),
        DedupItem(item_id="i3", agent="c", text="请补充相关工作综述"),
    ]
    req = DedupRequest(paper_id="paper-001", items=items, config={"eps": 0.16})
    res = run_dedup(req)
    print(json.dumps(res.__dict__, default=lambda o: o.__dict__, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    demo_evidence()
    demo_dedup()
