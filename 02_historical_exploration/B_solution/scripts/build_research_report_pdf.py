"""Render a NEW research-report PDF using the existing embedded-font builder.

Input and output are mandatory. Existing PDF/build-report files are never
overwritten, and the earlier paper's PDF and QA files remain untouched.
Run with the bundled Python containing reportlab; --check creates no PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def builder_module():
    spec = importlib.util.spec_from_file_location("independent_report_builder", ROOT/"scripts/build_paper_pdf.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Additional elementary functions needed by the new mechanism proofs.
    # This affects only this separately loaded reporting module, never a solver.
    module.COMMANDS.update(arcsin="arcsin", arccos="arccos", rm="")
    return module


def protected_output(output):
    output = Path(output).resolve()
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output must have a .pdf extension")
    if output == (ROOT/"output/pdf/B题研究论文.pdf").resolve():
        raise ValueError("The previous paper PDF filename is reserved")
    report = output.with_suffix(".build.json")
    if output.exists() or report.exists():
        raise FileExistsError("Preserve existing artifact: choose a new PDF filename")
    return output, report


def build(source, output, *, check=False, render=False, font_dir=Path("C:/Windows/Fonts"), body_size=11.):
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError("Report source does not yet exist")
    output, report_path = protected_output(output)
    if not 9.5 <= body_size <= 13:
        raise ValueError("Body font size must be 9.5..13 pt")
    builder = builder_module()
    blocks = builder.parse_markdown(source.read_text(encoding="utf-8-sig"))
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), source.stem)
    def draw_report_page(canvas, document):
        canvas.saveState()
        canvas.setFillColor(builder.colors.HexColor("#666666"))
        canvas.setFont("PaperSong", 8)
        if document.page > 1:
            header = title
            available = builder.PAGE_WIDTH-2*builder.LEFT_MARGIN
            while canvas.stringWidth(header, "PaperSong", 8) > available:
                header = header[:-2]+"…"
            canvas.drawString(builder.LEFT_MARGIN, builder.PAGE_HEIGHT-30, header)
        canvas.setFillColor(builder.colors.HexColor("#555555"))
        canvas.setFont("PaperSong", 9)
        canvas.drawCentredString(builder.PAGE_WIDTH/2, 25, str(document.page))
        canvas.restoreState()
    builder.draw_page = draw_report_page
    if check:
        fonts = builder.register_fonts(Path(font_dir))
        story, report = builder.build_story(blocks, source.parent, body_size=body_size)
        report = builder.check_layout(story, report)
        return {**report, "fonts": fonts, "pdf_created": False, "source_sha256": sha(source)}
    # Isolate the original builder's hard-coded tmp/pdfs/paper_build_report.json.
    # No temporary file or QA record under the first paper's path is replaced.
    workspace = ROOT/"tmp/pdf_report_builds"/(output.stem+"_"+uuid.uuid4().hex)
    workspace.mkdir(parents=True)
    builder.ROOT = workspace
    staged = workspace/"new_report.pdf"
    report = builder.generate_pdf(source, staged, body_size=body_size, min_pages=1, font_dir=Path(font_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    os.link(staged, output)  # Exclusive installation: fails if another writer won.
    report.update(output=str(output), pdf_sha256=sha(output), source_sha256=sha(source),
                  report_title=title,
                  builder_sha256=sha(ROOT/"scripts/build_paper_pdf.py"), wrapper_sha256=sha(__file__),
                  old_paper_unchanged=True, render_paths=[])
    if render:
        bundled = Path(os.environ.get("USERPROFILE", "C:/Users/Expedition"))/".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
        executable = str(bundled) if bundled.is_file() else shutil.which("pdftoppm")
        if not executable:
            raise RuntimeError("PDF built; Poppler unavailable for required visual rendering")
        pages = workspace/"rendered_pages"
        pages.mkdir()
        subprocess.run([executable, "-png", "-r", "110", str(output), str(pages/"page")], check=True)
        report["render_paths"] = [str(p) for p in sorted(pages.glob("*.png"))]
        report["qa_status"] = "PDF rendered; each page awaits human/model visual inspection"
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--font-dir", type=Path, default=Path("C:/Windows/Fonts"))
    parser.add_argument("--body-size", type=float, default=11.)
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.output, check=args.check, render=args.render,
                           font_dir=args.font_dir, body_size=args.body_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
