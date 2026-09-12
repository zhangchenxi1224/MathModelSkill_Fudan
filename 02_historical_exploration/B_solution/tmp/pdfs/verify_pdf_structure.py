"""Record structural checks alongside the separately completed visual review."""
from pathlib import Path
import hashlib
import json
from pypdf import PdfReader

root = Path(__file__).resolve().parents[2]
source = root / "docs" / "paper.md"
pdf = root / "output" / "pdf" / "B题研究论文.pdf"
reader = PdfReader(str(pdf))
fonts = {}
page_text_lengths = []
links = 0
for page in reader.pages:
    text = page.extract_text() or ""
    assert text.strip(), "Empty PDF page"
    assert "\ufffd" not in text, "Replacement character in PDF text"
    page_text_lengths.append(len(text))
    for reference in page.get("/Annots", []):
        if reference.get_object().get("/Subtype") == "/Link":
            links += 1
    resources = page["/Resources"].get_object()
    for reference in resources.get("/Font", {}).get_object().values():
        font = reference.get_object()
        descriptor_ref = font.get("/FontDescriptor")
        descriptor = descriptor_ref.get_object() if descriptor_ref else {}
        embedded = any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))
        name = str(font.get("/BaseFont"))
        # ReportLab may register unused Helvetica. All actually used Chinese,
        # Latin and mathematical TrueType fonts must be embedded.
        fonts[name] = {"subtype": str(font.get("/Subtype")), "embedded": embedded}
        if font.get("/Subtype") == "/TrueType":
            assert embedded, f"Unembedded TrueType font: {name}"

assert len(reader.pages) >= 15
assert source.stat().st_mtime <= pdf.stat().st_mtime, "Source changed after PDF build"
result = {
    "pdf": str(pdf), "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
    "pages": len(reader.pages), "pdf_bytes": pdf.stat().st_size,
    "page_text_lengths": page_text_lengths,
    "embedded_fonts": fonts, "clickable_links": links,
    "visual_review": {
        "status": "passed",
        "inspected_pages": list(range(1, len(reader.pages) + 1)),
        "render": "pdftoppm -r 110 -png",
        "directory": str(root / "tmp" / "pdfs" / "paper_pages"),
        "findings": "All pages visually inspected individually. Chinese, Unicode math, 10 tables and 9 figures readable; no clipping or overlap. Consecutive section-heading pagination corrected and all pages re-reviewed. Final short appendix page is natural pagination, with no filler pages.",
    },
}
destination = root / "tmp" / "pdfs" / "paper_visual_qa.json"
destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
build_record = root / "tmp" / "pdfs" / "paper_build_report.json"
build = json.loads(build_record.read_text(encoding="utf-8"))
assert build["source_sha256"] == result["source_sha256"]
assert build["pdf_sha256"] == result["pdf_sha256"]
build["qa_status"] = "Passed: every final PDF page rendered and visually inspected; see paper_visual_qa.json"
build["visual_qa_record"] = str(destination)
build_record.write_text(json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: result[key] for key in ("pages", "pdf_bytes", "source_sha256", "pdf_sha256", "embedded_fonts", "clickable_links")}, ensure_ascii=False, indent=2))
