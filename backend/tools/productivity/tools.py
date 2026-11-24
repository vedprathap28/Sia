"""
SIA — Productivity tools: Word documents, Excel workbooks, PowerPoint decks, spreadsheet analysis.
All outputs are written inside allowed folders and verified by re-opening the file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from backend.security.sandbox import resolve_safe
from backend.tools.registry import ToolResult, tool_def


def _reopen(kind: str):
    def v(args: dict, res: ToolResult):
        p = Path(res.data.get("path", ""))
        if not p.exists():
            return False, "file missing"
        try:
            if kind == "docx":
                import docx
                n = len(docx.Document(str(p)).paragraphs)
                return n > 0, f"re-opened, {n} paragraphs"
            if kind == "xlsx":
                import openpyxl
                wb = openpyxl.load_workbook(p, read_only=True)
                return True, f"re-opened, sheets={wb.sheetnames}"
            if kind == "pptx":
                from pptx import Presentation
                n = len(Presentation(str(p)).slides)
                return n > 0, f"re-opened, {n} slides"
        except Exception as e:
            return False, f"could not re-open: {e}"
        return True, "exists"
    return v


def _out(path: str, ext: str) -> Path:
    p = resolve_safe(path if path.lower().endswith(ext) else path + ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Word ──────────────────────────────────────────────────────────────────────

def _add_inline(par, text: str):
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part:
            par.add_run(part)


@tool_def(category="productivity", risk=lambda a: 2, verifier=_reopen("docx"), timeout=60,
          summarize=lambda a: f"Create Word document {a.get('path')}")
def create_word_document(path: Annotated[str, Field(description="Output .docx path")],
                         title: str,
                         markdown: Annotated[str, Field(description=(
                             "Body in simple markdown: '# ', '## ' headings, '- ' bullets, '1. ' numbered, "
                             "'| a | b |' tables, **bold**"))]) -> ToolResult:
    """Create a Word (.docx) document from markdown-style text."""
    import docx
    p = _out(path, ".docx")
    if p.exists():
        return ToolResult(ok=False, error=f"{p} exists; choose another name.")
    d = docx.Document()
    d.add_heading(title, 0)
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                t = d.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                t.style = "Table Grid"
                for r, row in enumerate(rows):
                    for c, val in enumerate(row):
                        t.cell(r, c).text = val
            continue
        if ln.startswith("### "):
            d.add_heading(ln[4:], 3)
        elif ln.startswith("## "):
            d.add_heading(ln[3:], 2)
        elif ln.startswith("# "):
            d.add_heading(ln[2:], 1)
        elif re.match(r"^\s*[-*] ", ln):
            _add_inline(d.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*] ", "", ln))
        elif re.match(r"^\s*\d+[.)] ", ln):
            _add_inline(d.add_paragraph(style="List Number"), re.sub(r"^\s*\d+[.)] ", "", ln))
        elif ln.strip():
            _add_inline(d.add_paragraph(), ln)
        i += 1
    d.save(p)
    return ToolResult(ok=True, output=f"Created {p}", data={"path": str(p)})


# ── Excel ─────────────────────────────────────────────────────────────────────

class SheetSpec(BaseModel):
    name: str = Field(description="Sheet name (≤31 chars)")
    headers: list[str]
    rows: list[list[Any]]


@tool_def(category="productivity", risk=2, verifier=_reopen("xlsx"), timeout=60,
          summarize=lambda a: f"Create Excel workbook {a.get('path')} with {len(a.get('sheets', []))} sheet(s)")
def create_spreadsheet(path: Annotated[str, Field(description="Output .xlsx path")],
                       sheets: list[SheetSpec]) -> ToolResult:
    """Create an Excel workbook. Numbers stay numeric; values starting with '=' become formulas."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    p = _out(path, ".xlsx")
    if p.exists():
        return ToolResult(ok=False, error=f"{p} exists; choose another name.")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        sh = sh if isinstance(sh, SheetSpec) else SheetSpec(**sh)
        ws = wb.create_sheet(sh.name[:31] or "Sheet")
        ws.append(sh.headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E78")
        for r in sh.rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        for i, h in enumerate(sh.headers, 1):
            width = max([len(str(h))] + [len(str(r[i - 1])) for r in sh.rows if len(r) >= i][:200])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width + 2))
    wb.save(p)
    return ToolResult(ok=True, output=f"Created {p}", data={"path": str(p)})


@tool_def(category="productivity", risk=1, timeout=90)
def analyse_spreadsheet(path: Annotated[str, Field(description=".xlsx or .csv path")],
                        sheet: str = "") -> ToolResult:
    """Summarise a spreadsheet: columns, row count, numeric stats (min/max/mean/sum), top values."""
    import csv
    p = resolve_safe(path, must_exist=True)
    if p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8", errors="replace") as f:
            data = list(csv.reader(f))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        data = [list(r) for r in ws.iter_rows(values_only=True)]
    if not data:
        return ToolResult(ok=False, error="Empty sheet")
    headers, rows = [str(h) for h in data[0]], data[1:]
    report = [f"{len(rows)} rows × {len(headers)} columns"]
    stats = {}
    for i, h in enumerate(headers):
        col = [r[i] for r in rows if i < len(r) and r[i] not in (None, "")]
        nums = []
        for v in col:
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                pass
        if nums and len(nums) >= 0.8 * len(col):
            s = {"min": min(nums), "max": max(nums), "mean": round(sum(nums) / len(nums), 3), "sum": round(sum(nums), 3)}
            report.append(f"- {h}: numeric {s}")
        else:
            from collections import Counter
            s = {"distinct": len(set(map(str, col))), "top": Counter(map(str, col)).most_common(3)}
            report.append(f"- {h}: text, {s['distinct']} distinct, top {s['top']}")
        stats[h] = s
    return ToolResult(ok=True, output="\n".join(report), data={"stats": stats})


# ── PowerPoint ────────────────────────────────────────────────────────────────

class SlideSpec(BaseModel):
    title: str
    bullets: list[str] = []
    notes: str = ""


@tool_def(category="productivity", risk=2, verifier=_reopen("pptx"), timeout=60,
          summarize=lambda a: f"Create presentation {a.get('path')} ({len(a.get('slides', []))} slides)")
def create_presentation(path: Annotated[str, Field(description="Output .pptx path")], title: str,
                        slides: list[SlideSpec], subtitle: str = "") -> ToolResult:
    """Create a PowerPoint deck: a title slide plus one slide per item (title + bullets + notes)."""
    from pptx import Presentation
    from pptx.util import Pt
    p = _out(path, ".pptx")
    if p.exists():
        return ToolResult(ok=False, error=f"{p} exists; choose another name.")
    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = title
    if subtitle and len(s0.placeholders) > 1:
        s0.placeholders[1].text = subtitle
    for sl in slides:
        sl = sl if isinstance(sl, SlideSpec) else SlideSpec(**sl)
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = sl.title
        body = s.placeholders[1].text_frame
        body.clear()
        for j, b in enumerate(sl.bullets or [""]):
            para = body.paragraphs[0] if j == 0 else body.add_paragraph()
            para.text = b
            para.font.size = Pt(20 if len(sl.bullets) <= 5 else 16)
        if sl.notes:
            s.notes_slide.notes_text_frame.text = sl.notes
    prs.save(p)
    return ToolResult(ok=True, output=f"Created {p}", data={"path": str(p)})
