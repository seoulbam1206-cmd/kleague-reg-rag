"""청킹 검증. 기대값은 원본 PDF의 목차와 본문에서 직접 확인한 값이다."""

import re
from collections import Counter

import pytest

from chunk import (
    EXTRACTED_DIR,
    FIXED_OVERLAP,
    FIXED_SIZE,
    MAX_CHARS,
    article_chunks,
    build_segments,
    fixed_chunks,
    is_article_title,
    load_documents,
    normalize_title,
    parse_structure,
)
from extract import clean_text

# 문서별 본문 첫 조와 마지막 조 (원본 PDF 목차 기준)
FIRST_LAST = {
    1: (("제1조", "명칭"), ("제63조", "규정의 제정")),
    2: (("제1조", "목적"), ("제33조", "그 밖의 사항")),
    3: (("제1조", "프로클럽의 자격요건"), ("제7조", "클럽 및 연고지")),
    4: (("제1조", "목적"), ("제6조", "기타")),
    5: (("제1조", "프로계약"), ("제27조", "보칙")),
    6: (("제1조", "목적"), ("제36조", "이의신청")),
    7: (("제1조", "목적"), ("제12조", "시행일")),
    8: (("제1조", "명칭"), ("제33조", "유형별 제재")),
    9: (("제1조", "목적"), ("제28조", "회의의 비공개 등")),
    10: (("제1조", "경기장의 유지"), ("제39조", "친선경기 등")),
    11: (("제1조", "목적"), ("제13조", "기타")),
    12: (("제1조", "목적"), ("제16조", "연맹 상벌위원회의 징계")),
    13: (("제1조", "목적"), ("제23조", "재심")),
    14: (("제1조", "목적"), ("제5조", "추가 예산 심사")),
    15: (("제1조", "용어 및 정의"), ("제20조", "사회적 책임 기준")),
    16: (("제1조", "용어 및 정의"), ("제28조", "시행")),
    17: (("제1조", "목적"), ("제18조", "비상계획")),
}


def doc_with(lines, no=1, title="테스트 규정"):
    return {
        "no": no, "title": title, "effective_date": "2026-01-16", "effective_kind": "개정",
        "enforcement_date": "2026-01-16",
        "pages": [{"page": 3, "kind": "body", "text": "\n".join(lines)}],
    }


def articles(doc):
    return [s["article_no"] for s in parse_structure(doc).segments]


@pytest.mark.parametrize(
    "title", ["프로계약 ········", "프로계약 ····· 3", "명칭 5", "공식경기는 매년 3월부터 실시한다."],
)
def test_toc_entries_and_sentences_are_not_titles(title):
    assert is_article_title(title) is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("목 적", "목적"),
        ("방 향", "방향"),
        ("[명칭]", "명칭"),
        ("(연맹 상벌위원회의 징계)", "연맹 상벌위원회의 징계"),
        ("용어 및 정의 (Terms and Definitions)", "용어 및 정의"),
        ("안전 요원", "안전 요원"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_toc_line_in_body_does_not_open_article():
    assert articles(doc_with(["제1조. 프로계약 ·········· 3", "본문"])) == ["서문"]


def test_inline_references_do_not_open_article():
    doc = doc_with([
        "제1조 목적", "내용은 아래와 같다.",
        "제2조에 따른 사항을 따른다.",
        "제9조의 대상자는 제외한다.",
        "제2조 제2항을 준용한다.",
        "제2조 정의", "내용",
    ])
    assert articles(doc) == ["서문", "제1조", "제2조"]


def test_out_of_order_number_does_not_open_article():
    doc = doc_with(["제1조 목적", "내용", "제12조 공식경기 일정", "제2조 정의"])
    assert articles(doc) == ["서문", "제1조", "제2조"]


def test_branch_article_is_recognized():
    doc = doc_with(["제2조 정의", "내용"][:0] + ["제1조 목적", "제1조의1 적용", "제2조 정의"])
    assert articles(doc) == ["서문", "제1조", "제1조의1", "제2조"]


def test_addendum_article_is_separate_from_body_article():
    doc = doc_with(["제1조 목적", "본문 내용", "부 칙", "제1조 시행일", "부칙 내용"])
    chunks = article_chunks(doc)
    assert [c["article_no"] for c in chunks] == ["제1조", "부칙 제1조"]
    assert [c["part"] for c in chunks] == ["본문", "부칙"]


def test_chapter_addendum_heading():
    doc = doc_with(["제1조 목적", "내용", "제12장 부칙", "제1조 시행일", "내용"])
    assert articles(doc)[-1] == "부칙 제1조"


def test_spread_title_is_joined():
    chunks = article_chunks(doc_with(["제1조 목 적", "내용"]))
    assert chunks[0]["article_title"] == "목적"


def test_doc_15_article_and_annex_patterns():
    doc = doc_with(
        ["1조 (Article 1): 용어 및 정의 (Terms and Definitions)", "내용",
         "20조에 명시된 일부 기준을 적용한다.",
         "2조 (Article 2): 소개 (Introduction)", "2.1 내용",
         "별첨 1 (Annex 1): K리그 참가를 위한 예외 정책", "내용"],
        no=15,
    )
    chunks = article_chunks(doc)
    assert [(c["article_no"], c["article_title"]) for c in chunks] == [
        ("제1조", "용어 및 정의"),
        ("제2조", "소개"),
        ("별첨1", "K리그 참가를 위한 예외 정책"),
    ]
    assert "20조에 명시된" in chunks[0]["body"]


def test_long_article_splits_by_paragraph_and_keeps_title():
    filler = "가" * 600
    doc = doc_with(["제1조 긴 조문", f"① {filler}", f"② {filler}", f"③ {filler}"])
    chunks = article_chunks(doc)
    assert [c["paragraph"] for c in chunks] == ["①", "②", "③"]
    assert all(c["article_no"] == "제1조" for c in chunks)
    assert all(c["text"].startswith("[테스트 규정] 제1조 긴 조문\n") for c in chunks)


def test_short_paragraphs_stay_together():
    chunks = article_chunks(doc_with(["제1조 짧은 조문", "① 가", "② 나"]))
    assert len(chunks) == 1
    assert chunks[0]["paragraph"] == ""


def test_split_table_repeats_header():
    rows = [f"| 항목{i} | {'나' * 300} |" for i in range(6)]
    doc = doc_with(["제1조 표", "| 구분 | 내용 |", "| --- | --- |", *rows])
    chunks = article_chunks(doc)
    assert len(chunks) > 1
    assert all("| 구분 | 내용 |" in c["text"] for c in chunks)
    assert "| 구분 | 내용 |" not in chunks[1]["body"]


def test_new_table_does_not_inherit_previous_header():
    first = [f"| 가{i} | {'나' * 300} |" for i in range(3)]
    second = [f"| 다{i} | {'라' * 300} |" for i in range(3)]
    doc = doc_with([
        "제1조 표", "| 앞 표 | 내용 |", "| --- | --- |", *first,
        "| 뒤 표 | 내용 |", "| --- | --- |", *second,
    ])
    starting = [c for c in article_chunks(doc) if c["body"].startswith("| 뒤 표 |")]
    assert len(starting) == 1
    assert "앞 표" not in starting[0]["text"]


def test_fixed_chunks_ignore_articles():
    doc = doc_with(["제1조 목적", "가" * 2000])
    chunks = fixed_chunks(doc, size=800, overlap=100)
    assert all(len(c["body"]) <= 800 for c in chunks)
    assert "article_no" not in chunks[0]
    assert chunks[0]["body"][-100:] == chunks[1]["body"][:100]


def test_fixed_chunks_reject_bad_overlap():
    with pytest.raises(ValueError):
        fixed_chunks(doc_with(["내용"]), size=100, overlap=100)


@pytest.fixture(scope="module")
def docs():
    loaded = load_documents(EXTRACTED_DIR)
    if len(loaded) < 17:
        pytest.skip("추출 결과 없음: src/extract.py를 먼저 실행할 것")
    return {d["no"]: d for d in loaded}


@pytest.fixture(scope="module")
def chunks(docs):
    return {no: article_chunks(d) for no, d in docs.items()}


def find(chunks, no, article_no):
    found = [c for c in chunks[no] if c["article_no"] == article_no]
    assert found, (no, article_no)
    return found


def body_of(chunks, no, article_no):
    return "\n".join(c["body"] for c in find(chunks, no, article_no))


def test_first_and_last_article_match_contents(chunks):
    for no, (first, last) in FIRST_LAST.items():
        body = [c for c in chunks[no] if c["part"] == "본문" and c["article_no"].startswith("제")]
        assert (body[0]["article_no"], body[0]["article_title"]) == first, no
        assert (body[-1]["article_no"], body[-1]["article_title"]) == last, no


def test_body_articles_are_consecutive(chunks):
    for no, doc_chunks in chunks.items():
        numbers = []
        for c in doc_chunks:
            match = re.fullmatch(r"제(\d+)조", c["article_no"])
            if c["part"] == "본문" and match and int(match[1]) not in numbers:
                numbers.append(int(match[1]))
        assert numbers == list(range(1, numbers[-1] + 1)), no


@pytest.mark.parametrize(
    "no, article_no, title, phrase",
    [
        (1, "제1조", "명칭", "이 법인의 명칭은 사단법인 한국프로축구연맹"),
        (2, "제1조", "목적", "총재 선거가 회원의"),
        (5, "제1조", "프로계약", "프로계약은 연맹에 등록하는 모든 선수를 대상으로"),
        (5, "제2조", "계약기간", "최소 5개월에서 최대 5년"),
        (10, "제1조", "경기장의 유지", "안전하고 쾌적한 상태에서 홈경기를 실시"),
        (10, "제2조", "경기장", "그라운드는 길이 105m, 너비 68m를 권고"),
        (9, "별표1 13", "벌점 및 제재금", "| 41점~45점 | 50만원 | 25만원 |"),
        (9, "별표2 1", "단체상 및 개인상 시상 기준", "| 구분 | 내용 | 선정 방법 | 포상 시기 |"),
        (15, "제4조", "적용 범위", "4.1 본 규정은 AFC, 협회 및 연맹 주최 클럽 대회"),
        (15, "제11조", "주요평가절차", "11.5.5 재심청구인은 FIB 결정문을 수신한 날로부터 7일 이내 CLA에"),
        (15, "제12조", "AFC 클럽 대회 참가자격", "12.1.1 AFC 클럽 대회 참가를 위해서는"),
        (17, "제1조", "목적", ""),
        (17, "별첨1 제1조", "목적", "가변석의 체계적인 관리와 운영으로"),
        (6, "부칙 제1조", "시행일", "이 규정은 이사회 의결이 있는 날부터 시행한다."),
    ],
)
def test_known_articles(chunks, no, article_no, title, phrase):
    assert find(chunks, no, article_no)[0]["article_title"] == title
    assert phrase in body_of(chunks, no, article_no)


def test_body_and_addendum_first_article_differ(chunks):
    assert "연맹" in body_of(chunks, 6, "제1조")
    assert body_of(chunks, 6, "제1조") != body_of(chunks, 6, "부칙 제1조")
    assert find(chunks, 6, "부칙 제1조")[0]["part"] == "부칙"


def test_every_chunk_has_metadata(docs, chunks):
    for no, doc_chunks in chunks.items():
        assert doc_chunks, no
        for c in doc_chunks:
            assert c["regulation"] == docs[no]["title"]
            assert c["article_no"]
            assert c["effective_date"] == docs[no]["effective_date"]
            assert c["enforcement_date"] == docs[no]["enforcement_date"]
            assert c["pages"]
            assert c["body"].strip()
            assert c["text"].startswith(f"[{docs[no]['title']}] ")


def test_chunk_ids_are_unique(chunks):
    ids = [c["id"] for doc_chunks in chunks.values() for c in doc_chunks]
    assert len(ids) == len(set(ids))


def test_chunks_respect_max_size(chunks):
    for doc_chunks in chunks.values():
        for c in doc_chunks:
            assert len(c["body"]) <= MAX_CHARS, c["id"]


def test_no_chunk_comes_from_toc_or_cover(docs, chunks):
    for no, doc_chunks in chunks.items():
        removed = {
            p["page"] for p in docs[no]["pages"] if p["kind"] in ("cover", "toc", "back_cover")
        }
        for c in doc_chunks:
            assert not removed & set(c["pages"]), c["id"]
            assert not re.search(r"[·.]{5,}", c["body"]), c["id"]


def test_split_chunks_keep_article_title(chunks):
    split = [c for c in chunks[15] if c["article_no"] == "제11조"]
    assert len(split) > 1
    assert all(c["text"].startswith("[K리그 클럽 라이선싱 규정] 제11조 주요평가절차\n") for c in split)


def test_manual_summary_is_its_own_chunk(chunks):
    found = [c for c in chunks[15] if c["part"] == "수동요약"]
    assert found
    assert all(c["pages"] == [30] for c in found)
    assert "CLA에 서면으로 재심 청구 의사를 통보" in "\n".join(c["body"] for c in found)


def _letters(text: str) -> Counter:
    return Counter(re.sub(r"\s", "", clean_text(text)))


def test_no_text_lost_or_duplicated(docs, chunks):
    """본문 글자는 청크 또는 메타데이터로 옮긴 표제 줄 중 한 곳에 정확히 한 번 있어야 한다."""
    for no, doc in docs.items():
        original = "\n".join(
            p["text"] for p in doc["pages"] if p["kind"] in ("body", "manual", "history")
        )
        _, dropped = build_segments(doc)
        rebuilt = "\n".join([*(c["body"] for c in chunks[no]), *dropped])
        assert _letters(original) == _letters(rebuilt), no


def test_dropped_lines_are_only_structure(docs):
    pattern = re.compile(
        r"^(제\s*\d+\s*[조장절]|\d+장\.|\d+조 \(Article|[ⅠⅡⅢⅣⅤⅥⅦ]+\.|부\s*칙|\[별표|별첨|별지|\d{1,2}\. )"
    )
    allowed = {
        1: ["K리그 정관"],
        2: ["공 고", "총재 후보자 등록신청서", "총재 선거 후보자 명단", "대 총재", "사실증명확인서",
            "임원결격사유의 부존재"],
        9: ["유형별 징계 기준", "포상의 대상 및 기준"],
        17: ["임시관람석", "Safety and Security", "안전 가이드라인"],
    }
    for no, doc in docs.items():
        _, dropped = build_segments(doc)
        for line in dropped:
            if pattern.match(line) or line.replace(" ", "") == doc["title"].replace(" ", ""):
                continue
            assert any(word in line for word in allowed.get(no, [])) or len(line) < 40, (no, line)


def test_fixed_chunks_cover_whole_document(docs):
    for no, doc in docs.items():
        result = fixed_chunks(doc)
        assert all(len(c["body"]) <= FIXED_SIZE for c in result)
        text = "\n".join(
            p["text"] for p in doc["pages"]
            if p["kind"] in ("body", "manual", "history") and p["text"]
        )
        rebuilt = _letters("".join(c["body"][FIXED_OVERLAP if i else 0:] for i, c in enumerate(result)))
        assert sum(rebuilt.values()) >= sum(_letters(text).values()) * 0.99, no
        assert all(c["pages"] for c in result)
