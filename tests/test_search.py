"""검색 검증. 실제 모델이 필요한 평가는 tests/test_search_eval.py에 있다."""

import zlib

import numpy as np
import pytest

from search import (
    BM25,
    CHUNK_DIR,
    Searcher,
    article_keys,
    find_regulation,
    find_regulations,
    keep_in_top,
    load_chunks,
    rrf,
    tokenize,
)


def chunk(regulation, article_no, body):
    return {
        "regulation": regulation, "article_no": article_no, "body": body,
        "text": f"[{regulation}] {article_no}\n{body}",
    }


SAMPLE = [
    chunk("선수 규정", "제1조", "프로계약은 연맹에 등록하는 선수를 대상으로 한다."),
    chunk("선수 규정", "제2조", "계약 기간은 최소 5개월에서 최대 5년으로 한다."),
    chunk("경기 규정", "제2조", "경기장은 시설 안전기준을 충족하여야 한다."),
    chunk("경기 규정", "부칙 제1조", "이 규정은 이사회 의결일부터 시행한다."),
    chunk("K리그 클럽 라이선싱 규정", "제11조", "11.5.5 재심청구인은 7일 이내 통보한다.\n11.6.10 CAS에 청구한다."),
    chunk("상벌 규정", "별표1 13", "| 41점~45점 | 50만원 | 25만원 |"),
]


class FakeEncoder:
    """글자 단위 해시 임베딩. 테스트에서 실제 모델을 대신한다.

    파이썬 hash()는 실행마다 값이 바뀌어(PYTHONHASHSEED) 순위가 흔들렸다. 고정된 crc32를 쓴다.
    """

    name = "fake"

    def _vector(self, text):
        vector = np.zeros(64)
        for char in text:
            vector[zlib.crc32(char.encode("utf-8")) % 64] += 1
        return vector / (np.linalg.norm(vector) or 1)

    def encode_passages(self, texts):
        return np.array([self._vector(t) for t in texts])

    def encode_query(self, text):
        return self._vector(text)


def test_tokenize_adds_hangul_bigrams_for_particles():
    assert "계약" in tokenize("계약기간은")
    assert "기간" in tokenize("계약기간은")


def test_tokenize_keeps_numbers_and_english():
    tokens = tokenize("좌석 수 5,000석, VAR 11.5.5")
    assert "5,000" in tokens
    assert "var" in tokens
    assert "11.5.5" in tokens


def test_bm25_prefers_matching_document():
    scores = BM25(["사과 바나나", "포도 수박", "포도 포도 포도"]).scores("수박")
    assert int(np.argmax(scores)) == 1


def test_rrf_rewards_agreement():
    assert rrf([[1, 2, 3], [2, 1, 3]])[:2] == [1, 2]
    assert rrf([[1, 2], [3, 2]])[0] == 2


@pytest.mark.parametrize(
    "query, expected",
    [
        ("선수 규정 제2조", "선수 규정"),
        ("클럽 라이선싱 규정 11.6.10", "K리그 클럽 라이선싱 규정"),
        ("K리그 클럽 라이선싱 규정의 기준", "K리그 클럽 라이선싱 규정"),
        ("오늘 날씨는 어떤가요", None),
    ],
)
def test_find_regulation(query, expected):
    titles = ["선수 규정", "경기 규정", "K리그 클럽 라이선싱 규정", "상벌 규정"]
    assert find_regulation(query, titles) == expected


def test_find_regulation_picks_longest_name():
    titles = ["클럽 규정", "재정건전화 규정", "클럽 재정건전화 준수 세칙"]
    assert find_regulation("클럽 재정건전화 준수 세칙 제2조", titles) == "클럽 재정건전화 준수 세칙"


@pytest.mark.parametrize(
    "query, keys",
    [
        ("선수 규정 제2조", ["제2조"]),
        ("클럽 규정 3조", ["제3조"]),
        ("재정 세칙 제2조의1", ["제2조의1"]),
        ("중재 규정 부칙 제1조", ["부칙 제1조"]),
        ("안전 가이드라인 별첨 1의 제1조", ["별첨1 제1조"]),
        ("상벌 규정 별표1 13", ["별표1 13"]),
        ("총재선거 별지 서식 3", ["별지 서식 3"]),
        ("라이선싱 규정 별첨 2", ["별첨2"]),
    ],
)
def test_article_keys(query, keys):
    assert article_keys(query) == keys


def test_direct_article_is_first():
    results = Searcher(SAMPLE).search("선수 규정 제2조", mode="keyword")
    assert (results[0]["regulation"], results[0]["article_no"]) == ("선수 규정", "제2조")


def test_same_number_in_other_regulation_is_not_first():
    results = Searcher(SAMPLE).search("경기 규정 제2조", mode="keyword")
    assert results[0]["regulation"] == "경기 규정"


def test_addendum_reference_is_first():
    results = Searcher(SAMPLE).search("경기 규정 부칙 제1조는?", mode="keyword")
    assert results[0]["article_no"] == "부칙 제1조"


def test_dotted_clause_reference_is_first():
    results = Searcher(SAMPLE).search("클럽 라이선싱 규정 11.6.10 내용", mode="keyword")
    assert results[0]["article_no"] == "제11조"


def test_find_regulations_in_order_of_appearance():
    titles = ["선수 규정", "경기 규정", "K리그 클럽 라이선싱 규정"]
    found = find_regulations("경기 규정과 클럽 라이선싱 규정을 비교", titles)
    assert [title for title, _ in found] == ["경기 규정", "K리그 클럽 라이선싱 규정"]


def test_find_regulations_ignores_name_inside_longer_name():
    titles = ["재정건전화 규정", "클럽 재정건전화 준수 세칙"]
    found = find_regulations("클럽 재정건전화 준수 세칙 제2조", titles)
    assert [title for title, _ in found] == ["클럽 재정건전화 준수 세칙"]


def test_find_regulations_splits_segments_by_name():
    found = dict(find_regulations("선수 규정 제2조와 경기 규정 제3조", ["선수 규정", "경기 규정"]))
    assert "제2조" in found["선수 규정"] and "제3조" not in found["선수 규정"]
    assert "제3조" in found["경기 규정"]


def test_repeated_name_counts_once():
    found = find_regulations("선수 규정과 선수 규정의 차이", ["선수 규정"])
    assert len(found) == 1


def test_comparison_returns_both_regulations():
    chunks = [chunk("선수 규정", f"제{i}조", f"계약 내용 {i}") for i in range(1, 7)]
    chunks.append(chunk("경기 규정", "제1조", "경기장 시설 안내"))
    results = Searcher(chunks).search("선수 규정과 경기 규정의 계약 내용 비교", k=3, mode="keyword")
    assert {c["regulation"] for c in results} == {"선수 규정", "경기 규정"}


def test_comparison_alternates_regulations():
    chunks = [chunk("선수 규정", f"제{i}조", "계약") for i in range(1, 4)]
    chunks += [chunk("경기 규정", f"제{i}조", "계약") for i in range(1, 4)]
    results = Searcher(chunks).search("선수 규정과 경기 규정의 계약", k=4, mode="keyword")
    assert [c["regulation"] for c in results] == ["선수 규정", "경기 규정"] * 2


def test_comparison_keeps_article_numbers_with_their_regulation():
    results = Searcher(SAMPLE).search("선수 규정 제2조와 경기 규정 제2조 비교", k=2, mode="keyword")
    assert [(c["regulation"], c["article_no"]) for c in results] == [
        ("선수 규정", "제2조"), ("경기 규정", "제2조"),
    ]


def test_single_regulation_query_is_not_split():
    results = Searcher(SAMPLE).search("선수 규정 계약", k=5, mode="keyword")
    assert results[0]["regulation"] == "선수 규정"


@pytest.mark.parametrize(
    "ranking, keep, k, expected",
    [
        ([1, 2, 3, 4, 5, 6, 7], [7], 5, [1, 2, 3, 4, 7, 5, 6]),  # 밖이면 k번째 자리로
        ([1, 2, 3, 4, 5, 6], [2], 5, [1, 2, 3, 4, 5, 6]),  # 이미 안이면 그대로
        ([1, 2, 3], [], 5, [1, 2, 3]),  # 키워드 결과가 없을 때
    ],
)
def test_keep_in_top(ranking, keep, k, expected):
    assert keep_in_top(ranking, keep, k) == expected


class ScriptedSearcher(Searcher):
    """키워드와 임베딩 순위를 지정해 융합 동작만 확인한다."""

    def __init__(self, chunks, keyword, embedding):
        super().__init__(chunks, encoder=FakeEncoder())
        self.keyword = keyword
        self.embedding = embedding

    def keyword_ranking(self, query):
        return self.keyword

    def embedding_ranking(self, query):
        return self.embedding


def many_chunks():
    return [chunk("안전 규정", f"제{i}조", f"내용 {i}") for i in range(1, 11)]


def test_hybrid_keeps_keyword_first_when_embedding_misses():
    keyword = [0, 1, 2, 3, 4, 5, 6]
    embedding = [1, 2, 3, 4, 5, 6, 7, 8, 9]  # 0번 청크를 찾지 못함
    searcher = ScriptedSearcher(many_chunks(), keyword, embedding)
    ids = [c["article_no"] for c in searcher.search("질문", k=5)]
    assert "제1조" in ids
    assert len(ids) == len(set(ids)) == 5


def test_embedding_first_is_not_guaranteed():
    keyword = [1, 2, 3, 4, 5, 6, 7, 8]
    embedding = [0, 1, 2, 3, 4, 5, 6, 7, 8]  # 0번은 임베딩 1위, 키워드에는 없음
    searcher = ScriptedSearcher(many_chunks(), keyword, embedding)
    ranking = rrf([keyword, embedding])
    assert 0 not in ranking[:5]
    assert "제1조" not in [c["article_no"] for c in searcher.search("질문", k=5)]


def test_keyword_guarantee_skips_direct_article_lookup():
    chunks = [chunk("선수 규정", f"제{i}조", f"내용 {i}") for i in range(1, 11)]
    searcher = ScriptedSearcher(chunks, [9, 1, 2], [1, 2, 3, 4, 5, 6, 7, 8, 0])
    results = searcher.search("선수 규정 제1조", k=2)
    assert results[0]["article_no"] == "제1조"
    assert "제10조" not in [c["article_no"] for c in results]


def test_article_number_without_regulation_is_not_forced():
    assert Searcher(SAMPLE).direct_hits("제2조는 무엇인가요") == set()


def test_unknown_topic_returns_no_keyword_results():
    assert Searcher(SAMPLE).search("zzzz", mode="keyword") == []


def test_k_limits_results():
    assert len(Searcher(SAMPLE).search("규정", k=2, mode="keyword")) <= 2


def test_hybrid_uses_both_rankings():
    searcher = Searcher(SAMPLE, encoder=FakeEncoder())
    assert searcher.search("계약 기간", k=3, mode="hybrid")[0]["article_no"] == "제2조"


def test_embedding_mode_requires_encoder():
    with pytest.raises(ValueError):
        Searcher(SAMPLE).search("계약", mode="embedding")


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        Searcher(SAMPLE).search("계약", mode="other")


@pytest.fixture(scope="module")
def real_chunks():
    if not (CHUNK_DIR / "article.jsonl").exists():
        pytest.skip("청크 없음: src/chunk.py를 먼저 실행할 것")
    return load_chunks("article")


@pytest.mark.parametrize(
    "query, regulation, article_no",
    [
        ("선수 규정 제2조", "선수 규정", "제2조"),
        ("K리그 정관 제20조", "K리그 정관", "제20조"),
        ("상벌 규정 제1조", "상벌 규정", "제1조"),
        ("중재위원회 운영 규정 부칙 제1조", "중재위원회 운영 규정", "부칙 제1조"),
        ("안전 가이드라인 별첨 1의 제1조", "K리그 안전 가이드라인", "별첨1 제1조"),
        ("클럽 라이선싱 규정 제4조", "K리그 클럽 라이선싱 규정", "제4조"),
        ("클럽 규정 제2조", "클럽 규정", "제2조"),
        ("상벌 규정 별표1 13", "상벌 규정", "별표1 13"),
        ("경기 규정 제39조", "경기 규정", "제39조"),
    ],
)
def test_direct_lookup_on_real_chunks(real_chunks, query, regulation, article_no):
    first = Searcher(real_chunks).search(query, mode="keyword")[0]
    assert (first["regulation"], first["article_no"]) == (regulation, article_no)
