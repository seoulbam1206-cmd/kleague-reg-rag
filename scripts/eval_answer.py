"""평가 질문 11개를 실제 LLM으로 돌려 eval/answer_results.md에 저장한다. 사람이 읽고 확인한다.

    python scripts/eval_answer.py                          # 11문항 1회
    python scripts/eval_answer.py --repeat 3 --only Q07,Q30  # 지정 문항만 3회

--only나 --repeat를 쓰면 1회 결과를 덮어쓰지 않도록 eval/answer_results_repeat.md에 저장한다.

문항은 결과를 보기 전에 정했다. 유형별 대표 문항과, 앞 단계에서 위험하다고 표시한 문항이다.
Q32는 화면에서 사용자가 질문한 사례로, 직접 규정이 없을 때의 참고 조항 표시를 확인하려고 추가했다.
규정 내용 일부가 .env의 LLM_PROVIDER 서버로 전송되므로 공개 규정 청크만 쓴다.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from answer import answer_question, build_searcher, chunk_label, create_llm  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.csv"
RESULTS = ROOT / "eval" / "answer_results.md"
REPEAT_RESULTS = ROOT / "eval" / "answer_results_repeat.md"
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
    "Q32": "상황 적용: 직접 규정이 없을 때 찾을 수 없음으로 답하고, 화면 참고 목록에 관련 조항이 나오는지",
}


def load_review_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row["번호"] in REVIEW_SET]


def only_numbers(value: str) -> set[str]:
    numbers = {n.strip().upper() for n in value.split(",") if n.strip()}
    unknown = sorted(numbers - set(REVIEW_SET))
    if unknown or not numbers:
        raise argparse.ArgumentTypeError(
            f"평가 대상 문항이 아니다: {', '.join(unknown) or value} "
            f"(대상: {', '.join(sorted(REVIEW_SET))})"
        )
    return numbers


def positive_int(value: str) -> int:
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"1 이상의 정수여야 한다: {value}")
    return int(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="답변 평가 문항을 실제 LLM으로 실행한다")
    parser.add_argument("--only", type=only_numbers, help="실행할 문항. 예: Q07,Q30")
    parser.add_argument("--repeat", type=positive_int, default=1, help="문항당 실행 횟수")
    return parser.parse_args(argv)


def select_questions(questions: list[dict], only: set[str] | None, repeat: int) -> list[dict]:
    """같은 문항의 반복 결과를 나란히 보도록 문항별로 묶어 돌려준다."""
    return [q for q in questions if only is None or q["번호"] in only for _ in range(repeat)]


def render(rows: list[tuple[dict, dict]], model: str = "") -> str:
    lines = ["# 답변 평가 결과", "", "사람이 읽고 정답 요지와 비교해 확인한다.", ""]
    if model:
        lines += [f"모델: {model}", ""]
    totals = Counter(question["번호"] for question, _ in rows)
    seen = Counter()
    for question, result in rows:
        number = question["번호"]
        seen[number] += 1
        attempt = f" ({seen[number]}/{totals[number]}회)" if totals[number] > 1 else ""
        lines += [
            f"## {number}{attempt}. {question['질문']}",
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
        if result["not_found"]:
            references = result.get("references", [])
            lines += [
                f"- 찾을 수 없음 사유: {result.get('reason', '')}",
                "- 화면 참고 목록: " + (" / ".join(chunk_label(c) for c in references) or "없음"),
                "- LLM 원문:",
                "",
                "```",
                result.get("raw", ""),
                "```",
                "",
            ]
    return "\n".join(lines)


def run(llm, searcher, questions: list[dict], mode: str = "hybrid") -> list[tuple[dict, dict]]:
    return [(q, answer_question(q["질문"], searcher, llm, mode=mode)) for q in questions]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    questions = select_questions(load_review_questions(), args.only, args.repeat)
    llm = create_llm()
    rows = run(llm, build_searcher(), questions)
    path = RESULTS if args.only is None and args.repeat == 1 else REPEAT_RESULTS
    path.write_text(render(rows, llm.name), encoding="utf-8")
    print(f"저장: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
