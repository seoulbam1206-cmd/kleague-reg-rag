"""응답 시간 측정 스크립트 검증. 실제 LLM은 부르지 않고 기록 계산만 확인한다."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_latency import HttpLog, summarize, usage_text  # noqa: E402


class FakeClock:
    def __init__(self, times):
        self.times = iter(times)

    def __call__(self):
        return next(self.times)


def response(status, retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return SimpleNamespace(status_code=status, headers=headers)


def test_retry_wait_is_gap_between_429_and_next_request():
    log = HttpLog(FakeClock([0.0, 0.5, 3.5, 6.0]))
    log.on_request(None)
    log.on_response(response(429, "3"))
    log.on_request(None)
    log.on_response(response(200))
    summary = summarize(log.events)
    assert summary["attempts"] == 2
    assert summary["rate_limited"] == 1
    assert summary["statuses"] == [429, 200]
    assert summary["retry_after"] == ["3"]
    assert summary["retry_wait"] == 3.0
    assert summary["request_times"] == [0.5, 2.5]


def test_single_success_has_no_wait():
    log = HttpLog(FakeClock([1.0, 4.0]))
    log.on_request(None)
    log.on_response(response(200))
    summary = summarize(log.events)
    assert (summary["attempts"], summary["rate_limited"], summary["retry_wait"]) == (1, 0, 0)
    assert summary["request_times"] == [3.0]


def test_no_events():
    assert summarize([])["attempts"] == 0


def test_usage_text_shows_reasoning_tokens():
    usage = SimpleNamespace(input_tokens=3000, output_tokens=800,
                            output_tokens_details=SimpleNamespace(reasoning_tokens=600))
    assert usage_text(SimpleNamespace(usage=usage)) == "입력 3000 토큰, 출력 800 토큰 (그중 추론 600 토큰)"
    assert usage_text(SimpleNamespace(usage=None)) == "사용량 정보 없음"
