"""화면 검증. Streamlit AppTest로 화면을 실행하고, 검색기와 LLM은 가짜 객체를 넣는다."""

import time
from pathlib import Path

import pytest
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


def start(searcher=None, llm=None, last_asked_at=None):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["searcher"] = searcher or FakeSearcher()
    at.session_state["llm"] = llm or FakeLLM()
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
