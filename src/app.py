"""K리그 규정 챗봇 화면.

    streamlit run src/app.py

질문 하나에 답변 하나만 보여준다. 답변 아래에 인용한 조문의 원문을 펼쳐 볼 수 있다.
검색기와 LLM은 처음 한 번만 만든다. 테스트는 session_state의 searcher, llm에 가짜 객체를 넣는다.
"""

import re
import time

import streamlit as st

from answer import MissingConfigError, answer_question, build_searcher, chunk_label, create_llm
from enforcement import enforcement_notice

MAX_QUESTION_CHARS = 500
MIN_INTERVAL_SECONDS = 5

DISCLAIMER = "AI 답변은 틀릴 수 있습니다. 중요한 내용은 아래 근거 조문 원문을 꼭 확인하세요."
MSG_EMPTY = "질문을 입력해 주세요."
MSG_TOO_LONG = f"질문은 {MAX_QUESTION_CHARS}자 이내로 입력해 주세요."
MSG_TOO_FAST = f"질문은 {MIN_INTERVAL_SECONDS}초에 한 번씩 할 수 있습니다. 잠시 후 다시 질문해 주세요."
MSG_LOAD_ERROR = "검색 데이터를 불러오지 못했습니다. 관리자에게 문의해 주세요."
MSG_CONFIG_ERROR = "LLM 설정이 없어 답변할 수 없습니다. 관리자에게 문의해 주세요."
MSG_LLM_ERROR = "답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
REFERENCE_TITLE = "참고: 관련될 수 있는 조항 (질문의 답이 아닐 수 있습니다)"
# 질문을 처리하는 동안 보여줄 진행 단계
STAGES = {
    "load": "검색 데이터를 불러오는 중...",
    "search": "1/2 규정을 검색하는 중...",
    "llm": "2/2 검색한 조문으로 답변을 작성하는 중...",
}
WARMUP_QUERY = "선수 계약 기간"  # 임베딩 모델의 첫 계산(약 0.7초)을 미리 치른다

# 평가 질문지에서 고른 예시 (조번호 Q01, 표 Q14, 규정에 없는 질문 Q19)
EXAMPLES = [
    ("조번호 질문", "선수 규정 제2조에서 정한 선수 계약 기간은 얼마인가요?"),
    ("표 숫자 질문", "K리그1 팀의 시즌 벌점이 48점이면 제재금은 얼마인가요?"),
    ("규정에 없는 질문", "경기장 주차 요금의 상한은 얼마인가요?"),
]

# 마크다운으로 해석될 수 있는 문자. ~는 취소선, $는 수식으로 바뀌므로 반드시 막는다.
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~$])")
TABLE_CELL_SPECIAL = re.compile(r"([\\`*_{}\[\]<>~$])")


def check_question(question: str, last_asked: float | None, now: float) -> str:
    """질문을 받을 수 없으면 안내 문구를, 받을 수 있으면 빈 문자열을 돌려준다."""
    if not question.strip():
        return MSG_EMPTY
    if len(question) > MAX_QUESTION_CHARS:
        return MSG_TOO_LONG
    if last_asked is not None and now - last_asked < MIN_INTERVAL_SECONDS:
        return MSG_TOO_FAST
    return ""


def escape_answer(text: str) -> str:
    """LLM 답변의 ~와 $가 취소선이나 수식으로 바뀌지 않게 한다. 나머지 마크다운은 살린다."""
    return text.replace("~", r"\~").replace("$", r"\$")


def source_markdown(body: str) -> str:
    """조문 원문을 글자 그대로 보이게 한다. 표만 마크다운 표로 그린다."""
    blocks: list[str] = []
    lines: list[str] = []
    table: list[str] = []

    def flush() -> None:
        if lines:
            blocks.append("  \n".join(lines))
            lines.clear()
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for line in body.splitlines():
        if line.startswith("|"):
            if lines:
                flush()
            table.append(TABLE_CELL_SPECIAL.sub(r"\\\1", line))
        else:
            if table:
                flush()
            lines.append(MARKDOWN_SPECIAL.sub(r"\\\1", line))
    flush()
    return "\n\n".join(blocks)


def reference_label(chunk: dict) -> str:
    """참고 조항 제목. 검색 결과의 메타데이터로만 만든다."""
    pages = ", ".join(str(page) for page in chunk["pages"])
    label = f"{chunk_label(chunk)} · {pages}쪽 · 기준일 {chunk['effective_date']}"
    notice = enforcement_notice(chunk["enforcement_date"])
    return f"{label} · {notice}" if notice else label


def citation_label(item: dict) -> str:
    place = " ".join(x for x in (item["article_no"], item["article_title"], item["paragraph"]) if x)
    pages = ", ".join(str(page) for page in item["pages"])
    label = f"[{item['number']}] {item['regulation']} {place} · {pages}쪽 · 기준일 {item['effective_date']}"
    if item["notice"]:
        label += f" · {item['notice']}"
    return label


@st.cache_resource(show_spinner="검색 데이터를 불러오는 중...")
def load_searcher():
    searcher = build_searcher()
    searcher.search(WARMUP_QUERY, k=1)
    return searcher


def preload() -> None:
    """화면이 처음 열릴 때 검색기를 미리 만든다. 서버 프로세스에 한 번만 만들어지고 이후 접속은 재사용한다.

    Streamlit은 브라우저가 접속해야 스크립트를 실행하므로, 서버 시작 직후가 아니라 첫 화면이 열릴 때 만든다.
    실패해도 화면은 그대로 두고, 질문할 때 get_backend가 오류 안내를 띄운다.
    """
    if "searcher" in st.session_state:  # 테스트가 가짜 검색기를 넣은 경우
        return
    try:
        load_searcher()
    except Exception:
        pass


@st.cache_resource
def load_llm():
    return create_llm()


def get_backend():
    state = st.session_state
    searcher = state["searcher"] if "searcher" in state else load_searcher()
    llm = state["llm"] if "llm" in state else load_llm()
    return searcher, llm


def ask(question: str, on_stage=None) -> dict:
    """화면에 보여줄 상태를 돌려준다. kind는 notice, error, result 중 하나다."""
    try:
        if on_stage:
            on_stage("load")
        searcher, llm = get_backend()
    except MissingConfigError:
        return {"kind": "error", "message": MSG_CONFIG_ERROR}
    except Exception:
        return {"kind": "error", "message": MSG_LOAD_ERROR}
    try:
        result = answer_question(question, searcher, llm, on_stage=on_stage)
    except Exception:
        return {"kind": "error", "message": MSG_LLM_ERROR}
    return {"kind": "result", "result": result}


def submit(question: str) -> None:
    state = st.session_state
    now = time.monotonic()
    notice = check_question(question, state.get("last_asked_at"), now)
    if notice:
        state["view"] = {"kind": "notice", "message": notice}
        return
    state["last_asked_at"] = now
    slot = st.empty()  # 진행 표시는 끝나면 지운다
    with slot.status(STAGES["search"]) as status:
        state["view"] = ask(question, on_stage=lambda stage: status.update(label=STAGES[stage]))
    slot.empty()


def use_example(question: str) -> None:
    st.session_state["question_input"] = question
    st.session_state["pending"] = question


def render_references(chunks: list[dict]) -> None:
    """LLM 답변 없이 검색 상위 조항만 보여준다. 답이 아닐 수 있으므로 모두 접어 둔다."""
    if not chunks:
        return
    st.subheader(REFERENCE_TITLE)
    for chunk in chunks:
        with st.expander(reference_label(chunk), expanded=False):
            st.markdown(source_markdown(chunk["body"]))


def render_result(result: dict) -> None:
    if result["not_found"]:
        st.info(result["answer"])
        render_references(result.get("references", []))
        return
    st.subheader("답변")
    st.markdown(escape_answer(result["answer"]))
    notices = dict.fromkeys(
        f"{item['regulation']}: {item['notice']}" for item in result["citations"] if item["notice"]
    )
    for notice in notices:
        st.warning(notice)
    st.subheader("근거 조문")
    for i, item in enumerate(result["citations"]):
        chunk = result["retrieved"][item["number"] - 1]
        with st.expander(citation_label(item), expanded=i == 0):
            st.markdown(source_markdown(chunk["body"]))


def main() -> None:
    st.set_page_config(page_title="K리그 규정 챗봇", page_icon="⚽")
    st.title("K리그 규정 챗봇")
    st.info(DISCLAIMER)

    st.caption("예시 질문")
    for column, (label, question) in zip(st.columns(len(EXAMPLES)), EXAMPLES):
        column.button(label, help=question, on_click=use_example, args=(question,),
                      width="stretch")

    with st.form("ask"):
        # max_chars를 쓰면 Streamlit이 넘는 글자를 조용히 잘라 답해 버린다. 길이는 check_question에서 막는다.
        question = st.text_input("질문", key="question_input",
                                 placeholder="예: 선수 계약 기간은 얼마인가요?",
                                 help=f"{MAX_QUESTION_CHARS}자 이내")
        submitted = st.form_submit_button("질문하기")

    pending = st.session_state.pop("pending", None)
    if submitted:
        submit(question)
    elif pending:
        submit(pending)

    render_view(st.session_state.get("view"))
    # 화면을 먼저 그린 뒤 검색기를 불러온다. 사용자가 질문을 쓰는 동안 준비된다.
    preload()


def render_view(view: dict | None) -> None:
    if not view:
        return
    if view["kind"] == "notice":
        st.warning(view["message"])
    elif view["kind"] == "error":
        st.error(view["message"])
    else:
        render_result(view["result"])


if __name__ == "__main__":
    main()
