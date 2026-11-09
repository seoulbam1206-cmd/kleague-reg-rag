"""화면 검증. Streamlit AppTest로 화면을 실행하고, 검색기와 LLM은 가짜 객체를 넣는다."""

import time
from pathlib import Path

import bootstrap
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from answer import NO_CITATION, NOT_FOUND
from app import (
    DISCLAIMER,
    EXAMPLES,
    MAX_QUESTION_CHARS,
    MIN_INTERVAL_SECONDS,
    MSG_CONFIG_ERROR,
    MSG_EMPTY,
    MSG_LLM_ERROR,
    MSG_LOAD_ERROR,
    MSG_TOO_FAST,
    MSG_TOO_LONG,
    check_question,
    escape_answer,
    source_markdown,
)

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


def chunk(regulation="선수 규정", article_no="제2조", body="① 계약 기간은 최소 5개월에서 최대 5년으로 한다. 다만, 군팀은 예외로 한다.",
          enforcement="2026-01-17"):
    return {
        "regulation": regulation, "article_no": article_no, "article_title": "계약기간",
        "paragraph": "①", "pages": [3], "effective_date": "2026-01-16",
        "enforcement_date": enforcement, "visibility": "public", "body": body,
        "text": f"[{regulation}] {article_no}\n{body}",
    }


class FakeSearcher:
    def __init__(self, chunks=None, error=None):
        self.chunks = [chunk()] if chunks is None else chunks
        self.error = error
        self.queries = []

    def search(self, query, k=5, mode="hybrid"):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.chunks[:k]


class FakeLLM:
    external = True

    def __init__(self, reply="최소 5개월에서 최대 5년입니다 [1].", error=None):
        self.reply = reply
        self.error = error
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


def roomy_limiter():
    """서버 전체 제한은 프로세스에 하나라 테스트끼리 누적된다. 제한을 따로 재는 테스트 밖에서는 넉넉한 것을 쓴다."""
    from limits import UsageLimiter
    return UsageLimiter(per_minute=10**6, per_day=10**6)


def start(searcher=None, llm=None, last_asked_at=None):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["searcher"] = searcher or FakeSearcher()
    at.session_state["llm"] = llm or FakeLLM()
    at.session_state["limiter"] = roomy_limiter()
    if last_asked_at is not None:
        at.session_state["last_asked_at"] = last_asked_at
    return at.run()


def ask(at, question):
    at.text_input(key="question_input").input(question)
    at.button[len(EXAMPLES)].click()  # 예시 버튼 다음이 질문하기 버튼이다
    return at.run()


def texts(elements):
    return [element.value for element in elements]


# 화면 구성

def test_disclaimer_is_shown_before_question():
    at = start()
    assert not at.exception
    assert DISCLAIMER in texts(at.info)
    # 입력창이 글자를 잘라 버리지 않아야 길이 제한 안내를 띄울 수 있다
    assert not at.text_input(key="question_input").max_chars


def test_answer_and_cited_source_are_shown():
    at = ask(start(), "계약 기간은?")
    assert not at.exception
    assert any("최소 5개월에서 최대 5년입니다 [1]." in m for m in texts(at.markdown))
    assert len(at.expander) == 1
    label = at.expander[0].label
    assert "[1] 선수 규정 제2조 계약기간 ①" in label
    assert "3쪽" in label and "기준일 2026-01-16" in label


def test_source_text_is_inside_expander():
    at = ask(start(), "계약 기간은?")
    inside = texts(at.expander[0].markdown)
    assert any("다만, 군팀은 예외로 한다" in m for m in inside)


def test_only_first_source_is_expanded():
    searcher = FakeSearcher([chunk(article_no="제2조"), chunk(article_no="제3조")])
    at = ask(start(searcher, FakeLLM("답입니다 [1]. 추가 [2].")), "질문")
    assert len(at.expander) == 2
    assert at.expander[0].proto.expanded is True
    assert at.expander[1].proto.expanded is False
    assert "제3조" in at.expander[1].label


def test_not_yet_enforced_regulation_is_marked():
    searcher = FakeSearcher([chunk(regulation="경기장 규정", enforcement="2099-01-01")])
    at = ask(start(searcher), "질문")
    assert "경기장 규정: 시행 전 (2099-01-01 시행 예정)" in texts(at.warning)
    assert "시행 전 (2099-01-01 시행 예정)" in at.expander[0].label


def test_new_question_replaces_previous_answer():
    llm = FakeLLM()
    at = ask(start(llm=llm), "첫 질문")
    at.session_state["last_asked_at"] = time.monotonic() - MIN_INTERVAL_SECONDS - 1
    llm.reply = "두 번째 답입니다 [1]."
    at = ask(at, "둘째 질문")
    markdown = texts(at.markdown)
    assert any("두 번째 답입니다" in m for m in markdown)
    assert not any("최소 5개월에서 최대 5년입니다" in m for m in markdown)
    assert len(at.expander) == 1


# 예시 질문

def test_example_questions_come_from_eval_set():
    import csv
    path = Path(__file__).resolve().parent.parent / "eval" / "questions.csv"
    with path.open(encoding="utf-8-sig") as f:
        rows = {row["질문"]: row["유형"] for row in csv.DictReader(f)}
    assert [rows.get(question) for _, question in EXAMPLES] == ["조번호", "표", "없음"]


def test_example_button_asks_immediately():
    searcher = FakeSearcher()
    at = start(searcher)
    at.button[0].click()
    at.run()
    assert not at.exception
    assert searcher.queries == [EXAMPLES[0][1]]
    assert at.text_input(key="question_input").value == EXAMPLES[0][1]
    assert len(at.expander) == 1


# 근거 없음과 오류

def test_no_search_result_shows_not_found():
    llm = FakeLLM()
    at = ask(start(FakeSearcher([]), llm), "주차 요금은?")
    assert not at.exception
    assert NOT_FOUND in texts(at.info)
    assert len(at.expander) == 0
    assert llm.calls == 0


def test_llm_not_found_answer_shows_not_found():
    from app import REFERENCE_TITLE
    at = ask(start(llm=FakeLLM(NOT_FOUND)), "주차 요금은?")
    assert NOT_FOUND in texts(at.info)
    # 검색 결과 1건이 참고 조항으로 나온다. 답변의 근거 조문으로 보이면 안 된다 (사용자 승인 후 0 → 1)
    assert len(at.expander) == 1
    headings = [h.value for h in at.subheader]
    assert REFERENCE_TITLE in headings and "근거 조문" not in headings


def test_answer_without_citation_is_not_shown():
    at = ask(start(llm=FakeLLM("근거 없는 답입니다.")), "질문")
    assert NO_CITATION in texts(at.info)
    assert not any("근거 없는 답입니다" in m for m in texts(at.markdown))


@pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("429 Too Many Requests")])
def test_llm_error_keeps_screen_working(error):
    at = ask(start(llm=FakeLLM(error=error)), "질문")
    assert not at.exception
    assert texts(at.error) == [MSG_LLM_ERROR]
    assert DISCLAIMER in texts(at.info)
    assert at.text_input(key="question_input").value == "질문"


def test_search_error_keeps_screen_working():
    at = ask(start(FakeSearcher(error=RuntimeError("index broken"))), "질문")
    assert not at.exception
    assert texts(at.error) == [MSG_LLM_ERROR]


def test_backend_load_errors_are_shown_as_messages(monkeypatch):
    import app
    from answer import MissingConfigError

    def missing_config():
        raise MissingConfigError("GROQ_API_KEY")

    def missing_chunks():
        raise FileNotFoundError("data/chunks/article.jsonl")

    monkeypatch.setattr(app, "get_backend", missing_config)
    assert app.ask("질문") == {"kind": "error", "message": MSG_CONFIG_ERROR}
    monkeypatch.setattr(app, "get_backend", missing_chunks)
    assert app.ask("질문") == {"kind": "error", "message": MSG_LOAD_ERROR}


# 공개 데모 보호

def test_check_question_limits():
    assert check_question("", None, 100.0) == MSG_EMPTY
    assert check_question("   ", None, 100.0) == MSG_EMPTY
    assert check_question("가" * MAX_QUESTION_CHARS, None, 100.0) == ""
    assert check_question("가" * (MAX_QUESTION_CHARS + 1), None, 100.0) == MSG_TOO_LONG
    assert check_question("질문", 100.0, 100.0 + MIN_INTERVAL_SECONDS - 0.1) == MSG_TOO_FAST
    assert check_question("질문", 100.0, 100.0 + MIN_INTERVAL_SECONDS) == ""


def test_too_long_question_shows_notice_only():
    searcher, llm = FakeSearcher(), FakeLLM()
    at = start(searcher, llm)
    at.text_input(key="question_input").set_value("가" * (MAX_QUESTION_CHARS + 1))
    at.button[len(EXAMPLES)].click()
    at.run()
    assert not at.exception
    assert texts(at.warning) == [MSG_TOO_LONG]
    assert len(at.expander) == 0
    assert (searcher.queries, llm.calls) == ([], 0)


def test_question_within_interval_shows_notice_only():
    searcher, llm = FakeSearcher(), FakeLLM()
    at = start(searcher, llm)
    # 화면 첫 실행이 느려도 5초가 지나지 않도록, 시각은 화면을 띄운 뒤 질문 직전에 정한다
    at.session_state["last_asked_at"] = time.monotonic()
    at = ask(at, "질문")
    assert not at.exception
    assert texts(at.warning) == [MSG_TOO_FAST]
    assert len(at.expander) == 0
    assert not any("최소 5개월" in m for m in texts(at.markdown))
    assert (searcher.queries, llm.calls) == ([], 0)


def test_second_question_right_away_is_blocked():
    llm = FakeLLM()
    at = ask(start(llm=llm), "첫 질문")
    assert llm.calls == 1
    at = ask(at, "둘째 질문")
    assert texts(at.warning) == [MSG_TOO_FAST]
    assert llm.calls == 1


def test_example_button_is_also_rate_limited():
    llm = FakeLLM()
    at = start(llm=llm)
    at.session_state["last_asked_at"] = time.monotonic()
    at.button[1].click()
    at.run()
    assert texts(at.warning) == [MSG_TOO_FAST]
    assert llm.calls == 0


def test_question_after_interval_is_answered():
    llm = FakeLLM()
    at = ask(start(llm=llm, last_asked_at=time.monotonic() - MIN_INTERVAL_SECONDS - 1), "질문")
    assert not texts(at.warning)
    assert llm.calls == 1


def test_blocked_question_does_not_reset_timer():
    at = start()
    first = time.monotonic() - 1
    at.session_state["last_asked_at"] = first
    at = ask(at, "질문")
    assert texts(at.warning) == [MSG_TOO_FAST]
    assert at.session_state["last_asked_at"] == first


def test_empty_question_shows_notice():
    llm = FakeLLM()
    at = ask(start(llm=llm), "")
    assert texts(at.warning) == [MSG_EMPTY]
    assert llm.calls == 0


# 원문 표시

def test_source_markdown_keeps_text_literal():
    body = "1. 금액은 5~10만원이다. *별표* 참고 $100\n② 둘째 항"
    rendered = source_markdown(body)
    assert r"5\~10" in rendered and r"\*별표\*" in rendered and r"\$100" in rendered
    assert r"1\. 금액" in rendered  # 번호 목록으로 바뀌지 않음
    assert "  \n② 둘째 항" in rendered  # 줄바꿈 유지


def test_source_markdown_renders_tables():
    body = "① 다음과 같다.\n| 벌점 | 제재금 |\n| --- | --- |\n| 48~ | 500만원 |\n표 아래 글"
    rendered = source_markdown(body)
    assert "\n\n| 벌점 | 제재금 |\n| --- | --- |\n| 48\\~ | 500만원 |\n\n" in rendered
    assert rendered.endswith("표 아래 글")


def test_escape_answer_blocks_strikethrough_and_math():
    assert escape_answer("5~10년, $5") == r"5\~10년, \$5"
    assert escape_answer("**굵게** [1]") == "**굵게** [1]"



# 참고 조항

def test_llm_not_found_shows_reference_list_collapsed():
    from app import REFERENCE_TITLE
    chunks = [chunk(article_no=f"제{i}조", body=f"원문 {i}") for i in range(1, 6)]
    at = ask(start(FakeSearcher(chunks), FakeLLM(NOT_FOUND)), "구급차가 빠지면?")
    assert not at.exception
    assert NOT_FOUND in texts(at.info)
    assert REFERENCE_TITLE in [h.value for h in at.subheader]
    assert [e.label.split(" · ")[0] for e in at.expander] == [
        "선수 규정 제1조 계약기간 ①", "선수 규정 제2조 계약기간 ①", "선수 규정 제3조 계약기간 ①"]
    assert all(e.proto.expanded is False for e in at.expander)
    assert any("원문 1" in m for m in texts(at.expander[0].markdown))


def test_reference_label_shows_not_yet_enforced():
    at = ask(start(FakeSearcher([chunk(enforcement="2099-01-01")]), FakeLLM(NOT_FOUND)), "질문")
    assert "시행 전 (2099-01-01 시행 예정)" in at.expander[0].label
    assert "3쪽" in at.expander[0].label and "기준일 2026-01-16" in at.expander[0].label


def test_reference_list_has_no_llm_text():
    reply = f"{NOT_FOUND} 따라서 대기해야 합니다."
    at = ask(start(llm=FakeLLM(reply)), "질문")
    shown = texts(at.markdown) + texts(at.info) + [e.label for e in at.expander]
    assert not any("대기해야" in t for t in shown)


def test_no_search_result_has_no_reference_list():
    from app import REFERENCE_TITLE
    at = ask(start(FakeSearcher([]), FakeLLM()), "질문")
    assert REFERENCE_TITLE not in [h.value for h in at.subheader]
    assert len(at.expander) == 0


def test_unverified_answer_has_no_reference_list():
    at = ask(start(llm=FakeLLM("근거 없는 답입니다.")), "질문")
    assert NO_CITATION in texts(at.info)
    assert len(at.expander) == 0


def test_conclusion_marker_answer_shows_references_only():
    from app import REFERENCE_TITLE
    reply = "구급차 2대를 대기시켜야 합니다 [1]. 따라서 기다려야 합니다."
    at = ask(start(llm=FakeLLM(reply)), "질문")
    assert not at.exception
    assert NOT_FOUND in texts(at.info)
    headings = [h.value for h in at.subheader]
    assert REFERENCE_TITLE in headings and "근거 조문" not in headings
    shown = texts(at.markdown) + texts(at.info) + [e.label for e in at.expander]
    assert not any("기다려야" in t for t in shown)


# 속도 개선: 검색기 미리 로드, 진행 단계 표시

@pytest.fixture
def real_loader(monkeypatch):
    """검색기를 주입하지 않고 app.load_searcher가 build_searcher를 부르게 한다. 캐시는 테스트마다 비운다.

    AppTest가 실행한 스크립트의 함수는 테스트에서 import한 app.load_searcher와 캐시 키가 달라
    load_searcher.clear()로는 지워지지 않는다. 그래서 캐시 전체를 비운다.
    """
    import answer

    made = []

    def fake_build():
        searcher = FakeSearcher()
        made.append(searcher)
        return searcher

    monkeypatch.setattr(answer, "build_searcher", fake_build)
    monkeypatch.setattr(bootstrap, "is_ready", lambda *a: True)
    monkeypatch.setattr(bootstrap, "read_status", lambda *a: bootstrap.Prepared(ok=True))
    st.cache_resource.clear()
    yield made
    st.cache_resource.clear()


def open_page(llm=None):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["llm"] = llm or FakeLLM()
    at.session_state["limiter"] = roomy_limiter()
    return at.run()


def test_page_open_preloads_and_warms_up_searcher(real_loader):
    at = open_page()
    assert not at.exception
    assert len(real_loader) == 1
    from app import WARMUP_QUERY
    assert real_loader[0].queries == [WARMUP_QUERY]  # 임베딩 첫 계산을 미리 치른다


def test_preloaded_searcher_is_reused_for_question(real_loader):
    at = open_page()
    at = ask(at, "계약 기간은?")
    assert len(real_loader) == 1
    assert real_loader[0].queries[-1] == "계약 기간은?"
    assert len(at.expander) == 1


def test_preload_failure_keeps_page_and_shows_error_on_question(monkeypatch):
    import answer

    def broken():
        raise FileNotFoundError("data/chunks/article.jsonl")

    monkeypatch.setattr(answer, "build_searcher", broken)
    monkeypatch.setattr(bootstrap, "is_ready", lambda *a: True)
    monkeypatch.setattr(bootstrap, "read_status", lambda *a: bootstrap.Prepared(ok=True))
    st.cache_resource.clear()
    try:
        at = open_page()
        assert not at.exception
        assert not texts(at.error)  # 질문 전에는 오류를 띄우지 않는다
        at = ask(at, "질문")
        assert texts(at.error) == [MSG_LOAD_ERROR]
    finally:
        st.cache_resource.clear()


def test_injected_searcher_skips_preload(real_loader):
    start()
    assert real_loader == []


def test_ask_reports_stages_in_order(monkeypatch):
    import app
    monkeypatch.setattr(app, "get_backend", lambda: (FakeSearcher(), FakeLLM()))
    stages = []
    view = app.ask("계약 기간은?", on_stage=stages.append)
    assert view["kind"] == "result"
    assert stages == ["load", "search", "llm"]
    assert set(stages) <= set(app.STAGES)


def test_progress_is_cleared_after_answer():
    from app import STAGES
    at = ask(start(), "계약 기간은?")
    labels = [e.label for e in at.expander]
    assert not any(label in STAGES.values() for label in labels)
    assert len(at.expander) == 1


# 6단계 배포: 안내 문구, 서버 전체 제한, 첫 실행 준비 실패, Secrets

def test_portfolio_notice_is_shown_above_disclaimer():
    from app import PORTFOLIO_NOTICE
    at = start()
    infos = texts(at.info)
    assert PORTFOLIO_NOTICE == "한국프로축구연맹 공식 서비스가 아닌 개인 포트폴리오 프로젝트입니다. 답변은 공식 해석이 아닙니다."
    assert infos.index(PORTFOLIO_NOTICE) < infos.index(DISCLAIMER)


@pytest.mark.parametrize("per_minute, per_day, message_key", [(0, 100, "MSG_MINUTE"), (100, 0, "MSG_DAY")])
def test_global_limit_shows_notice_only(per_minute, per_day, message_key):
    import limits
    searcher, llm = FakeSearcher(), FakeLLM()
    at = start(searcher, llm)
    at.session_state["limiter"] = limits.UsageLimiter(per_minute=per_minute, per_day=per_day)
    at = ask(at, "질문")
    assert texts(at.warning) == [getattr(limits, message_key)]
    assert (searcher.queries, llm.calls) == ([], 0)
    assert len(at.expander) == 0


def test_session_limits_are_checked_before_global_count():
    from limits import UsageLimiter
    limiter = UsageLimiter(per_minute=100, per_day=100)
    at = start()
    at.session_state["limiter"] = limiter
    at = ask(at, "가" * (MAX_QUESTION_CHARS + 1))
    assert texts(at.warning) == [MSG_TOO_LONG]
    assert limiter.day_count == 0  # 세션 제한에 걸린 질문은 서버 전체 횟수에 넣지 않는다


@pytest.fixture
def not_prepared(monkeypatch):
    """청크 파일이 없는 배포 첫 실행 상태. 준비 결과는 테스트가 정한다."""
    state = {"ready": False, "result": bootstrap.Prepared(ok=False, error="규정 문서를 받지 못했습니다")}
    calls = []

    def prepare_data(on_step=None, **kwargs):
        calls.append(1)
        for step in ("download", "extract", "chunk"):
            on_step(step)
        state["ready"] = state["result"].ok
        return state["result"]

    monkeypatch.setattr(bootstrap, "is_ready", lambda *a: state["ready"])
    monkeypatch.setattr(bootstrap, "prepare_data", prepare_data)
    monkeypatch.setattr(bootstrap, "read_status", lambda *a: state["result"])
    st.cache_resource.clear()
    yield state, calls
    st.cache_resource.clear()


def test_prepare_failure_shows_error_and_retry(not_prepared):
    from app import MSG_PREPARE_ERROR
    state, calls = not_prepared
    at = open_page()
    assert not at.exception
    assert texts(at.error) == [MSG_PREPARE_ERROR]
    assert "다시 시도" in [b.label for b in at.button]
    retry = next(b for b in at.button if b.label == "다시 시도")
    retry.click()
    at.run()
    assert len(calls) == 2  # 다시 시도하면 처음부터 다시 받는다


def test_failed_regulations_are_listed(not_prepared, monkeypatch):
    import answer
    state, _ = not_prepared
    state["result"] = bootstrap.Prepared(ok=True, failed=["K리그 클럽 라이선싱 규정"])
    monkeypatch.setattr(answer, "build_searcher", FakeSearcher)
    at = open_page()
    assert not at.exception
    assert "일부 규정을 불러오지 못했습니다(K리그 클럽 라이선싱 규정). 이 규정에 대한 질문에는 답할 수 없습니다." in texts(at.warning)
    assert not texts(at.error)


def test_recheck_notice_appears_in_citation_label():
    searcher = FakeSearcher([{**chunk(), "enforcement_recheck": True}])
    at = ask(start(searcher), "질문")
    assert "시행일 재확인 필요" in at.expander[0].label


def test_secrets_fill_missing_environment(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    at = AppTest.from_file(APP, default_timeout=30)
    at.secrets["GROQ_API_KEY"] = "from-secrets"
    at.secrets["GROQ_MODEL"] = "secret-model"
    at.session_state["searcher"] = FakeSearcher()
    at.session_state["limiter"] = roomy_limiter()
    at.run()
    import os
    assert os.environ["GROQ_MODEL"] == "secret-model"
    assert os.environ["GROQ_API_KEY"] == "from-env"  # 이미 있는 환경변수는 덮어쓰지 않는다
    monkeypatch.delenv("GROQ_MODEL")


# 표 구조와 원문 페이지 보기

PARKING_ROW = "주차장 > 긴급구조차량 > 경찰버스 (기준 A): ★★★★ 4대, ★★★ 4대, ★★ 신축 2대, ★★ 기존 1대, ★ 1대 (관련 조항 7.5.)"
FIRE_ROW = "주차장 > 긴급구조차량 > 소방차 (기준 A): ★★★★ 2대, ★★★ 2대, ★★ 신축 2대, ★★ 기존 1대, ★ 1대 (관련 조항 7.5.)"


def table_chunk():
    item = chunk(regulation="K리그 경기장 시설기준 가이드라인", article_no="별첨1",
                 body=f"■ 주차장\n{FIRE_ROW}\n{PARKING_ROW}")
    return {**item, "doc_no": 16, "pages": [57], "id": "16-별첨1-7"}


def test_manual_table_rows_are_drawn_as_merged_html_table():
    from app import source_blocks
    blocks = source_blocks(f"앞 줄\n{FIRE_ROW}\n{PARKING_ROW}\n뒷 줄")
    assert [kind for kind, _ in blocks] == ["text", "html", "text"]
    html = blocks[1][1]
    assert '<th colspan="2">★★</th>' in html  # 머리글 2층
    assert '<td rowspan="2">긴급구조차량</td>' in html  # 걸친 칸은 청크 안의 행만큼
    assert "버스" not in html.replace("경찰버스", "")  # 청크에 없는 행은 그리지 않는다


def test_table_chunk_shows_html_table_and_page_image_by_default():
    at = ask(start(searcher=FakeSearcher([table_chunk()]), llm=FakeLLM("경찰버스 1대입니다 [1].")), "경찰버스")
    assert not at.exception
    assert any("<table" in m.value for m in at.expander[0].markdown)
    assert at.expander[0].toggle[0].value is True


def test_plain_chunk_hides_page_image_until_toggled():
    at = ask(start(), "계약 기간")
    toggle = at.expander[0].toggle[0]
    assert toggle.label == "원문 페이지 보기" and toggle.value is False
    at = toggle.set_value(True).run()
    assert not at.exception
    assert any("원문 페이지를 불러오지 못했습니다" in c.value for c in at.expander[0].caption)  # 가짜 청크라 PDF가 없다


def test_page_image_renders_real_pdf_page():
    from app import page_image, pdf_files
    file = pdf_files().get(16)
    if not file or not (bootstrap.PDF_DIR / file).exists():
        pytest.skip("규정 PDF 없음")
    image = page_image(file, 57)
    assert image and image[:8] == b"\x89PNG\r\n\x1a\n"
    assert page_image("없는파일.pdf", 1) is None


RAW_TABLE = "7.6. 경기장은 최소 다음과 같은 주차 공간을 제공해야 한다.\n| 기준 | 항목 |  | ★★★★ |\n| --- | --- | --- | --- |\n| A | 선수단 | 버스 | 4대 |"


def raw_table_chunk():
    item = chunk(regulation="K리그 경기장 시설기준 가이드라인", article_no="제7조", body=RAW_TABLE)
    return {**item, "doc_no": 16, "pages": [16], "id": "16-제7조-1"}


def test_unrestored_table_shows_page_image_first_and_folds_text():
    at = ask(start(searcher=FakeSearcher([raw_table_chunk()]), llm=FakeLLM("버스 4대입니다 [1].")), "버스")
    assert not at.exception
    box = at.expander[0]
    assert [type(c).__name__ for c in box.children.values()][0] == "Image"  # 원문 16쪽 이미지가 맨 앞
    toggle = box.toggle[0]
    assert toggle.label == "추출 텍스트 보기" and toggle.value is False
    assert not any("선수단" in m.value for m in box.markdown)
    at = toggle.set_value(True).run()
    assert any("선수단" in m.value for m in at.expander[0].markdown)


def test_ai_text_is_unchanged_for_unrestored_table():
    """화면 표시만 바꾼다. LLM에 보내는 청크 텍스트에는 표 줄이 그대로 있다."""
    from answer import build_user_prompt
    from datetime import date
    prompt = build_user_prompt("버스", [raw_table_chunk()], date(2026, 11, 9))
    assert "| A | 선수단 | 버스 | 4대 |" in prompt
