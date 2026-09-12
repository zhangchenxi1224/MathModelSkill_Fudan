"""Build the Chinese research-paper PDF from Markdown with embedded fonts.

Bundled runtime dependencies: reportlab and Pillow.  --self-test and --check
perform parsing, font and layout checks WITHOUT creating any PDF.  A real build
must be preceded by the task's artifact-operation marker when used in Codex.

This builder never changes the paper Markdown or invents content to reach a
minimum page count.  Markdown tables wrap at page width, repeat headers, and
split into clearly labelled column panels when too wide.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import html
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from typing import Iterable

from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether,
                               LongTable, PageBreak, PageTemplate, Paragraph, Spacer, TableStyle)


ROOT = Path(__file__).resolve().parents[1]
PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = RIGHT_MARGIN = 49.0
TOP_MARGIN = 53.0
BOTTOM_MARGIN = 47.0
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
CONTENT_HEIGHT = PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN
FONT_PATHS = {
    "PaperSong": ("simsun.ttc", 0),
    "PaperHei": ("simhei.ttf", 0),
    "PaperLatin": ("cambria.ttc", 0),
    "PaperSymbols": ("seguisym.ttf", 0),
    "PaperCode": ("consola.ttf", 0),
}


class PaperBuildError(RuntimeError):
    pass


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    path: str = ""
    line: int = 0


def register_fonts(font_dir: Path) -> dict[str, str]:
    registered = {}
    for name, (filename, index) in FONT_PATHS.items():
        path = font_dir / filename
        if not path.is_file():
            if name in {"PaperSong", "PaperHei"}:
                raise PaperBuildError(f"Required Chinese font is missing: {path}")
            continue
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
            # Explicit family avoids accidental Helvetica fallback for <b>/<i>.
            pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
        registered[name] = str(path)
    return registered


def normalize_typography(text: str) -> str:
    return (text.replace("\ufeff", "").replace("\r\n", "\n")
            .replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
            .replace("\u200b", "").replace("\u2060", ""))


def font_runs(text: str, base: str = "PaperSong") -> str:
    """Escape user text and select an embedded font containing every glyph."""
    if not text:
        return ""
    available = set(pdfmetrics.getRegisteredFontNames())
    stack = list(dict.fromkeys([base, "PaperSong", "PaperLatin", "PaperSymbols"]))
    stack = [font for font in stack if font in available]
    chunks, current, chars = [], None, []
    def flush():
        if chars:
            chunks.append(f'<font name="{current}">{html.escape("".join(chars))}</font>')
            chars.clear()
    for ch in normalize_typography(text):
        if ch == "\n":
            flush()
            chunks.append("<br/>")
            current = None
            continue
        if ch == "\t":
            ch = " "
        font = next((name for name in stack
                     if ord(ch) in pdfmetrics.getFont(name).face.charToGlyph), None)
        if font is None:
            raise PaperBuildError(f"No embedded font for U+{ord(ch):04X} {ch!r}; replace or supply a font")
        if font != current:
            flush()
            current = font
        chars.append(ch)
    flush()
    return "".join(chunks)


COMMANDS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "theta": "θ", "vartheta": "ϑ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "eta": "η",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩", "setminus": "∖",
    "forall": "∀", "exists": "∃", "emptyset": "∅", "varnothing": "∅",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "iint": "∬", "oint": "∮", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "mapsto": "↦",
    "ldots": "…", "cdots": "⋯", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "langle": "〈", "rangle": "〉", "lVert": "‖", "rVert": "‖", "Vert": "‖",
    "lvert": "|", "rvert": "|", "vert": "|", "ell": "ℓ", "circ": "°",
    "perp": "⊥", "parallel": "∥", "angle": "∠", "triangle": "△",
    "min": "min", "max": "max", "inf": "inf", "sup": "sup", "arg": "arg",
    "argmin": "argmin", "argmax": "argmax", "log": "log", "ln": "ln", "exp": "exp",
    "sin": "sin", "cos": "cos", "tan": "tan", "arctan": "arctan", "atan": "atan",
    "det": "det", "dim": "dim", "ker": "ker", "Pr": "Pr", "lim": "lim",
    "quad": "  ", "qquad": "    ", "enspace": " ", "neg": "¬", "land": "∧", "lor": "∨",
}
WRAPPERS = {"mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal", "operatorname", "text", "textrm", "textbf"}


def _group_at(text: str, start: int) -> tuple[str, int]:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        raise PaperBuildError("Incomplete formula group")
    if text[start] != "{":
        return text[start], start + 1
    depth, index = 1, start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    raise PaperBuildError("Unclosed formula group")


def tex_to_unicode(text: str) -> str:
    """Convert common small TeX expressions into readable linear Unicode math.

    Fractions intentionally become parenthesized ratios, not unrendered TeX.
    Unknown commands fail loudly.  Sub/superscripts are rendered later by the
    Paragraph engine.  Complex TeX should be rewritten in Unicode by the author.
    """
    text = normalize_typography(text.strip())
    text = re.sub(r"\\(?:begin|end)\{(?:aligned|align\*?|equation\*?|gathered)\}", "", text)
    text = text.replace("\\\\", "\n").replace("&", " ")
    output, i = [], 0
    while i < len(text):
        if text[i] != "\\":
            output.append(text[i])
            i += 1
            continue
        if i + 1 >= len(text):
            raise PaperBuildError("Trailing backslash in formula")
        match = re.match(r"\\([A-Za-z]+)", text[i:])
        if match is None:
            token = text[i + 1]
            output.append({"|": "‖", ",": " ", ";": " ", ":": " ", "!": "", " ": " "}.get(token, token))
            i += 2
            continue
        command = match.group(1)
        i += len(match.group(0))
        if command in {"left", "right", "displaystyle", "textstyle", "limits", "nolimits"}:
            continue
        if command in {"frac", "dfrac", "tfrac"}:
            numerator, i = _group_at(text, i)
            denominator, i = _group_at(text, i)
            output.append(f"({tex_to_unicode(numerator)})/({tex_to_unicode(denominator)})")
        elif command == "sqrt":
            if i < len(text) and text[i] == "[":
                end = text.find("]", i)
                if end < 0:
                    raise PaperBuildError("Unclosed root index")
                index = text[i + 1:end]
                i = end + 1
                argument, i = _group_at(text, i)
                output.append(f"({tex_to_unicode(argument)})^{{1/({tex_to_unicode(index)})}}")
            else:
                argument, i = _group_at(text, i)
                output.append(f"√({tex_to_unicode(argument)})")
        elif command in WRAPPERS:
            argument, i = _group_at(text, i)
            output.append(tex_to_unicode(argument))
        elif command == "mathbb":
            argument, i = _group_at(text, i)
            output.append({"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "E"}.get(argument, argument))
        elif command in {"overline", "bar", "hat", "widehat", "tilde", "widetilde", "vec"}:
            argument, i = _group_at(text, i)
            label = {"overline": "bar", "bar": "bar", "hat": "hat", "widehat": "hat",
                     "tilde": "tilde", "widetilde": "tilde", "vec": "vec"}[command]
            output.append(f"{label}({tex_to_unicode(argument)})")
        elif command in COMMANDS:
            output.append(COMMANDS[command])
        else:
            raise PaperBuildError(f"Unsupported TeX command \\{command}; use readable Unicode notation")
    return "".join(output)


def math_markup(text: str, base: str = "PaperLatin") -> str:
    converted = tex_to_unicode(text)
    output, plain, i = [], [], 0
    def flush():
        if plain:
            output.append(font_runs("".join(plain), base))
            plain.clear()
    while i < len(converted):
        char = converted[i]
        if char in "_^" and i + 1 < len(converted):
            flush()
            value, i = _group_at(converted, i + 1)
            tag = "sub" if char == "_" else "super"
            output.append(f"<{tag}>{font_runs(value, base)}</{tag}>")
        elif char in "{}":
            # Grouping braces without a command are rendered as braces: set
            # notation must not silently lose its delimiters.
            plain.append(char)
            i += 1
        else:
            plain.append(char)
            i += 1
    flush()
    return "".join(output)


INLINE = re.compile(r"(`[^`]+`|\$[^$\n]+\$|\\\(.+?\\\)|\*\*.+?\*\*|\[[^\]]+\]\([^\n]+?\)|\*[^*\n]+\*)")


def inline_markup(text: str, base: str = "PaperSong") -> str:
    parts, cursor = [], 0
    for match in INLINE.finditer(text):
        plain = text[cursor:match.start()]
        if re.search(r"\\[A-Za-z]+", plain):
            parts.append(math_markup(plain, base))
        else:
            parts.append(font_runs(plain, base))
        token = match.group(0)
        if token.startswith("`"):
            parts.append(font_runs(token[1:-1], "PaperCode"))
        elif token.startswith("$"):
            parts.append(math_markup(token[1:-1]))
        elif token.startswith("\\("):
            parts.append(math_markup(token[2:-2]))
        elif token.startswith("**"):
            parts.append(inline_markup(token[2:-2], "PaperHei"))
        elif token.startswith("["):
            label, target = re.match(r"\[([^\]]+)\]\((.+)\)", token).groups()
            target = target.strip().strip("<>")
            rendered = inline_markup(label, base)
            if target.startswith(("https://", "http://")):
                parts.append(f'<link href="{html.escape(target, quote=True)}" color="#234A70">{rendered}</link>')
            else:
                parts.append(rendered)
        else:
            parts.append(inline_markup(token[1:-1], base))
        cursor = match.end()
    remaining = text[cursor:]
    parts.append(math_markup(remaining, base) if re.search(r"\\[A-Za-z]+", remaining) else font_runs(remaining, base))
    return "".join(parts)


def split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells, chars, in_code, in_math, i = [], [], False, False, 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            chars.append("|")
            i += 2
            continue
        if char == "`":
            in_code = not in_code
        elif char == "$" and not in_code:
            in_math = not in_math
        if char == "|" and not in_code and not in_math:
            cells.append("".join(chars).strip())
            chars.clear()
        else:
            chars.append(char)
        i += 1
    cells.append("".join(chars).strip())
    return cells


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def parse_markdown(text: str) -> list[Block]:
    lines = normalize_typography(text).splitlines()
    blocks, i = [], 0
    while i < len(lines):
        line, stripped, number = lines[i], lines[i].strip(), i + 1
        if not stripped:
            i += 1
            continue
        if stripped in {"<!-- pagebreak -->", "<!-- newpage -->", "<div style=\"page-break-after: always;\"></div>"}:
            blocks.append(Block("pagebreak", line=number))
            i += 1
            continue
        if stripped.startswith("<!--"):
            while "-->" not in lines[i] and i + 1 < len(lines):
                i += 1
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            blocks.append(Block("heading", heading.group(2), len(heading.group(1)), line=number))
            i += 1
            continue
        if stripped.startswith("```"):
            language = stripped[3:].strip().lower()
            i += 1
            content = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                content.append(lines[i])
                i += 1
            if i >= len(lines):
                raise PaperBuildError(f"Unclosed code fence at line {number}")
            blocks.append(Block("math" if language in {"math", "latex", "tex"} else "code",
                                "\n".join(content), line=number))
            i += 1
            continue
        if stripped.startswith("\\[") or stripped.startswith("$$"):
            opening, closing = ("\\[", "\\]") if stripped.startswith("\\[") else ("$$", "$$")
            content = stripped[len(opening):]
            while closing not in content:
                i += 1
                if i >= len(lines):
                    raise PaperBuildError(f"Unclosed display math at line {number}")
                content += "\n" + lines[i]
            value, tail = content.split(closing, 1)
            if tail.strip():
                raise PaperBuildError(f"Text after closing display math at line {i + 1}")
            blocks.append(Block("math", value.strip(), line=number))
            i += 1
            continue
        image_match = re.fullmatch(r"!\[([^\]]*)\]\((.+?)\)", stripped)
        if image_match:
            target = image_match.group(2).strip().strip("<>")
            target = re.sub(r'\s+"[^"]*"$', "", target)
            blocks.append(Block("image", image_match.group(1), path=target, line=number))
            i += 1
            continue
        if i + 1 < len(lines) and "|" in line and is_table_separator(lines[i + 1]):
            rows = [split_table_row(line)]
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                row = split_table_row(lines[i])
                if len(row) != len(rows[0]):
                    raise PaperBuildError(f"Table at line {number}: row {i + 1} has {len(row)} cells, expected {len(rows[0])}")
                rows.append(row)
                i += 1
            blocks.append(Block("table", rows=rows, line=number))
            continue
        if re.fullmatch(r"[-*_]{3,}", stripped):
            blocks.append(Block("space", line=number))
            i += 1
            continue
        list_match = re.match(r"^\s*(?:([-+*])\s+|((?:\d+)[.)])\s+)(.*)$", line)
        if list_match:
            prefix = (list_match.group(2) + " ") if list_match.group(2) else "• "
            blocks.append(Block("list", prefix + list_match.group(3), line=number))
            i += 1
            continue
        if stripped.startswith(">"):
            blocks.append(Block("quote", stripped.lstrip(">").strip(), line=number))
            i += 1
            continue
        paragraph = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip():
            nxt = lines[i].strip()
            if (re.match(r"^(#{1,6})\s|^[-+*]\s|^\d+[.)]\s|^!\[|^```|^>|^<!--", nxt)
                    or nxt.startswith(("\\[", "$$"))
                    or (i + 1 < len(lines) and "|" in lines[i] and is_table_separator(lines[i + 1]))):
                break
            paragraph.append(nxt)
            i += 1
        blocks.append(Block("paragraph", " ".join(paragraph), line=number))
    return blocks


def make_styles(body_size: float = 11.0) -> dict[str, ParagraphStyle]:
    registered = set(pdfmetrics.getRegisteredFontNames())
    math_font = "PaperLatin" if "PaperLatin" in registered else "PaperSong"
    code_font = "PaperCode" if "PaperCode" in registered else "PaperSong"
    base = dict(fontName="PaperSong", fontSize=body_size, leading=body_size * 1.69,
                textColor=colors.HexColor("#171717"), wordWrap="CJK", splitLongWords=True,
                allowWidows=0, allowOrphans=0, spaceAfter=6.5)
    styles = {
        "body": ParagraphStyle("Body", firstLineIndent=2 * body_size, **base),
        "list": ParagraphStyle("List", leftIndent=12, rightIndent=2, firstLineIndent=0, **base),
        "quote": ParagraphStyle("Quote", leftIndent=16, rightIndent=12, **base),
        "title": ParagraphStyle("Title", fontName="PaperHei", fontSize=22, leading=31,
                                alignment=TA_CENTER, spaceBefore=6, spaceAfter=17, wordWrap="CJK", keepWithNext=True),
        "h2": ParagraphStyle("H2", fontName="PaperHei", fontSize=13.4, leading=21,
                             spaceBefore=13, spaceAfter=7, wordWrap="CJK", keepWithNext=True),
        "h3": ParagraphStyle("H3", fontName="PaperHei", fontSize=11.7, leading=18.7,
                             spaceBefore=9, spaceAfter=5.5, wordWrap="CJK", keepWithNext=True),
        "h4": ParagraphStyle("H4", fontName="PaperHei", fontSize=11.0, leading=18,
                             spaceBefore=7, spaceAfter=4.5, wordWrap="CJK", keepWithNext=True),
        "math": ParagraphStyle("Math", fontName=math_font, fontSize=10.7, leading=18.5,
                               alignment=TA_CENTER, leftIndent=7, rightIndent=7, spaceBefore=4,
                               spaceAfter=9, wordWrap="CJK", splitLongWords=True),
        "caption": ParagraphStyle("Caption", fontName="PaperSong", fontSize=9.3, leading=14,
                                  alignment=TA_CENTER, spaceBefore=5, spaceAfter=11, wordWrap="CJK"),
        "code": ParagraphStyle("Code", fontName=code_font, fontSize=8.7, leading=12.5,
                               leftIndent=9, rightIndent=9, spaceBefore=1, spaceAfter=1,
                               backColor=colors.HexColor("#F4F5F6"), borderPadding=4,
                               wordWrap="CJK", splitLongWords=True),
    }
    return styles


def _display_length(value: str) -> float:
    text = re.sub(r"[`*$]", "", value)
    return sum(1.0 if ord(c) > 255 else 0.54 for c in text)


def table_column_widths(rows: list[list[str]], width: float) -> list[float]:
    count = len(rows[0])
    # Long descriptions receive more space, but no cell can force page overflow.
    weights = []
    for column in range(count):
        lengths = sorted(_display_length(row[column]) for row in rows)
        median = lengths[len(lengths) // 2]
        weights.append(min(24.0, max(5.0, math.sqrt(max(lengths[-1], median)) * 2.0,
                                     min(16.0, _display_length(rows[0][column])))))
    total = sum(weights)
    return [width * weight / total for weight in weights]


def table_flowables(block: Block, styles: dict, width: float) -> tuple[list, list[dict]]:
    rows = block.rows
    columns = len(rows[0])
    groups = [list(range(columns))] if columns <= 8 else [[0] + list(range(i, min(i + 6, columns)))
                                                        for i in range(1, columns, 6)]
    flowables, notes = [], []
    for index, indices in enumerate(groups):
        panel = [[row[column] for column in indices] for row in rows]
        widths = table_column_widths(panel, width)
        size = 8.8 if len(indices) <= 5 else 8.2
        code_column = next((c for c, header in enumerate(panel[0]) if "案例编码" in header), None)
        code_pattern = re.compile(r"^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$")
        actual_case_codes = (code_column is not None and
                             any(code_pattern.fullmatch(row[code_column].strip("` ")) for row in panel[1:]))
        code_font = "PaperCode" if "PaperCode" in pdfmetrics.getRegisteredFontNames() else "PaperSong"
        code_size = 7.6
        keep_short_table = bool(actual_case_codes and len(panel) <= 7)
        if actual_case_codes:
            # Official case codes must remain individually verifiable, without
            # a final character stranded on a new line. Reclaim unused width
            # from the one-digit problem column before scaling numeric columns.
            fixed = {code_column: max(width * .22,
                     max(pdfmetrics.stringWidth(row[code_column].strip("` "), code_font, code_size)
                         for row in panel[1:]) + 12)}
            for c, header in enumerate(panel[0]):
                if header.strip() == "问题":
                    fixed[c] = 32.0
            remaining = width - sum(fixed.values())
            floating_total = sum(value for c, value in enumerate(widths) if c not in fixed)
            if remaining <= 0 or floating_total <= 0:
                raise PaperBuildError("Case-code table cannot fit the available width")
            widths = [fixed[c] if c in fixed else value * remaining / floating_total
                      for c, value in enumerate(widths)]
        cell_style = ParagraphStyle("TableCell", fontName="PaperSong", fontSize=size,
                                    leading=size * 1.47, wordWrap="CJK", splitLongWords=True,
                                    spaceAfter=0, allowWidows=1, allowOrphans=1)
        code_style = ParagraphStyle("CaseCode", parent=cell_style, fontName=code_font,
                                    fontSize=code_size, leading=size * 1.47,
                                    wordWrap="LTR", splitLongWords=False)
        data = []
        for r, row in enumerate(panel):
            formatted_row = []
            for c, cell in enumerate(row):
                if actual_case_codes and r > 0 and c == code_column:
                    formatted_row.append(Paragraph(font_runs(cell.strip("` "), code_font), code_style))
                else:
                    formatted_row.append(Paragraph(inline_markup(cell, "PaperHei" if r == 0 else "PaperSong"), cell_style))
            data.append(formatted_row)
        table = LongTable(data, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1,
                          hAlign="LEFT", spaceBefore=4, spaceAfter=11)
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EDF2")),
                    ("LINEABOVE", (0, 0), (-1, 0), .65, colors.HexColor("#505965")),
                    ("LINEBELOW", (0, 0), (-1, 0), .45, colors.HexColor("#8B939C")),
                    ("LINEBELOW", (0, -1), (-1, -1), .6, colors.HexColor("#505965")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8FA")])]
        table.setStyle(TableStyle(commands))
        if len(groups) > 1:
            flowables.append(Paragraph(font_runs(f"宽表分栏 {index + 1}/{len(groups)}（首列重复以保持行对应）"), styles["caption"]))
        flowables.append(KeepTogether([table]) if keep_short_table else table)
        notes.append({"source_line": block.line, "columns": len(indices), "rows": len(rows),
                      "panel": index + 1, "panels": len(groups), "width_pt": sum(widths),
                      "keep_together": keep_short_table,
                      "case_code_column": code_column if actual_case_codes else None})
    return flowables, notes


def build_story(blocks: list[Block], source_dir: Path, *, body_size: float = 11.0) -> tuple[list, dict]:
    styles = make_styles(body_size)
    story, tables, images, headings = [], [], [], []
    first_title = True
    for block_index, block in enumerate(blocks):
        try:
            if block.kind == "heading":
                is_title = block.level == 1 and first_title
                name = "title" if is_title else "h2" if block.level <= 2 else "h3" if block.level == 3 else "h4"
                base = "PaperHei"
                para = Paragraph(inline_markup(block.text, base), styles[name])
                para._paper_heading = (0 if is_title or block.level <= 2 else min(block.level - 2, 2), block.text)
                # A break sentinel between consecutive headings interrupts
                # ReportLab's keepWithNext chain and can strand the parent
                # section heading at the preceding page foot.
                if not is_title and (block_index == 0 or blocks[block_index - 1].kind != "heading"):
                    story.append(CondPageBreak(74))
                story.append(para)
                headings.append({"line": block.line, "level": block.level, "text": block.text})
                first_title = False
            elif block.kind in {"paragraph", "list", "quote"}:
                text = inline_markup(block.text)
                style = styles["body" if block.kind == "paragraph" else block.kind]
                story.append(Paragraph(text, style))
            elif block.kind == "math":
                story.append(Paragraph(math_markup(block.text), styles["math"]))
            elif block.kind == "code":
                for line in block.text.splitlines() or [""]:
                    leading = len(line) - len(line.lstrip(" "))
                    markup = "&#160;" * leading + font_runs(line.lstrip(" ") or " ", "PaperCode")
                    story.append(Paragraph(markup, styles["code"]))
                story.append(Spacer(1, 7))
            elif block.kind == "table":
                objects, descriptions = table_flowables(block, styles, CONTENT_WIDTH)
                story.extend(objects)
                tables.extend(descriptions)
            elif block.kind == "image":
                if block.path.startswith(("http://", "https://", "data:")):
                    raise PaperBuildError("Images must already exist locally; remote image downloads are not performed")
                path = Path(block.path)
                if not path.is_absolute():
                    path = source_dir / path
                path = path.resolve()
                if not path.is_file():
                    raise PaperBuildError(f"Image does not exist: {path}")
                with PillowImage.open(path) as picture:
                    pixel_width, pixel_height = picture.size
                if not pixel_width or not pixel_height:
                    raise PaperBuildError(f"Invalid image dimensions: {path}")
                scale = min(CONTENT_WIDTH / pixel_width, 405.0 / pixel_height)
                width, height = pixel_width * scale, pixel_height * scale
                pic = Image(str(path), width=width, height=height, hAlign="CENTER")
                group = [Spacer(1, 5), pic]
                if block.text:
                    group.append(Paragraph(inline_markup(block.text), styles["caption"]))
                else:
                    group.append(Spacer(1, 9))
                story.append(KeepTogether(group))
                images.append({"source_line": block.line, "path": str(path), "width_pt": width,
                               "height_pt": height, "source_pixels": [pixel_width, pixel_height]})
            elif block.kind == "pagebreak":
                story.append(PageBreak())
            elif block.kind == "space":
                story.append(Spacer(1, 7))
            else:
                raise PaperBuildError(f"Unknown block kind: {block.kind}")
        except Exception as exc:
            if isinstance(exc, PaperBuildError):
                raise PaperBuildError(f"Markdown line {block.line}: {exc}") from exc
            raise PaperBuildError(f"Could not format Markdown line {block.line}: {exc}") from exc
    report = {"blocks": len(blocks), "flowables": len(story), "headings": headings,
              "tables": tables, "images": images, "content_width_pt": CONTENT_WIDTH,
              "content_height_pt": CONTENT_HEIGHT, "body_font_size_pt": body_size,
              "pdf_created": False}
    return story, report


class PaperDocTemplate(BaseDocTemplate):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._outline_index = 0
        self._outline_level = -1
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="PaperFrame")
        self.addPageTemplates(PageTemplate(id="Paper", frames=[frame], onPage=draw_page))

    def afterFlowable(self, flowable):
        heading = getattr(flowable, "_paper_heading", None)
        if heading:
            level, text = heading
            level = min(level, self._outline_level + 1)
            key = f"section-{self._outline_index}"
            self._outline_index += 1
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(re.sub(r"[*`$]", "", text), key, level=level, closed=False)
            self._outline_level = level


def draw_page(canvas, document):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.setFont("PaperSong", 8)
    if document.page > 1:
        canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 30, "无线电干扰源的快速自动定位与清除")
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.setFont("PaperSong", 9)
    canvas.drawCentredString(PAGE_WIDTH / 2, 25, str(document.page))
    canvas.restoreState()


def check_layout(story: list, report: dict) -> dict:
    """Check measured widths without constructing a PDF or a Canvas."""
    measured, overflows = 0, []
    for flowable in story:
        if isinstance(flowable, (KeepTogether, PageBreak, CondPageBreak)):
            continue
        width, height = flowable.wrap(CONTENT_WIDTH, CONTENT_HEIGHT)
        measured += 1
        if width > CONTENT_WIDTH + 1e-5:
            overflows.append({"type": type(flowable).__name__, "width_pt": width})
    if overflows:
        raise PaperBuildError(f"Flowables exceed page width: {overflows}")
    return {**report, "measured_flowables": measured, "width_overflows": overflows}


def generate_pdf(source: Path, output: Path, *, body_size: float = 11.0,
                 min_pages: int = 15, font_dir: Path = Path("C:/Windows/Fonts")) -> dict:
    fonts = register_fonts(font_dir)
    source_bytes = source.read_bytes()
    blocks = parse_markdown(source_bytes.decode("utf-8"))
    if not blocks:
        raise PaperBuildError("Paper has no content")
    story, report = build_story(blocks, source.parent, body_size=body_size)
    report = check_layout(story, report)
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), source.stem)
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "tmp" / "pdfs"
    scratch.mkdir(parents=True, exist_ok=True)
    staged = scratch / (output.stem + ".building.pdf")
    document = PaperDocTemplate(str(staged), pagesize=A4,
                               leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
                               topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
                               title=title, author="", subject="B题数学建模研究",
                               pageCompression=1)
    document.build(story)
    pages = document.page
    if pages < min_pages:
        raise PaperBuildError(f"Paper rendered to {pages} pages, below required {min_pages}; "
                              f"no blank pages were added. Review staged output: {staged}")
    os.replace(staged, output)
    report.update(pdf_created=True, pages=pages, source=str(source), output=str(output),
                  bytes=output.stat().st_size, fonts=fonts,
                  source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                  pdf_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  qa_status="Built; every page still requires rendered visual inspection")
    qa_file = scratch / "paper_build_report.json"
    qa_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


class PreparationTests(unittest.TestCase):
    """Parser/measurement tests only: never writes a PDF or calls the marker."""

    @classmethod
    def setUpClass(cls):
        register_fonts(Path("C:/Windows/Fonts"))

    def test_unicode_math_and_font_fallback(self):
        markup = font_runs("无线电 ∀g∈ℝ²，∃q：‖q−g‖≤20；ε=1.0051°，𝒯，𝑥")
        self.assertIn("Paper", markup)
        paragraph = Paragraph(markup, make_styles()["body"])
        width, height = paragraph.wrap(CONTENT_WIDTH, CONTENT_HEIGHT)
        self.assertLessEqual(width, CONTENT_WIDTH)
        self.assertGreater(height, 0)

    def test_tex_is_readable_and_unknown_commands_fail(self):
        value = tex_to_unicode(r"\frac{L}{5}+5M+S+5K+3F\leq\sqrt{2}\cdot r")
        self.assertNotIn("\\", value)
        self.assertIn("(L)/(5)", value)
        paragraph = Paragraph(math_markup(r"x_{t+1}=\frac{a^2}{b}+\epsilon"), make_styles()["math"])
        paragraph.wrap(CONTENT_WIDTH, CONTENT_HEIGHT)
        with self.assertRaises(PaperBuildError):
            tex_to_unicode(r"\unknowncommand{x}")

    def test_table_splits_only_at_real_delimiters_and_panels_fit(self):
        self.assertEqual(split_table_row(r"| 式子 $|x|$ | `a|b` | c\|d |"), ["式子 $|x|$", "`a|b`", "c|d"])
        rows = [[f"列{i}" for i in range(12)], ["很长的中文表格内容" * 4 for _ in range(12)]]
        flowables, notes = table_flowables(Block("table", rows=rows, line=1), make_styles(), CONTENT_WIDTH)
        self.assertEqual(len(notes), 2)
        for table in flowables:
            if isinstance(table, LongTable):
                width, _ = table.wrap(CONTENT_WIDTH, CONTENT_HEIGHT)
                self.assertAlmostEqual(width, CONTENT_WIDTH)

    def test_source_parser_handles_paper_blocks(self):
        sample = """# 中文研究论文

## 摘要

**方法**以集合外包维护状态，公式为 $R(P)\\le20$。

| 参数 | 数值 | 含义 |
|---|---:|---|
| 速度 | 5 | 米每秒 |

\\[
T=\\frac{L}{5}+5M+S+5K+3F
\\]

1. 验证条件。
- 记录失败。

```python
result = client.measure(1, (0, 0))
```

<!-- pagebreak -->
"""
        blocks = parse_markdown(sample)
        self.assertEqual(sum(b.kind == "table" for b in blocks), 1)
        self.assertEqual(sum(b.kind == "math" for b in blocks), 1)
        story, report = build_story(blocks, ROOT / "docs")
        result = check_layout(story, report)
        self.assertFalse(result["pdf_created"])

    def test_official_case_codes_stay_on_one_line_in_short_table(self):
        rows = [["问题", "案例编码", "清除/结束后总数", "总虚拟时间/s", "平均定位清除/s", "本地程序/s"],
                ["4", "63G4-VPK5-AZX3-2YPM", "14/14", "10794.814634", "771.058188", "4.592336"],
                ["3", "MK6S-GWQ5-EBNN-VPAC", "15/15", "5232.306106", "348.820407", "2.426759"]]
        flowables, notes = table_flowables(Block("table", rows=rows), make_styles(), CONTENT_WIDTH)
        self.assertIsInstance(flowables[0], KeepTogether)
        table = flowables[0]._content[0]
        self.assertAlmostEqual(sum(table._colWidths), CONTENT_WIDTH)
        for row in table._cellvalues[1:]:
            paragraph = row[1]
            _, height = paragraph.wrap(table._colWidths[1] - 10, CONTENT_HEIGHT)
            self.assertLessEqual(height, paragraph.style.leading + 1e-6)

    def test_missing_image_and_bad_table_are_actionable(self):
        with self.assertRaises(PaperBuildError):
            build_story([Block("image", "图", path="missing-not-a-real-image.png", line=3)], ROOT)
        with self.assertRaises(PaperBuildError):
            parse_markdown("| A | B |\n|---|---|\n| a | b | c |")

    def test_relative_image_dimensions_are_preserved_and_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            PillowImage.new("RGB", (1200, 500), "white").save(directory / "figure.png")
            story, report = build_story([Block("image", "图 1 样例", path="figure.png", line=1)], directory)
            self.assertEqual(len(story), 1)
            image = report["images"][0]
            self.assertLessEqual(image["width_pt"], CONTENT_WIDTH)
            self.assertLessEqual(image["height_pt"], 405)
            self.assertAlmostEqual(image["width_pt"] / image["height_pt"], 1200 / 500)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "docs" / "paper.md")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "pdf" / "B题研究论文.pdf")
    parser.add_argument("--font-dir", type=Path, default=Path("C:/Windows/Fonts"))
    parser.add_argument("--body-size", type=float, default=11.0)
    parser.add_argument("--min-pages", type=int, default=15)
    parser.add_argument("--check", action="store_true", help="Parse and measure; do not create a PDF")
    parser.add_argument("--self-test", action="store_true", help="Run small preparation tests; do not create a PDF")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PreparationTests))
        return 0 if result.wasSuccessful() else 1
    if not args.input.is_file():
        parser.error(f"Paper source does not exist yet: {args.input}")
    if not 9.5 <= args.body_size <= 13.0:
        parser.error("body-size must be 9.5..13 pt")
    if args.min_pages < 1:
        parser.error("min-pages must be positive")
    try:
        if args.check:
            fonts = register_fonts(args.font_dir)
            blocks = parse_markdown(args.input.read_text(encoding="utf-8"))
            story, report = build_story(blocks, args.input.parent, body_size=args.body_size)
            report = check_layout(story, report)
            report["fonts"] = fonts
        else:
            report = generate_pdf(args.input.resolve(), args.output.resolve(), body_size=args.body_size,
                                  min_pages=args.min_pages, font_dir=args.font_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except PaperBuildError as exc:
        print(f"Paper build error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
