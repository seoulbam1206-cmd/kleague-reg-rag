"""K리그 규정 질의응답(Q&A) 서비스 화면.

    streamlit run src/app.py

질문 하나에 답변 하나만 보여준다. 답변 아래에 인용한 조문의 원문을 펼쳐 볼 수 있다.
검색기와 LLM은 처음 한 번만 만든다. 테스트는 session_state의 searcher, llm, limiter에 가짜 객체를 넣는다.
배포 환경에서는 첫 화면이 열릴 때 규정 PDF를 받아 청크를 만든다 (bootstrap.py).
"""

import io
import json
import os
import re
import threading
import time

import streamlit as st

import bootstrap
from answer import MissingConfigError, answer_question, build_searcher, chunk_label, create_llm
from enforcement import chunk_notice
from limits import UsageLimiter
from tables import load_tables, row_index, table_html

MAX_QUESTION_CHARS = 500
MIN_INTERVAL_SECONDS = 5

PORTFOLIO_NOTICE = "한국프로축구연맹 공식 서비스가 아닌 개인 포트폴리오 프로젝트입니다. 답변은 공식 해석이 아닙니다."
DISCLAIMER = "AI 답변은 틀릴 수 있습니다. 중요한 내용은 아래 근거 조문 원문을 꼭 확인하세요."
MSG_EMPTY = "질문을 입력해 주세요."
MSG_TOO_LONG = f"질문은 {MAX_QUESTION_CHARS}자 이내로 입력해 주세요."
MSG_TOO_FAST = f"질문은 {MIN_INTERVAL_SECONDS}초에 한 번씩 할 수 있습니다. 잠시 후 다시 질문해 주세요."
MSG_LOAD_ERROR = "검색 데이터를 불러오지 못했습니다. 관리자에게 문의해 주세요."
MSG_CONFIG_ERROR = "LLM 설정이 없어 답변할 수 없습니다. 관리자에게 문의해 주세요."
MSG_LLM_ERROR = "답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
MSG_PREPARE_ERROR = "규정 문서를 받지 못했습니다. 공식 사이트 접속 문제일 수 있으니 잠시 후 다시 시도해 주세요."
REFERENCE_TITLE = "참고: 관련될 수 있는 조항 (질문의 답이 아닐 수 있습니다)"
# 질문을 처리하는 동안 보여줄 진행 단계
STAGES = {
    "load": "검색 데이터를 불러오는 중...",
    "search": "1/2 규정을 검색하는 중...",
    "llm": "2/2 검색한 조문으로 답변을 작성하는 중...",
}
# Streamlit Secrets의 이 값들을 환경변수로 옮긴다. 로컬에서는 .env를 쓴다 (answer.create_llm)
SECRET_KEYS = ("LLM_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL", "HF_TOKEN")
WARMUP_QUERY = "선수 계약 기간"  # 임베딩 모델의 첫 계산(약 0.7초)을 미리 치른다
PAGE_SCALE = 1.5  # 원문 페이지 이미지 배율 (72dpi 기준, A4 한 쪽이 약 890x1260 픽셀)
PAGE_CACHE = 24  # 서버 전체에서 기억해 둘 페이지 이미지 수
MSG_PAGE_ERROR = "원문 페이지를 불러오지 못했습니다."

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


@st.cache_resource
def manual_tables() -> tuple[dict, dict]:
    """사람이 옮겨 적은 표 구조와, 행 텍스트로 표 행을 찾는 사전."""
    tables = load_tables()
    return {table["id"]: table for table in tables.values()}, row_index(tables)


def source_blocks(body: str) -> list[tuple[str, str]]:
    """조문 원문을 ("text", 줄들) 또는 ("html", 표) 조각으로 나눈다.

    표 구조가 있는 표의 행은 청크 본문에 "열 제목: 값" 줄로 들어 있다. 이어진 행을 모아
    원본처럼 칸이 합쳐진 HTML 표로 바꾼다.
    """
    by_id, index = manual_tables()
    blocks: list[tuple[str, str]] = []
    lines: list[str] = []
    run: tuple[str, list[int]] | None = None

    def flush_run() -> None:
        nonlocal run
        if run:
            blocks.append(("html", table_html(by_id[run[0]], run[1])))
            run = None

    def flush_lines() -> None:
        if lines:
            blocks.append(("text", "\n".join(lines)))
            lines.clear()

    for line in body.splitlines():
        hit = index.get(line)
        if hit and run and run[0] == hit[0] and run[1][-1] + 1 == hit[1]:
            run[1].append(hit[1])
        elif hit:
            flush_lines()
            flush_run()
            run = (hit[0], [hit[1]])
        else:
            flush_run()
            lines.append(line)
    flush_run()
    flush_lines()
    return blocks


def has_table(body: str) -> bool:
    """표 구조로 복원한 표의 행이 있는가."""
    _, index = manual_tables()
    return any(line in index for line in body.splitlines())


def has_raw_table(body: str) -> bool:
    """아직 복원하지 않은 자동 추출 표(마크다운 줄)가 있는가. 칸 합치기가 풀려 보기 어려울 수 있다."""
    return any(line.startswith("|") for line in body.splitlines())


@st.cache_data(show_spinner=False)
def pdf_files() -> dict[int, str]:
    manifest = bootstrap.PDF_DIR / "manifest.json"
    if not manifest.exists():
        return {}
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    return {e["no"]: e["files"][0]["file"] for e in entries if e.get("files")}


@st.cache_resource
def render_lock() -> threading.Lock:
    """PDF 렌더링 라이브러리(pdfium)는 여러 스레드가 동시에 쓰면 안 된다."""
    return threading.Lock()


@st.cache_data(max_entries=PAGE_CACHE, show_spinner=False)
def page_image(file: str, page: int) -> bytes | None:
    """원본 PDF 한 쪽을 PNG로 그린다. 파일이 없거나 실패하면 None."""
    path = bootstrap.PDF_DIR / file
    if not path.exists():
        return None
    try:
        import pypdfium2

        with render_lock():
            pdf = pypdfium2.PdfDocument(str(path))
            try:
                image = pdf[page - 1].render(scale=PAGE_SCALE).to_pil()
            finally:
                pdf.close()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def render_page_images(chunk: dict) -> None:
    file = pdf_files().get(chunk.get("doc_no"))
    for page in chunk.get("pages", []):
        image = page_image(file, page) if file else None
        if image:
            st.image(image, caption=f"원문 {page}쪽")
        else:
            st.caption(f"{MSG_PAGE_ERROR} ({page}쪽)")


def render_blocks(body: str) -> None:
    for kind, content in source_blocks(body):
        if kind == "html":
            st.markdown(content, unsafe_allow_html=True)  # 표 구조 파일의 글자는 table_html이 이스케이프한다
        else:
            st.markdown(source_markdown(content))


def render_source(chunk: dict, key: str) -> None:
    """조문 원문을 보여준다. 화면 표시만 다르고 AI·검색에 쓰는 청크 텍스트는 그대로다.

    - 복원하지 않은 표가 있으면: 원문 페이지 이미지를 먼저 보이고, 추출 텍스트는 켜야 보인다
    - 그 밖에는: 추출 텍스트(복원한 표는 HTML 표)를 보이고, 원문 페이지는 복원한 표가 있을 때만 처음부터 보인다
    """
    if has_raw_table(chunk["body"]):
        render_page_images(chunk)
        if st.toggle("추출 텍스트 보기", value=False, key=f"{key}-text"):
            render_blocks(chunk["body"])
        return
    render_blocks(chunk["body"])
    if st.toggle("원문 페이지 보기", value=has_table(chunk["body"]), key=key):
        render_page_images(chunk)


def reference_label(chunk: dict) -> str:
    """참고 조항 제목. 검색 결과의 메타데이터로만 만든다."""
    pages = ", ".join(str(page) for page in chunk["pages"])
    label = f"{chunk_label(chunk)} · {pages}쪽 · 기준일 {chunk['effective_date']}"
    notice = chunk_notice(chunk)
    return f"{label} · {notice}" if notice else label


def citation_label(item: dict) -> str:
    place = " ".join(x for x in (item["article_no"], item["article_title"], item["paragraph"]) if x)
    pages = ", ".join(str(page) for page in item["pages"])
    label = f"[{item['number']}] {item['regulation']} {place} · {pages}쪽 · 기준일 {item['effective_date']}"
    if item["notice"]:
        label += f" · {item['notice']}"
    return label


@st.cache_resource(show_spinner="검색 데이터를 불러오는 중...", max_entries=1)
def load_searcher(version: str = ""):
    """version(데이터 지문)이 바뀌면 새로 만든다. 이전 검색기는 메모리에서 내보낸다 (max_entries=1)."""
    searcher = build_searcher()
    searcher.search(WARMUP_QUERY, k=1)
    return searcher


def failed_notice(failed: list[str]) -> str:
    return f"일부 규정을 불러오지 못했습니다({', '.join(failed)}). 이 규정에 대한 질문에는 답할 수 없습니다."


@st.cache_resource
def prepare_lock() -> threading.Lock:
    """여러 접속이 동시에 첫 실행 준비를 하지 않도록 서버 프로세스에 하나만 둔다."""
    return threading.Lock()


def prepare(slot) -> bootstrap.Prepared:
    """청크 파일이 없으면 규정 PDF를 받아 만든다. 진행 단계와 결과 안내는 slot(화면 위쪽)에 쓴다."""
    if not bootstrap.is_ready():
        progress = slot.empty()  # 진행 표시는 끝나면 지운다
        lock = prepare_lock()
        if lock.locked():
            progress.info("다른 접속에서 규정 문서를 준비하고 있습니다. 잠시만 기다려 주세요.")
        with lock:
            if not bootstrap.is_ready():
                with progress.status(bootstrap.STEPS["download"]) as status:
                    bootstrap.prepare_data(on_step=lambda step: status.update(label=bootstrap.STEPS[step]))
        progress.empty()
    return bootstrap.read_status()


def preload(slot) -> None:
    """화면이 처음 열릴 때 규정 데이터와 검색기를 미리 만든다. 서버 프로세스에 한 번만 만들어진다.

    Streamlit은 브라우저가 접속해야 스크립트를 실행하므로, 서버 시작 직후가 아니라 첫 화면이 열릴 때 만든다.
    검색기를 만들다 실패하면 화면은 그대로 두고, 질문할 때 get_backend가 오류 안내를 띄운다.
    """
    if "searcher" in st.session_state:  # 테스트가 가짜 검색기를 넣은 경우
        return
    status = prepare(slot)
    if not status.ok:
        slot.error(MSG_PREPARE_ERROR)
        slot.button("다시 시도")  # 누르면 다시 실행되고, 청크 파일이 없으므로 처음부터 다시 받는다
        return
    if status.failed:
        slot.warning(failed_notice(status.failed))
    try:
        load_searcher(bootstrap.data_version())
    except Exception:
        pass


def apply_secrets() -> None:
    """Streamlit Secrets의 값을 환경변수가 비어 있을 때만 옮긴다. Secrets가 없으면 아무것도 하지 않는다."""
    try:
        secrets = {key: st.secrets[key] for key in SECRET_KEYS if key in st.secrets}
    except Exception:  # secrets.toml이 없는 로컬 환경
        return
    for key, value in secrets.items():
        if not os.environ.get(key):
            os.environ[key] = str(value)


@st.cache_resource
def shared_limiter() -> UsageLimiter:
    return UsageLimiter()


def get_limiter() -> UsageLimiter:
    return st.session_state["limiter"] if "limiter" in st.session_state else shared_limiter()


@st.cache_resource
def load_llm():
    return create_llm()


def get_backend():
    state = st.session_state
    searcher = state["searcher"] if "searcher" in state else load_searcher(bootstrap.data_version())
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
    limit = get_limiter().acquire()  # 서버 전체 사용량. 세션 제한을 통과한 질문만 센다
    if limit:
        state["view"] = {"kind": "notice", "message": limit}
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
    for i, chunk in enumerate(chunks):
        with st.expander(reference_label(chunk), expanded=False):
            render_source(chunk, key=f"page-ref-{i}-{chunk.get('id', '')}")


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
            render_source(chunk, key=f"page-cite-{item['number']}-{chunk.get('id', '')}")


def main() -> None:
    st.set_page_config(page_title="K리그 규정 질의응답(Q&A) 서비스", page_icon="⚽")
    apply_secrets()
    st.title("K리그 규정 질의응답(Q&A) 서비스")
    st.info(PORTFOLIO_NOTICE)
    st.info(DISCLAIMER)
    status_slot = st.container()  # 첫 실행 준비 진행과 일부 규정 누락 안내 자리

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
    preload(status_slot)


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
