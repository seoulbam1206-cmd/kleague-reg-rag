"""표 구조 파일의 초안을 PDF의 칸 경계로 만든다. 사람이 원본 이미지와 대조해 고친 뒤 쓴다.

    python scripts/draft_table.py 16 57          # 16번 규정 57쪽의 첫 번째 표
    python scripts/draft_table.py 16 68 --table 2

칸의 행·열 걸침은 PDF에 그려진 칸 경계에서 계산한다. 칸 안의 줄바꿈은 공백으로 잇는데,
낱말 중간에서 줄이 바뀐 경우(예: "안전 수\\n칙")는 사람이 고쳐야 하므로 목록으로 알려준다.
결과는 docs_public/manual/tables/{번호}_p{쪽}[_t{표}].json에 쓰고 status는 "초안"이다.
"""

import argparse
import json
import sys
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_text as extract_chars_text

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from extract import PDF_DIR, TABLE_SETTINGS, _area, _in_bbox, clean_text, drop_nested_tables  # noqa: E402
from tables import TABLE_DIR, check  # noqa: E402

TOLERANCE = 1.5  # 같은 경계로 볼 좌표 차이 (pt)
BROKEN_DOT = "ž"  # 원본 PDF에서 가운뎃점이 이 글자로 추출된다


def boundaries(values: list[float]) -> list[float]:
    merged: list[float] = []
    for value in sorted(values):
        if not merged or value - merged[-1] > TOLERANCE:
            merged.append(value)
    return merged


def index_of(lines: list[float], value: float) -> int:
    return min(range(len(lines)), key=lambda i: abs(lines[i] - value))


def cell_texts(table, page) -> dict[tuple, list[str]]:
    """칸마다 글자를 줄 단위로 모은다. 겹친 칸은 가장 작은 칸이 글자를 갖는다."""
    chars: dict[tuple, list] = {}
    for char in page.crop(table.bbox).chars:
        holders = [c for c in table.cells if _in_bbox(char, c)]
        if holders:
            chars.setdefault(min(holders, key=_area), []).append(char)
    return {
        cell: [clean_text(line).replace(BROKEN_DOT, "·") for line in extract_chars_text(found).splitlines()
               if clean_text(line)]
        for cell, found in chars.items()
    }


def guess_role(name: str) -> str:
    if name in ("기준",):
        return "tag"
    if "관련" in name and "조항" in name:
        return "note"
    if name in ("분류", "항목", "") or name.startswith("항목"):
        return "path"
    return "value"


def draft(table, page) -> tuple[dict, list[str]]:
    texts = cell_texts(table, page)
    xs = boundaries([v for c in table.cells for v in (c[0], c[2])])
    ys = boundaries([v for c in table.cells for v in (c[1], c[3])])
    placed = []
    for cell in table.cells:
        r0, r1 = index_of(ys, cell[1]), index_of(ys, cell[3])
        c0, c1 = index_of(xs, cell[0]), index_of(xs, cell[2])
        lines = texts.get(cell, [])
        placed.append((r0, c0, r1 - r0, c1 - c0, " ".join(lines), lines))
    rows: list[list] = [[] for _ in range(len(ys) - 1)]
    wraps = []
    for r0, c0, rowspan, colspan, text, lines in sorted(placed):
        cell = text if rowspan == colspan == 1 else {
            "text": text, **({"rowspan": rowspan} if rowspan > 1 else {}),
            **({"colspan": colspan} if colspan > 1 else {}),
        }
        rows[r0].append(cell)
        if len(lines) > 1:
            wraps.append(" / ".join(lines))
    header_count = max((rs for r0, _, rs, _, _, _ in placed if r0 == 0), default=1)
    header, body = rows[:header_count], rows[header_count:]
    width = len(xs) - 1
    names = [""] * width
    for r0, c0, rowspan, colspan, text, _ in placed:
        if r0 < header_count:
            for c in range(c0, c0 + colspan):
                names[c] = (names[c] + " " + text).strip() if names[c] != text else text
    roles = [guess_role(name) for name in names]
    return {"header": header, "rows": body, "roles": roles}, wraps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("no", type=int, help="규정 번호")
    parser.add_argument("page", type=int)
    parser.add_argument("--table", type=int, default=1, help="그 쪽의 몇 번째 표 (위에서부터)")
    args = parser.parse_args()

    entries = json.loads((PDF_DIR / "manifest.json").read_text(encoding="utf-8"))
    entry = next(e for e in entries if e["no"] == args.no)
    file = entry["files"][0]["file"]
    with pdfplumber.open(PDF_DIR / file) as pdf:
        page = pdf.pages[args.page - 1]
        found = sorted(drop_nested_tables(page.find_tables(TABLE_SETTINGS)), key=lambda t: t.bbox[1])
        structure, wraps = draft(found[args.table - 1], page)
    suffix = f"_t{args.table}" if args.table > 1 else ""
    out = TABLE_DIR / f"{args.no:02d}_p{args.page}{suffix}.json"
    if out.exists():
        print(f"이미 있음: {out.relative_to(ROOT)} (덮어쓰지 않음)")
        return 1
    table = {
        "status": "초안",
        "source": entry["title"],
        "source_file": file,
        "page": args.page,
        "table": args.table,
        "title": "",
        "note": "PDF의 칸 경계로 만든 초안을 원본 이미지와 대조해 고친다.",
        **structure,
    }
    check(table)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out.relative_to(ROOT)} (머리글 {len(structure['header'])}행, 본문 {len(structure['rows'])}행)")
    for line in wraps:
        print(f"  줄바꿈 확인: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
