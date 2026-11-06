"""규정 PDF에서 페이지별 텍스트를 뽑아 data/extracted/에 JSON으로 저장한다.

페이지 수를 원본과 맞추기 위해 표지·목차·뒤표지도 kind를 표시해 남기고 text만 비운다.
이미지라서 텍스트가 없는 페이지는 docs_public/manual/의 수동 요약으로 채운다.
기준일은 등록일이 아니라 manifest의 body_text에서 구한다.
"""

import json
import re
import sys
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_text as extract_chars_text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_pdfs import TABLE_SETTINGS  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))

from enforcement import load_enforcement_rules, resolve_enforcement  # noqa: E402

PDF_DIR = ROOT / "docs_public"
MANUAL_DIR = PDF_DIR / "manual"
OUT_DIR = ROOT / "data" / "extracted"

# 17번은 공백 글리프가 \x00으로 추출된다. 지우면 어절이 붙으므로 공백으로 바꾼다.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPACES = re.compile(r" {2,}")
REVISION = re.compile(
    r"(\d{4})(?:년\s*|/)(\d{1,2})(?:월\s*|/)(\d{1,2})일?\s*(개정|기준|제정)"
)
PAGE_MARK = re.compile(
    r"^(?:-\s*\d+\s*-(?:\s*\S.*\(\d{2}\.\d{2}\.\d{2}\))?"  # - 4 -, - 10 - 규정명 (20.11.05)
    r"|\d{1,3}"  # 3
    r"|\S.*\(\d{4}\)\s+\d{1,3})$"  # 규정명 (2026) 29
)
TOC_TITLE = re.compile(r"^[-\s]*목\s*차[-\s]*$")
HISTORY_TITLE = re.compile(r"^(?:제·)?개정\s*내역$")
TOC_ENTRY = re.compile(r"\s\d{1,3}$")
COVER_MAX_LINES = 4


def parse_revision(body_text: str) -> dict:
    """개정 메모를 {"date": "YYYY-MM-DD", "kind": 개정|기준|제정}으로 바꾼다."""
    match = REVISION.search(body_text)
    if not match:
        raise ValueError(f"기준일을 읽을 수 없음: {body_text!r}")
    year, month, day, kind = match.groups()
    return {"date": f"{year}-{int(month):02d}-{int(day):02d}", "kind": kind}


def clean_text(text: str) -> str:
    return SPACES.sub(" ", CONTROL_CHARS.sub(" ", text)).strip()


def strip_page_marks(lines: list[str]) -> list[str]:
    """첫 줄과 마지막 줄의 쪽번호만 지운다. 본문 속 숫자는 건드리지 않는다."""
    lines = list(lines)
    if lines and PAGE_MARK.match(lines[-1].strip()):
        lines.pop()
    if lines and PAGE_MARK.match(lines[0].strip()):
        lines.pop(0)
    return lines


def classify_page(lines: list[str], page_no: int, page_count: int, prev_is_toc: bool) -> str:
    """페이지 종류를 판별한다: cover, toc, history, back_cover, body."""
    joined = "\n".join(lines)
    if page_no == 1 and len(lines) <= COVER_MAX_LINES:
        return "cover"
    if page_no == page_count and "www.kleague.com" in joined:
        return "back_cover"
    if any(TOC_TITLE.match(line.strip()) for line in lines[:3]):
        return "toc"
    if prev_is_toc and lines:
        entries = sum(bool(TOC_ENTRY.search(line.strip())) for line in lines)
        if entries >= len(lines) * 0.6:
            return "toc"
    if any(HISTORY_TITLE.match(line.strip()) for line in lines[:3]):
        return "history"
    return "body"


def table_to_markdown(rows: list[list[str | None]]) -> str:
    """표를 마크다운으로 바꾼다. 행이나 열이 하나뿐이면 표가 아니라 상자이므로 줄글로 둔다."""
    cleaned = [
        [" ".join(clean_text(cell or "").split()) for cell in row]
        for row in rows
    ]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return ""
    if len(cleaned) == 1 or len(cleaned[0]) == 1:
        return "\n".join(" ".join(cell for cell in row if cell) for row in cleaned)
    header, *body = cleaned
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def _in_bbox(obj: dict, bbox: tuple) -> bool:
    x = (obj["x0"] + obj["x1"]) / 2
    y = (obj["top"] + obj["bottom"]) / 2
    x0, top, x1, bottom = bbox
    return x0 <= x <= x1 and top <= y <= bottom


def _area(bbox: tuple) -> float:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _contains(outer: tuple, inner: tuple, margin: float = 1.0) -> bool:
    return (
        outer[0] - margin <= inner[0] and outer[1] - margin <= inner[1]
        and inner[2] <= outer[2] + margin and inner[3] <= outer[3] + margin
    )


def drop_nested_tables(tables: list) -> list:
    """다른 표 안에 들어 있는 표를 뺀다. 내용은 바깥 표의 셀로 추출된다."""
    return [
        t for t in tables
        if not any(
            o is not t and _area(o.bbox) > _area(t.bbox) and _contains(o.bbox, t.bbox)
            for o in tables
        )
    ]


def _overlap(a: tuple, b: tuple, margin: float = 1.0) -> bool:
    return (
        a[0] < b[2] - margin and b[0] < a[2] - margin
        and a[1] < b[3] - margin and b[1] < a[3] - margin
    )


def table_rows(table, page) -> list[list[str | None]]:
    """표의 행을 추출한다. 셀이 겹치면 글자를 가장 작은 셀에만 넣어 중복을 막는다."""
    cells = table.cells
    if not any(_overlap(a, b) for i, a in enumerate(cells) for b in cells[i + 1:]):
        return table.extract()
    assigned: dict[tuple, list] = {}
    for char in page.crop(table.bbox).chars:
        holders = [c for c in cells if _in_bbox(char, c)]
        if holders:
            assigned.setdefault(min(holders, key=_area), []).append(char)
    return [
        [extract_chars_text(assigned.get(cell, [])) if cell else None for cell in row.cells]
        for row in table.rows
    ]


def page_blocks(page) -> tuple[list[str], int]:
    """표 밖 텍스트 줄과 표를 세로 위치 순으로 합친다. (줄 목록, 표 개수)를 돌려준다."""
    tables = drop_nested_tables(page.find_tables(TABLE_SETTINGS))
    boxes = [t.bbox for t in tables]
    rest = page.filter(
        lambda o: o.get("object_type") != "char" or not any(_in_bbox(o, b) for b in boxes)
    )
    blocks = [(line["top"], clean_text(line["text"])) for line in rest.extract_text_lines()]
    rendered = 0
    for table in tables:
        markdown = table_to_markdown(table_rows(table, page))
        if markdown:
            blocks.append((table.bbox[1], markdown))
            rendered += "|" in markdown
    blocks.sort(key=lambda block: block[0])
    lines = [line for _, text in blocks for line in text.split("\n")]
    return lines, rendered


def load_manual_pages(folder: Path = MANUAL_DIR) -> dict[tuple[str, int], str]:
    """수동 요약을 {(원본 파일명, 쪽): 본문}으로 읽는다."""
    pages = {}
    for path in sorted(folder.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta = dict(line.split(": ", 1) for line in front.splitlines() if ": " in line)
        pages[(meta["source_file"], int(meta["page"]))] = body.strip()
    return pages


def extract_pdf(path: Path, manual: dict[tuple[str, int], str] | None = None) -> list[dict]:
    manual = manual or {}
    results = []
    prev_is_toc = False
    with pdfplumber.open(path) as pdf:
        count = len(pdf.pages)
        for no, page in enumerate(pdf.pages, 1):
            lines, tables = page_blocks(page)
            lines = strip_page_marks([line for line in lines if line.strip()])
            kind = classify_page(lines, no, count, prev_is_toc)
            prev_is_toc = kind == "toc"
            if (path.name, no) in manual:
                kind, text, tables = "manual", manual[(path.name, no)], 0
            elif kind in ("cover", "toc", "back_cover"):
                text, tables = "", 0
            else:
                text = "\n".join(lines)
            results.append({"page": no, "kind": kind, "text": text, "tables": tables})
    return results


def extract_document(entry: dict, pdf_dir: Path = PDF_DIR, manual: dict | None = None) -> dict:
    revision = parse_revision(entry["body_text"])
    enforcement = resolve_enforcement(
        entry["no"], revision["date"], load_enforcement_rules()
    )
    file_name = entry["files"][0]["file"]
    return {
        "no": entry["no"],
        "seq": entry["seq"],
        "title": entry["title"],
        "file": file_name,
        "registered": entry["registered"],
        "effective_date": revision["date"],
        "effective_kind": revision["kind"],
        "enforcement_date": enforcement["date"],
        "enforcement_basis": enforcement["basis"],
        "revision_note": entry["body_text"],
        "pages": extract_pdf(pdf_dir / file_name, manual),
    }


def main() -> int:
    entries = json.loads((PDF_DIR / "manifest.json").read_text(encoding="utf-8"))
    manual = load_manual_pages()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for entry in sorted(entries, key=lambda e: e["no"]):
        doc = extract_document(entry, manual=manual)
        out = OUT_DIR / f"{doc['no']:02d}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        kinds = [p["kind"] for p in doc["pages"]]
        print(
            f"{doc['no']:02d} {doc['effective_date']} {len(kinds)}쪽 "
            f"본문{kinds.count('body')} 목차{kinds.count('toc')} "
            f"표{sum(p['tables'] for p in doc['pages'])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
