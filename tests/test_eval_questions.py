"""평가 질문지 형식과 정답 근거 검증."""

import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from chunk import EXTRACTED_DIR, article_chunks, load_documents
from enforcement import enforcement_notice

QUESTIONS = Path(__file__).resolve().parent.parent / "eval" / "questions.csv"
COLUMNS = ["번호", "유형", "질문", "정답 규정명", "정답 조번호", "항", "쪽", "정답 요지", "근거 문구"]
# 질문이 다루는 주제가 규정에 없음을 확인하는 패턴
ABSENT = {"Q19": r"주차.{0,8}(요금|비용|가격)", "Q20": r"(?<!지)연금"}


def load_rows():
    with QUESTIONS.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_columns():
    assert list(load_rows()[0]) == COLUMNS


def test_question_mix():
    rows = load_rows()
    assert len(rows) == 21
    assert len({r["번호"] for r in rows}) == 21
    counts = Counter(r["유형"] for r in rows)
    assert counts["없음"] == 2
    assert min(counts["조번호"], counts["내용"], counts["표"]) >= 3


def test_table_questions_cover_docs_09_and_15():
    regulations = {r["정답 규정명"] for r in load_rows() if r["유형"] == "표"}
    assert regulations == {"상벌 규정", "K리그 클럽 라이선싱 규정"}


def test_absent_questions_have_no_gold_article():
    for row in load_rows():
        if row["유형"] == "없음":
            assert not row["정답 규정명"] and not row["정답 조번호"] and not row["근거 문구"]


@pytest.fixture(scope="module")
def chunks():
    docs = load_documents(EXTRACTED_DIR)
    if len(docs) < 17:
        pytest.skip("추출 결과 없음: src/extract.py를 먼저 실행할 것")
    return [c for doc in docs for c in article_chunks(doc)]


def test_gold_evidence_exists_in_gold_article(chunks):
    for row in load_rows():
        if row["유형"] == "없음":
            continue
        found = [
            c for c in chunks
            if c["regulation"] == row["정답 규정명"] and c["article_no"] == row["정답 조번호"]
        ]
        assert found, row["번호"]
        body = squeeze("".join(c["body"] for c in found))
        assert squeeze(row["근거 문구"]) in body, row["번호"]
        pages = {p for c in found for p in c["pages"]}
        assert int(row["쪽"]) in pages, row["번호"]


def test_evidence_is_on_stated_page():
    docs = {d["title"]: d for d in load_documents(EXTRACTED_DIR)}
    if len(docs) < 17:
        pytest.skip("추출 결과 없음: src/extract.py를 먼저 실행할 것")
    for row in load_rows():
        if row["유형"] == "없음":
            continue
        page = docs[row["정답 규정명"]]["pages"][int(row["쪽"]) - 1]
        assert squeeze(row["근거 문구"]) in squeeze(page["text"]), row["번호"]


def test_enforcement_question_matches_notice(chunks):
    row = next(r for r in load_rows() if r["유형"] == "시행일")
    found = next(c for c in chunks if c["regulation"] == row["정답 규정명"])
    notice = enforcement_notice(found["enforcement_date"], today=date(2026, 11, 6))
    assert notice == "시행 전 (2027-01-01 시행 예정)"
    assert notice in row["정답 요지"]


def test_absent_topics_are_really_absent(chunks):
    bodies = "\n".join(c["body"] for c in chunks)
    for number, pattern in ABSENT.items():
        assert not re.search(pattern, bodies), number
