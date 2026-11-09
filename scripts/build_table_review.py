"""표 구조 검토 페이지를 만든다: 표마다 왼쪽에 원본 페이지 이미지, 오른쪽에 복원한 표.

    python scripts/build_table_review.py      # review/tables.html (git 제외)

복원한 표 아래에는 AI·검색에 쓰는 행 텍스트도 펼쳐 볼 수 있게 둔다.
글자가 원본과 같은지는 tests/test_tables.py가 확인하고, 이 페이지는 사람이 구조를 눈으로 보는 용도다.
"""

import base64
import html
import io
import sys
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extract import PDF_DIR  # noqa: E402
from tables import load_tables, row_texts, table_html  # noqa: E402

OUT = ROOT / "review" / "tables.html"
SCALE = 1.6

STYLE = """
:root { --bg: #ffffff; --fg: #1f2328; --muted: #656d76; --line: #d0d7de; --head: #f6f8fa; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #8d96a0; --line: #30363d; --head: #161b22; }
}
body { background: var(--bg); color: var(--fg); font-family: system-ui, "Malgun Gothic", sans-serif;
       margin: 0 auto; max-width: 1800px; padding: 16px; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin: 0 0 4px; }
.meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 12px; }
nav a { margin-right: 10px; white-space: nowrap; }
section { border-top: 2px solid var(--line); padding: 20px 0; }
.pair { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; align-items: start; }
.pair img { width: 100%; border: 1px solid var(--line); background: #fff; }
.right { overflow-x: auto; }
details { margin-top: 12px; font-size: 0.85rem; } details li { margin: 2px 0; }
.reg-table th { background: var(--head); }
@media (max-width: 900px) { .pair { grid-template-columns: 1fr; } }
"""


def page_png(file: str, page: int) -> str:
    pdf = pypdfium2.PdfDocument(str(PDF_DIR / file))
    try:
        image = pdf[page - 1].render(scale=SCALE).to_pil()
    finally:
        pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def section(table: dict) -> str:
    rows = "".join(f"<li>{html.escape(line)}</li>" for line in row_texts(table))
    title = f"{table['source']} {table['page']}쪽" + (f" (표 {table['table']})" if table.get("table", 1) > 1 else "")
    return f"""
<section id="{table['id']}">
  <h2>{html.escape(title)} · {html.escape(table.get('title', ''))}</h2>
  <div class="meta">{html.escape(table['id'])}.json · 상태: {html.escape(table.get('status', ''))} ·
    본문 {len(table['rows'])}행 · {html.escape(table.get('note', ''))}</div>
  <div class="pair">
    <div><img alt="원본 {table['page']}쪽" src="data:image/png;base64,{page_png(table['source_file'], table['page'])}"></div>
    <div class="right">{table_html(table)}
      <details><summary>AI·검색용 행 텍스트 ({len(table['rows'])}줄)</summary><ol>{rows}</ol></details>
    </div>
  </div>
</section>"""


def main() -> int:
    tables = sorted(load_tables().values(), key=lambda t: (t["source_file"], t["page"], t.get("table", 1)))
    missing = [t["id"] for t in tables if not (PDF_DIR / t["source_file"]).exists()]
    if missing:
        print(f"규정 PDF가 없어 만들 수 없음: {missing}")
        return 1
    nav = " ".join(f'<a href="#{t["id"]}">{t["page"]}쪽{"-" + str(t["table"]) if t.get("table", 1) > 1 else ""}</a>'
                   for t in tables)
    body = "".join(section(t) for t in tables)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>표 구조 검토</title><style>{STYLE}</style></head><body>
<h1>표 구조 검토 ({len(tables)}개)</h1>
<p class="meta">왼쪽은 원본 PDF 페이지, 오른쪽은 docs_public/manual/tables/의 표 구조로 그린 표다.
칸의 글자는 테스트로 원본과 대조했으므로, 여기서는 행·열 합치기와 머리글 구조를 눈으로 확인한다.</p>
<nav>{nav}</nav>{body}
</body></html>""", encoding="utf-8")
    print(f"저장: {OUT.relative_to(ROOT)} (표 {len(tables)}개, {OUT.stat().st_size / 2**20:.1f}MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
