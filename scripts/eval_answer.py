"""평가 질문 10개를 실제 LLM으로 돌려 eval/answer_results.md에 저장한다. 사람이 읽고 확인한다.

    python scripts/eval_answer.py

문항은 결과를 보기 전에 정했다. 유형별 대표 문항과, 앞 단계에서 위험하다고 표시한 문항이다.
규정 내용 일부가 .env의 LLM_PROVIDER 서버로 전송되므로 공개 규정 청크만 쓴다.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from answer import answer_question, build_searcher, create_llm  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.csv"
RESULTS = ROOT / "eval" / "answer_results.md"
REVIEW_SET = {
    "Q01": "조번호 대표",
    "Q07": "내용 대표",
    "Q14": "표 숫자 대표",
    "Q17": "표 안의 표: 행·열 구조가 풀린 값을 읽을 수 있는지",
    "Q21": "시행 전 표시",
    "Q22": "융합 약점: 융합 16위라 근거가 전달되지 않는 문항",
    "Q19": "규정에 없는 내용",
    "Q20": "규정에 없는 내용",
    "Q30": "규정 간 비교",
    "Q31": "규정명 없는 비교: 검색이 놓치는 문항",
}


def load_review_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row["번호"] in REVIEW_SET]


def render(rows: list[tuple[dict, dict]], model: str = "") -> str:
    lines = ["# 답변 평가 결과", "", "사람이 읽고 정답 요지와 비교해 확인한다.", ""]
    if model:
        lines += [f"모델: {model}", ""]
    for question, result in rows:
        number = question["번호"]
        lines += [
            f"## {number}. {question['질문']}",
            "",
            f"- 확인 목적: {REVIEW_SET[number]}",
            f"- 정답 요지: {question['정답 요지']}",
            f"- 찾을 수 없음 처리: {'예' if result['not_found'] else '아니오'}",
            "",
            "```",
            result["text"],
            "```",
            "",
        ]
    return "\n".join(lines)


def run(llm, searcher, questions: list[dict], mode: str = "hybrid") -> list[tuple[dict, dict]]:
    return [(q, answer_question(q["질문"], searcher, llm, mode=mode)) for q in questions]


def main() -> int:
    llm = create_llm()
    rows = run(llm, build_searcher(), load_review_questions())
    RESULTS.write_text(render(rows, llm.name), encoding="utf-8")
    print(f"저장: {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
