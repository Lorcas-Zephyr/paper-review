import asyncio
from pathlib import Path

from app.pdf_to_md import main


def test_parse_pdf_uses_local_mineru_and_collects_outputs(tmp_path, monkeypatch):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    output_root = tmp_path / "outputs"
    output_root.mkdir()

    def fake_do_parse(**kwargs):
        assert kwargs["backend"] == "pipeline"
        assert kwargs["parse_method"] == "auto"
        assert kwargs["pdf_file_names"] == ["sample.pdf"]
        generated = Path(kwargs["output_dir"]) / "sample" / "auto"
        generated.mkdir(parents=True)
        (generated / "sample.md").write_text("# Local result", encoding="utf-8")
        (generated / "sample_content_list.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(main, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(main, "_mineru_do_parse", fake_do_parse)

    result = asyncio.run(main.parse_pdf_enhanced(str(pdf_path)))

    assert result["success"] is True
    assert result["files"]["markdown"]["content"] == "# Local result"
    assert result["files"]["content_list"]["content"] == []


def test_health_reports_local_runtime(monkeypatch):
    monkeypatch.setattr(main, "MINERU_AVAILABLE", True)
    result = asyncio.run(main.health_check())

    assert result["status"] == "healthy"
    assert result["mineru"]["available"] is True
    assert "pdf_parse_api_status" not in result
