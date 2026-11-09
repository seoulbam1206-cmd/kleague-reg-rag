"""PDF 추출 검증. 기대값은 manifest와 PDF 원문에서 확인한 값이다."""

import json
import re
from collections import Counter

import pdfplumber
import pytest

from extract import (
    PDF_DIR,
    classify_page,
    clean_text,
    extract_document,
    load_manual_pages,
    parse_revision,
    strip_page_marks,
    table_to_markdown,
)
from tables import load_tables

PAGE_COUNTS = {
    1: 24, 2: 15, 3: 9, 4: 9, 5: 20, 6: 14, 7: 8, 8: 20, 9: 34,
    10: 21, 11: 9, 12: 8, 13: 14, 14: 7, 15: 86, 16: 71, 17: 30,
}
EFFECTIVE = {
    1: ("2020-11-05", "기준"), 2: ("2020-11-05", "기준"), 3: ("2026-06-17", "개정"),
    4: ("2026-01-16", "개정"), 5: ("2026-01-16", "개정"), 6: ("2026-01-16", "개정"),
    7: ("2024-02-23", "개정"), 8: ("2026-06-17", "개정"), 9: ("2026-06-17", "개정"),
    10: ("2026-06-17", "개정"), 11: ("2026-01-16", "개정"), 12: ("2024-01-15", "제정"),
    13: ("2026-01-16", "개정"), 14: ("2024-11-29", "개정"), 15: ("2026-01-16", "개정"),
    16: ("2024-11-29", "개정"), 17: ("2026-06-29", "개정"),
}


@pytest.mark.parametrize(
    "memo, date, kind",
    [
        ("2026년 6월 17일 개정", "2026-06-17", "개정"),
        ("2026/01/16 개정", "2026-01-16", "개정"),
        ("K리그 정관 2020년 11월 05일 기준", "2020-11-05", "기준"),
        ("2020년 11월 5일 기준", "2020-11-05", "기준"),
        ("2024년 1월 15일 제정", "2024-01-15", "제정"),
    ],
)
def test_parse_revision(memo, date, kind):
    assert parse_revision(memo) == {"date": date, "kind": kind}


def test_parse_revision_rejects_unknown_format():
    with pytest.raises(ValueError):
        parse_revision("개정 예정")


def test_clean_text_turns_nul_into_word_space():
    assert clean_text("홈\x00구단이\x00 지정한") == "홈 구단이 지정한"


@pytest.mark.parametrize(
    "mark",
    [
        "3",
        "- 4 -",
        "K리그 클럽 라이선싱 규정 (2026) 29",
        "경기장 시설기준 가이드라인 (2024) 2",
        "- 10 - 총재선거관리규정 (20.11.05)",
    ],
)
def test_page_marks_are_removed_at_page_end(mark):
    assert strip_page_marks(["본문", mark]) == ["본문"]


def test_numbers_inside_page_are_kept():
    lines = ["본문", "2026", "끝"]
    assert strip_page_marks(lines) == lines


def test_classify_cover_and_toc():
    assert classify_page(["정 관", "한국프로축구연맹"], 1, 24, False) == "cover"
    assert classify_page(["목 차", "제1조. 명칭 5"], 2, 24, False) == "toc"
    assert classify_page(["- 목 차 -", "제1조.목적"], 2, 30, False) == "toc"
    assert classify_page(["제24조. 임원의 직무 12", "제25조. 감사의 직무 12"], 3, 24, True) == "toc"


def test_classify_body_after_toc():
    lines = ["선수 규정", "제1조 프로계약", "프로계약은 연맹에 등록하는 모든 선수를 대상으로 한다."]
    assert classify_page(lines, 3, 20, True) == "body"


def test_classify_history_and_back_cover():
    assert classify_page(["개정내역", "2024/01/15 개정"], 7, 8, False) == "history"
    assert classify_page(["한국프로축구연맹", "www.kleague.com"], 8, 8, False) == "back_cover"


def test_table_to_markdown_keeps_rows_and_columns():
    assert table_to_markdown([["구분", "내용"], ["우승", None]]) == (
        "| 구분 | 내용 |\n| --- | --- |\n| 우승 |  |"
    )


def test_single_column_box_is_plain_text():
    assert table_to_markdown([["첫 줄"], ["둘째 줄"]]) == "첫 줄\n둘째 줄"


def test_manual_page_is_loaded():
    pages = load_manual_pages()
    assert ("15_K리그_클럽_라이선싱_규정_20240327.pdf", 30) in pages


@pytest.fixture(scope="module")
def docs():
    if len(list(PDF_DIR.glob("*.pdf"))) < 17:
        pytest.skip("규정 PDF 없음: scripts/download_regulations.py로 받을 것")
    entries = json.loads((PDF_DIR / "manifest.json").read_text(encoding="utf-8"))
    manual = load_manual_pages()
    return {e["no"]: extract_document(e, manual=manual) for e in entries}


def page(docs, no, page_no):
    return docs[no]["pages"][page_no - 1]


@pytest.mark.slow
def test_all_documents_keep_page_counts(docs):
    assert {no: len(d["pages"]) for no, d in docs.items()} == PAGE_COUNTS


@pytest.mark.slow
def test_effective_dates(docs):
    actual = {no: (d["effective_date"], d["effective_kind"]) for no, d in docs.items()}
    assert actual == EFFECTIVE


@pytest.mark.slow
def test_enforcement_date_is_separate_from_effective_date(docs):
    assert docs[16]["effective_date"] == "2024-11-29"
    assert docs[16]["enforcement_date"] == "2027-01-01"
    assert "제28조" in docs[16]["enforcement_basis"]


@pytest.mark.slow
def test_effective_date_is_not_registered_date(docs):
    assert docs[15]["registered"] == "2024.03.27"
    assert docs[15]["effective_date"] == "2026-01-16"


@pytest.mark.slow
def test_no_control_or_broken_characters(docs):
    for d in docs.values():
        text = "\n".join(p["text"] for p in d["pages"])
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f�-]", text), d["no"]


@pytest.mark.slow
def test_word_spacing_survives_in_doc_17(docs):
    assert "홈 구단이 지정한 경기장에서 경기를 개최하기 위해서는" in page(docs, 17, 5)["text"]


@pytest.mark.slow
def test_every_body_page_has_text_and_no_page_mark(docs):
    for d in docs.values():
        for p in d["pages"]:
            if p["kind"] == "body":
                assert p["text"].strip(), (d["no"], p["page"])
                last = p["text"].splitlines()[-1].strip()
                assert not re.fullmatch(r"-?\s*\d{1,3}\s*-?", last), (d["no"], p["page"])


@pytest.mark.slow
def test_page_kinds(docs):
    assert page(docs, 1, 1)["kind"] == "cover"
    assert [page(docs, 1, n)["kind"] for n in (2, 3, 4)] == ["toc"] * 3
    assert [page(docs, 9, n)["kind"] for n in (2, 3)] == ["toc"] * 2
    assert page(docs, 16, 1)["kind"] == "cover"
    assert page(docs, 17, 1)["kind"] == "cover"
    assert page(docs, 17, 2)["kind"] == "toc"
    assert page(docs, 12, 7)["kind"] == "history"
    assert page(docs, 2, 1)["kind"] == "body"
    assert page(docs, 6, 1)["kind"] == "body"


@pytest.mark.slow
def test_removed_pages_have_no_text(docs):
    for d in docs.values():
        for p in d["pages"]:
            if p["kind"] in ("cover", "toc", "back_cover"):
                assert p["text"] == ""


@pytest.mark.slow
def test_table_with_dashed_rules_is_preserved(docs):
    text = page(docs, 9, 29)["text"]
    assert "| 라운드별 팀 벌점 합산 | 제재금 |  |" in text
    assert "| 41점~45점 | 50만원 | 25만원 |" in text
    assert "| 51점~ | 200만원 | 100만원 |" in text


@pytest.mark.slow
def test_award_table_is_preserved(docs):
    assert "| 구분 | 내용 | 선정 방법 | 포상 시기 |" in page(docs, 9, 30)["text"]


@pytest.mark.slow
def test_licensing_table_is_preserved(docs):
    text = page(docs, 15, 10)["text"]
    assert "| 대회 | 클럽 라이선싱 절차 | 라이선스 | 라이센서 |" in text
    assert "| K리그1 | K리그1 클럽 라이선싱 | K리그1 라이선스 | 연맹 |" in text


@pytest.mark.slow
def test_flowchart_page_uses_reviewed_summary(docs):
    p = page(docs, 15, 30)
    assert p["kind"] == "manual"
    assert "CLA에 서면으로 재심 청구 의사를 통보" in p["text"]
    assert "흐름도(30쪽)에는 AB 제출로 되어 있으나 본문 기준으로 작성함" in p["text"]


def _letters(text: str) -> Counter:
    return Counter(re.sub(r"[\s|\-]", "", clean_text(text)))


@pytest.mark.slow
def test_no_text_lost_or_duplicated(docs):
    """표 처리 전후로 글자가 사라지거나 중복되면 안 된다 (쪽번호 제외).

    표 구조로 복원한 쪽은 뺀다. 행마다 열 제목과 합쳐진 칸 값을 반복하므로 글자 수가 일부러 다르다.
    그 쪽의 글자는 tests/test_tables.py가 원본 표 영역과 칸 단위로 대조한다 (사용자 승인 2026-11-09).
    """
    restored = {(file, page) for file, page, _ in load_tables()}
    for d in docs.values():
        with pdfplumber.open(PDF_DIR / d["file"]) as pdf:
            for p in d["pages"]:
                if p["kind"] not in ("body", "history") or (d["file"], p["page"]) in restored:
                    continue
                raw = (pdf.pages[p["page"] - 1].extract_text() or "").splitlines()
                raw = strip_page_marks([clean_text(x) for x in raw if clean_text(x)])
                assert _letters("\n".join(raw)) == _letters(p["text"]), (d["no"], p["page"])
