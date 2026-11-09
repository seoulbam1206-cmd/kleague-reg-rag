"""사람이 원본 페이지를 보고 옮겨 적은 표 구조를 용도별로 바꾼다.

표 구조는 docs_public/manual/tables/*.json에 둔다. 칸 하나는 문자열이거나
{"text", "rowspan", "colspan"}이고, 각 행에는 그 행에서 시작하는 칸만 적는다(HTML 표와 같은 방식).

- AI·검색용: 행마다 열 제목을 붙인 한 줄 텍스트 (row_texts)
  예: 주차장 > 긴급구조차량 > 경찰버스 (기준 A): ★★★★ 4대, ★★★ 4대, ★★ 신축 2대, ★★ 기존 1대, ★ 1대 (관련 조항 7.5.)
- 화면용: 칸 합치기를 살린 HTML 표 (table_html)

열의 역할(roles)은 path(행 이름의 단계), tag(기준 등급처럼 괄호로 붙일 값), value(값), note(관련 조항) 중 하나다.
구조 파일은 원본 파일명과 쪽으로 찾는다. 규정이 개정되어 파일명이 바뀌면 적용되지 않고 자동 추출로 돌아간다.
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_DIR = ROOT / "docs_public" / "manual" / "tables"
ROLES = {"path", "tag", "value", "note"}


def cell_parts(cell) -> tuple[str, int, int]:
    if isinstance(cell, str):
        return cell, 1, 1
    return cell["text"], cell.get("rowspan", 1), cell.get("colspan", 1)


def expand(rows: list[list], width: int) -> list[list[tuple[str, tuple[int, int]]]]:
    """행·열 걸침을 펼쳐 칸마다 (글자, 그 칸이 시작한 위치)를 돌려준다. 칸이 비거나 넘치면 오류."""
    slots: dict[tuple[int, int], tuple[str, tuple[int, int]]] = {}
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in slots:
                c += 1
            text, rowspan, colspan = cell_parts(cell)
            if c + colspan > width or r + rowspan > len(rows):
                raise ValueError(f"{r + 1}행의 '{text}' 칸이 표 밖으로 나감")
            for dr in range(rowspan):
                for dc in range(colspan):
                    if (r + dr, c + dc) in slots:
                        raise ValueError(f"{r + 1}행의 '{text}' 칸이 다른 칸과 겹침")
                    slots[(r + dr, c + dc)] = (text, (r, c))
            c += colspan
    grid = []
    for r in range(len(rows)):
        missing = [c + 1 for c in range(width) if (r, c) not in slots]
        if missing:
            raise ValueError(f"{r + 1}행에 빈 자리가 있음: {missing}열")
        grid.append([slots[(r, c)] for c in range(width)])
    return grid


def check(table: dict) -> None:
    if not set(table["roles"]) <= ROLES:
        raise ValueError(f"알 수 없는 열 역할: {set(table['roles']) - ROLES}")
    width = len(table["roles"])
    expand(table["header"], width)
    expand(table["rows"], width)


def column_names(table: dict) -> list[str]:
    """머리글 여러 층을 위에서부터 이어 붙인다. ★★ 아래 신축이면 '★★ 신축'이 된다."""
    grid = expand(table["header"], len(table["roles"]))
    names = []
    for c in range(len(table["roles"])):
        parts = []
        for r in range(len(grid)):
            text, origin = grid[r][c]
            if text and (not parts or parts[-1][1] != origin):
                parts.append((text, origin))
        names.append(" ".join(text for text, _ in parts))
    return names


def row_texts(table: dict) -> list[str]:
    """본문 행마다 열 제목을 붙인 한 줄을 만든다.

    세로로 걸친 칸은 걸친 행마다 같은 값을 쓴다. 가로로 걸친 칸은 행 이름(path, tag)이면 한 번만 쓰고,
    값(value, note)이면 열마다 쓴다. "3대 이상"이 ★★★과 ★★ 신축에 걸쳐 있으면 두 등급 모두의 값이기 때문이다.
    """
    names = column_names(table)
    lines = []
    for row in expand(table["rows"], len(names)):
        path, tags, values, notes = [], [], [], []
        seen = set()
        for (text, origin), role, name in zip(row, table["roles"], names):
            if not text:
                continue
            if role in ("path", "tag"):
                if origin in seen:
                    continue
                seen.add(origin)
            if role == "path":
                if not path or path[-1] != text:  # 분류와 항목이 같은 글자면 한 번만
                    path.append(text)
            elif role == "tag":
                tags.append(f"{name} {text}")
            elif role == "value":
                values.append(f"{name} {text}")
            else:
                notes.append(f"{name} {text}")
        line = " > ".join(path)
        if tags:
            line += f" ({', '.join(tags)})"
        line += ": " + ", ".join(values)
        if notes:
            line += f" ({', '.join(notes)})"
        lines.append(line)
    return lines


def _rows_html(grid: list[list], rows: list[int], tag: str) -> list[str]:
    """grid에서 rows만 그린다. 걸친 칸이 범위 밖에서 시작했으면 범위 첫 행에 다시 쓴다."""
    out = []
    drawn = set()
    for position, r in enumerate(rows):
        cells = []
        for c, (text, origin) in enumerate(grid[r]):
            if origin in drawn:
                continue
            drawn.add(origin)
            rowspan = 1
            while position + rowspan < len(rows) and grid[rows[position + rowspan]][c][1] == origin:
                rowspan += 1
            colspan = 1
            while c + colspan < len(grid[r]) and grid[r][c + colspan][1] == origin:
                colspan += 1
            span = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (f' colspan="{colspan}"' if colspan > 1 else "")
            cells.append(f"<{tag}{span}>{html.escape(text)}</{tag}>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    return out


TABLE_STYLE = (
    "<style>.reg-table{border-collapse:collapse;font-size:0.85rem;width:100%;margin:0.5rem 0}"
    ".reg-table th,.reg-table td{border:1px solid rgba(128,128,128,0.45);padding:0.25rem 0.4rem;"
    "text-align:center;vertical-align:middle}"
    ".reg-table th{background:rgba(128,128,128,0.12);font-weight:600}</style>"
)


def table_html(table: dict, rows: list[int] | None = None) -> str:
    """화면용 HTML 표. rows를 주면 그 본문 행만 그리고 머리글은 항상 붙인다."""
    width = len(table["roles"])
    head = expand(table["header"], width)
    body = expand(table["rows"], width)
    rows = list(range(len(body))) if rows is None else rows
    parts = ['<table class="reg-table"><thead>']
    parts += _rows_html(head, list(range(len(head))), "th")
    parts.append("</thead><tbody>")
    parts += _rows_html(body, rows, "td")
    parts.append("</tbody></table>")
    return TABLE_STYLE + "".join(parts)


def load_tables(folder: Path = TABLE_DIR) -> dict[tuple[str, int, int], dict]:
    """표 구조를 {(원본 파일명, 쪽, 그 쪽의 몇 번째 표): 구조}로 읽고 검사한다."""
    tables = {}
    for path in sorted(folder.glob("*.json")):
        table = json.loads(path.read_text(encoding="utf-8"))
        check(table)
        table["id"] = path.stem
        tables[(table["source_file"], table["page"], table.get("table", 1))] = table
    return tables


def row_index(tables: dict) -> dict[str, tuple[str, int]]:
    """행 텍스트로 (표 id, 본문 행 번호)를 찾는 사전. 화면이 청크 본문에서 표 행을 알아보는 데 쓴다."""
    index = {}
    for table in tables.values():
        for i, line in enumerate(row_texts(table)):
            index[line] = (table["id"], i)
    return index
