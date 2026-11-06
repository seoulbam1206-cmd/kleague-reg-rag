"""평가 질문지로 검색 방식을 비교해 eval/search_results.md에 저장한다.

    python scripts/eval_search.py                # 키워드만 (모델 불필요)
    python scripts/eval_search.py --model intfloat/multilingual-e5-small

판정: 근거 문구가 상위 k개 청크 본문에 있으면 맞은 것으로 본다. 조문 청크와 고정 길이
청크를 같은 기준으로 비교하기 위해서다. 규정에 없는 내용을 묻는 질문은 제외한다.
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from search import Searcher, OnnxEncoder, cached_vectors, load_chunks  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.csv"
RESULTS = ROOT / "eval" / "search_results.md"
TOP_K = 5
CHUNKERS = ("article", "fixed")


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row["유형"] != "없음"]


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def hit_rank(results: list[dict], question: dict) -> int | None:
    """근거 문구가 들어 있는 첫 청크의 순위(1부터)를 돌려준다."""
    evidence = squeeze(question["근거 문구"])
    for rank, chunk in enumerate(results, 1):
        if chunk["regulation"] == question["정답 규정명"] and evidence in squeeze(chunk["body"]):
            return rank
    return None


def evaluate(searcher: Searcher, questions: list[dict], mode: str, k: int = TOP_K) -> dict:
    ranks = {q["번호"]: hit_rank(searcher.search(q["질문"], k=k, mode=mode), q) for q in questions}
    found = [r for r in ranks.values() if r is not None]
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for question in questions:
        by_type[question["유형"]][1] += 1
        by_type[question["유형"]][0] += ranks[question["번호"]] is not None
    return {
        "hit": len(found) / len(questions),
        "top1": sum(r == 1 for r in found) / len(questions),
        "mrr": sum(1 / r for r in found) / len(questions),
        "by_type": dict(by_type),
        "missed": [number for number, rank in ranks.items() if rank is None],
    }


def report(rows: list[dict], questions: list[dict]) -> str:
    lines = [
        "# 검색 평가 결과",
        "",
        f"답이 있는 {len(questions)}문항 기준. 근거 문구가 상위 {TOP_K}개 청크에 있으면 정답으로 본다.",
        "",
        "| 청킹 | 검색 | 임베딩 | 상위5 정답률 | 1위 정답률 | MRR | 유형별 | 틀린 문항 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        result = row["result"]
        types = ", ".join(f"{t} {a}/{b}" for t, (a, b) in result["by_type"].items())
        lines.append(
            f"| {row['chunker']} | {row['mode']} | {row['model']} | {result['hit']:.0%} | "
            f"{result['top1']:.0%} | {result['mrr']:.2f} | {types} | "
            f"{' '.join(result['missed']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], help="임베딩 모델 (반복 가능)")
    parser.add_argument("--no-save", action="store_true", help="결과 파일을 쓰지 않는다")
    args = parser.parse_args()

    questions = load_questions()
    rows = []
    for chunker in CHUNKERS:
        chunks = load_chunks(chunker)
        result = evaluate(Searcher(chunks), questions, "keyword")
        rows.append({"chunker": chunker, "mode": "keyword", "model": "-", "result": result})
    for model in args.model:
        encoder = OnnxEncoder(model)
        short = model.split("/")[-1]
        for chunker in CHUNKERS:
            chunks = load_chunks(chunker)
            vectors = cached_vectors(encoder, chunks, chunker)
            searcher = Searcher(chunks, encoder=encoder, vectors=vectors)
            for mode in ("embedding", "hybrid"):
                result = evaluate(searcher, questions, mode)
                rows.append({"chunker": chunker, "mode": mode, "model": short, "result": result})
    text = report(rows, questions)
    print(text)
    if not args.no_save:
        RESULTS.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
