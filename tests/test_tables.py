"""사람이 옮겨 적은 표 구조: 검사, 열 제목, 행 텍스트, HTML 표, 원본과의 글자 대조."""

import re
from collections import Counter

import pdfplumber
import pytest

from extract import PDF_DIR, TABLE_SETTINGS, clean_text, drop_nested_tables
from tables import (cell_parts, column_names, expand, load_tables, row_index, row_texts,
                    table_html)

SMALL = {
    "roles": ["path", "tag", "path", "value", "value", "note"],
    "header": [
        [{"text": "분류", "rowspan": 2}, {"text": "기준", "rowspan": 2}, {"text": "항목", "rowspan": 2},
         {"text": "★★", "colspan": 2}, {"text": "관련 조항", "rowspan": 2}],
        ["신축", "기존"],
    ],
    "rows": [
        [{"text": "주차장", "rowspan": 2}, {"text": "A", "rowspan": 2}, "버스", "4대", "2대", "7.1."],
        ["승용차", "24대", "12대", "7.1."],
        ["접근성", "A", "대중교통", {"text": "○", "colspan": 2}, "8.1."],
    ],
}


def test_expand_fills_spans_with_origin():
    grid = expand(SMALL["rows"], 6)
    assert grid[1][0] == ("주차장", (0, 0))  # 세로로 걸친 칸
    assert grid[2][4] == ("○", (2, 3))  # 가로로 걸친 칸


@pytest.mark.parametrize("rows, message", [
    ([["가", "나"], ["다"]], "빈 자리"),
    ([["가", "나", "다"]], "표 밖"),
    ([[{"text": "가", "rowspan": 3}, "나"], ["다"]], "표 밖"),
])
def test_expand_rejects_broken_structure(rows, message):
    with pytest.raises(ValueError, match=message):
        expand(rows, 2)


def test_column_names_join_header_levels():
    assert column_names(SMALL) == ["분류", "기준", "항목", "★★ 신축", "★★ 기존", "관련 조항"]


def test_row_texts_put_column_names_on_every_row():
    assert row_texts(SMALL) == [
        "주차장 > 버스 (기준 A): ★★ 신축 4대, ★★ 기존 2대 (관련 조항 7.1.)",
        "주차장 > 승용차 (기준 A): ★★ 신축 24대, ★★ 기존 12대 (관련 조항 7.1.)",
        "접근성 > 대중교통 (기준 A): ★★ 신축 ○, ★★ 기존 ○ (관련 조항 8.1.)",  # 가로로 걸친 값은 열마다
    ]


def test_html_keeps_merged_cells_and_two_level_header():
    html = table_html(SMALL)
    assert '<th colspan="2">★★</th>' in html
    assert '<th rowspan="2">분류</th>' in html
    assert '<td rowspan="2">주차장</td>' in html
    assert '<td colspan="2">○</td>' in html


def test_html_for_some_rows_repeats_group_label():
    """청크가 걸친 칸의 중간에서 시작하면 그 칸을 첫 행에 다시 쓴다. 머리글은 항상 붙인다."""
    html = table_html(SMALL, [1])
    assert "<td>주차장</td>" in html and "<td>승용차</td>" in html
    assert "버스" not in html and "<th" in html


def test_html_escapes_cell_text():
    table = {"roles": ["path", "value"], "header": [["가", "나"]], "rows": [["<b>", "&"]]}
    assert "&lt;b&gt;" in table_html(table) and "&amp;" in table_html(table)


def test_cell_parts_defaults():
    assert cell_parts("가") == ("가", 1, 1)
    assert cell_parts({"text": "가", "colspan": 2}) == ("가", 1, 2)


def test_review_files_are_valid_and_indexed():
    tables = load_tables()
    assert ("16_K리그_경기장_시설기준_가이드라인_20241203.pdf", 57, 1) in tables
    index = row_index(tables)
    line = "주차장 > 긴급구조차량 > 경찰버스 (기준 A): ★★★★ 4대, ★★★ 4대, ★★ 신축 2대, ★★ 기존 1대, ★ 1대 (관련 조항 7.5.)"
    assert index[line] == ("16_p57", 25)


def _letters(text: str) -> Counter:
    # 'ž'는 원본 PDF에서 가운뎃점(·)이 깨져 나오는 글자다
    return Counter(re.sub(r"[\s]", "", clean_text(text).replace("ž", "·")))


@pytest.mark.slow
@pytest.mark.parametrize("key", sorted(load_tables()))
def test_transcribed_cells_match_pdf_letters(key):
    """옮겨 적은 칸의 글자를 모두 모으면 원본 표 영역의 글자와 같아야 한다 (빠짐·오타·중복 확인)."""
    file, page_no, number = key
    table = load_tables()[key]
    path = PDF_DIR / file
    if not path.exists():
        pytest.skip("규정 PDF 없음")
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no - 1]
        found = sorted(drop_nested_tables(page.find_tables(TABLE_SETTINGS)), key=lambda t: t.bbox[1])
        raw = page.crop(found[number - 1].bbox).extract_text()
    cells = [cell_parts(cell)[0] for rows in (table["header"], table["rows"]) for row in rows for cell in row]
    assert _letters("".join(cells)) == _letters(raw)


def test_review_page_is_not_committed():
    """검토 페이지에는 규정 원문 페이지 이미지가 들어 있으므로 저장소에 올리지 않는다."""
    import subprocess
    from tables import ROOT
    result = subprocess.run(["git", "check-ignore", "-q", "review/tables.html"], cwd=ROOT)
    assert result.returncode == 0
