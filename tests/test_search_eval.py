"""평가 질문지로 검색 정답률을 검증한다. PLAN 3단계의 완료 기준은 상위 5개 정답률 80%다."""

import sys
from pathlib import Path

import numpy as np
import pytest

from search import CHUNK_DIR, DEFAULT_MODEL, INDEX_DIR, Searcher, cached_vectors, load_chunks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval_search import evaluate, group_scores, hit_rank, load_questions  # noqa: E402

TARGET = 0.8


def question(regulation="선수 규정", evidence="최소 5개월"):
    return {"번호": "Q01", "유형": "내용", "질문": "계약 기간", "정답 규정명": regulation,
            "근거 문구": evidence}


def result(regulation, body):
    return {"regulation": regulation, "body": body}


def test_hit_rank_ignores_whitespace():
    results = [result("선수 규정", "무관"), result("선수 규정", "최소\n5개월에서")]
    assert hit_rank(results, question()) == 2


def test_hit_rank_needs_all_evidence():
    gold = question("선수 규정||경기 규정", "최소 5개월||좌석 수")
    first = result("선수 규정", "최소 5개월")
    second = result("경기 규정", "좌석 수")
    assert hit_rank([first], gold) is None
    assert hit_rank([first, result("경기 규정", "무관"), second], gold) == 3


def test_group_scores_split_base_and_added():
    assert group_scores({"Q01": 1, "Q21": None, "Q22": 2, "Q31": None}) == ("1/2", "1/2")


def test_hit_rank_requires_same_regulation():
    assert hit_rank([result("경기 규정", "최소 5개월")], question()) is None


def test_evaluate_counts_by_type():
    class Stub:
        def search(self, query, k, mode):
            return [result("선수 규정", "최소 5개월")]

    missed = {**question(evidence="없는 문구"), "번호": "Q02", "유형": "표"}
    outcome = evaluate(Stub(), [question(), missed], "keyword")
    assert outcome["hit"] == 0.5
    assert outcome["missed"] == ["Q02"]
    assert outcome["by_type"] == {"내용": [1, 1], "표": [0, 1]}


class CountingEncoder:
    name = "org/fake"

    def __init__(self):
        self.calls = 0

    def encode_passages(self, texts):
        self.calls += 1
        return np.ones((len(texts), 2))


def test_cached_vectors_reuse_and_refresh(tmp_path):
    encoder = CountingEncoder()
    cached_vectors(encoder, [{"text": "가"}], "article", tmp_path)
    cached_vectors(encoder, [{"text": "가"}], "article", tmp_path)
    assert encoder.calls == 1
    cached_vectors(encoder, [{"text": "나"}], "article", tmp_path)  # 개수는 같고 내용만 다름
    assert encoder.calls == 2


@pytest.fixture(scope="module")
def chunks():
    if not (CHUNK_DIR / "article.jsonl").exists():
        pytest.skip("청크 없음: src/chunk.py를 먼저 실행할 것")
    return load_chunks("article")


def test_keyword_search_meets_target(chunks):
    assert evaluate(Searcher(chunks), load_questions(), "keyword")["hit"] >= TARGET


def test_article_number_questions_are_all_found(chunks):
    questions = [q for q in load_questions() if q["유형"] == "조번호"]
    outcome = evaluate(Searcher(chunks), questions, "keyword")
    assert outcome["hit"] == 1.0
    assert outcome["top1"] == 1.0


@pytest.mark.slow
def test_hybrid_search_meets_target(chunks):
    """실제 임베딩 모델을 쓴다. 모델이 캐시에 없으면 처음 한 번 내려받는다."""
    from search import OnnxEncoder

    encoder = OnnxEncoder(DEFAULT_MODEL)
    searcher = Searcher(chunks, encoder=encoder, vectors=cached_vectors(encoder, chunks, "article", INDEX_DIR))
    outcome = evaluate(searcher, load_questions(), "hybrid")
    assert outcome["hit"] >= TARGET
    assert "Q22" not in outcome["missed"]  # 키워드 1위 보장
