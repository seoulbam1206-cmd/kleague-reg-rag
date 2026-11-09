"""질문 1개 기준으로 답변이 느린 원인을 잰다. .env가 필요하므로 사용자가 실행한다.

    python scripts/measure_latency.py              # 평가 질문 Q01
    python scripts/measure_latency.py "질문"

재는 것
- 검색기 준비: 청크·ONNX 모델·벡터 캐시 로드. 화면에서는 서버를 띄운 뒤 첫 질문에만 걸린다
- 검색: 첫 검색과 두 번째 검색
- LLM: HTTP 요청마다 시작·끝 시각과 상태 코드를 기록해 응답 시간과 429 재시도 대기 시간을 나눈다
  재시도는 OpenAI SDK가 내부에서 하므로(max_retries) httpx 이벤트 훅으로 본다
규정 내용 일부가 .env의 LLM_PROVIDER 서버로 전송되므로 공개 규정 청크만 쓴다 (answer.ensure_sendable).
"""

import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from answer import (  # noqa: E402
    SYSTEM_PROMPT,
    TOP_K,
    build_searcher,
    build_user_prompt,
    create_llm,
    ensure_sendable,
)

DEFAULT_QUESTION = "선수 규정 제2조에서 정한 선수 계약 기간은 얼마인가요?"  # 평가 질문 Q01


class HttpLog:
    """httpx 이벤트 훅. 요청을 보낸 시각과 응답 머리를 받은 시각, 상태 코드를 쌓는다."""

    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.events: list[tuple] = []

    def on_request(self, request) -> None:
        self.events.append(("request", self.clock(), None, None))

    def on_response(self, response) -> None:
        retry_after = response.headers.get("retry-after")
        self.events.append(("response", self.clock(), response.status_code, retry_after))


def summarize(events: list[tuple]) -> dict:
    """요청·응답 기록에서 시도 횟수, 429 횟수, 재시도 대기 시간, 요청별 소요 시간을 구한다."""
    attempts, start = [], None
    for kind, at, status, retry_after in events:
        if kind == "request":
            start = at
        elif start is not None:
            attempts.append({"start": start, "end": at, "status": status, "retry_after": retry_after})
            start = None
    waits = [nxt["start"] - cur["end"] for cur, nxt in zip(attempts, attempts[1:])]
    return {
        "attempts": len(attempts),
        "rate_limited": sum(a["status"] == 429 for a in attempts),
        "statuses": [a["status"] for a in attempts],
        "retry_after": [a["retry_after"] for a in attempts if a["status"] == 429],
        "retry_wait": sum(waits),
        "request_times": [a["end"] - a["start"] for a in attempts],
    }


def usage_text(response) -> str:
    usage = getattr(response, "usage", None)
    if usage is None:
        return "사용량 정보 없음"
    details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    text = f"입력 {usage.input_tokens} 토큰, 출력 {usage.output_tokens} 토큰"
    return text + (f" (그중 추론 {reasoning} 토큰)" if reasoning is not None else "")


def main(argv: list[str] | None = None) -> int:
    from openai import DefaultHttpxClient

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    question = args[0] if args else DEFAULT_QUESTION
    clock = time.perf_counter

    t = clock()
    searcher = build_searcher()
    load_time = clock() - t

    t = clock()
    chunks = searcher.search(question, k=TOP_K)
    search_first = clock() - t
    t = clock()
    searcher.search(question, k=TOP_K)
    search_second = clock() - t

    llm = create_llm()
    ensure_sendable(chunks, llm)
    log = HttpLog(clock)
    client = llm.client.with_options(
        http_client=DefaultHttpxClient(event_hooks={"request": [log.on_request], "response": [log.on_response]})
    )
    prompt = build_user_prompt(question, chunks, date.today())
    t = clock()
    response = client.responses.create(model=llm.model, instructions=SYSTEM_PROMPT, input=prompt)
    llm_total = clock() - t
    summary = summarize(log.events)

    print(f"질문: {question}")
    print(f"모델: {llm.name}")
    print(f"검색기 준비 (서버 시작 후 첫 질문만): {load_time:.2f}초")
    print(f"검색: 첫 번째 {search_first:.2f}초, 두 번째 {search_second:.2f}초")
    print(f"LLM 전체: {llm_total:.2f}초")
    print(f"  HTTP 시도 {summary['attempts']}회, 상태 코드 {summary['statuses']}")
    print(f"  429 횟수 {summary['rate_limited']}회, retry-after {summary['retry_after']}")
    print(f"  재시도 대기 합계: {summary['retry_wait']:.2f}초")
    print("  요청별 응답 시간: " + ", ".join(f"{s:.2f}초" for s in summary["request_times"]))
    print(f"  프롬프트: 지시문 {len(SYSTEM_PROMPT)}자 + 근거·질문 {len(prompt)}자")
    print(f"  {usage_text(response)}")
    print(f"  답변 {len(response.output_text)}자")
    return 0


if __name__ == "__main__":
    sys.exit(main())
