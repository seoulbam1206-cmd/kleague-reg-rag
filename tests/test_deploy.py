"""6단계 배포 검증: 공개 전 점검, 첫 실행 준비, 실패 처리, 서버 전체 사용량 제한."""

import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

import answer
import bootstrap
import search
from chunk import load_documents
from enforcement import RECHECK_NOTICE, chunk_notice, load_enforcement_rules, resolve_enforcement
from limits import KST, MSG_DAY, MSG_MINUTE, UsageLimiter
from search import (CHUNK_DIR, DEFAULT_MODEL, cached_vectors, load_chunks, reuse_vectors, save_stored,
                    stored_file, text_hash)

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


# 공개 전 점검: 히스토리 전체와 저장소 파일

def test_history_never_contained_pdf_env_or_private_files():
    paths = set(git("log", "--all", "--name-only", "--format=").split())
    bad = [p for p in paths if p.lower().endswith(".pdf") or re.search(r"(^|/)\.env($|\.)", p)
           or p.startswith("docs_private/") or p.endswith("secrets.toml") or p.startswith("data/")]
    assert bad == []


KEY_PATTERNS = re.compile(r"gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}")


def test_history_has_no_api_keys():
    assert not KEY_PATTERNS.search(git("log", "--all", "-p"))


def test_tracked_files_have_no_api_keys_or_local_paths():
    for path in git("ls-files").splitlines():
        file = ROOT / path
        if not file.is_file() or file.suffix in {".pdf", ".npy"}:
            continue
        text = file.read_text(encoding="utf-8", errors="ignore")
        assert not KEY_PATTERNS.search(text), path
        assert not re.search(r"[A-Za-z]:\\Users\\|/home/\w+/", text), path


def test_deploy_target_has_no_pdf_data_env_or_index():
    tracked = git("ls-files").splitlines()
    assert not [p for p in tracked if p.lower().endswith((".pdf", ".npy", ".jsonl"))]
    assert not [p for p in tracked if p.startswith(("data/", "docs_private/", ".env"))]
    assert ".streamlit/secrets.toml" not in tracked


def test_gitignore_covers_deploy_secrets_and_derived_data():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (".env", "docs_private/", "docs_public/*.pdf", "data/", ".streamlit/secrets.toml"):
        assert rule in ignore


def test_requirements_are_pinned():
    lines = [line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#")]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[\w.]+", line) for line in lines)
    assert "streamlit==1.67.0" in lines


def test_public_docs_use_generic_private_wording():
    """특정 기관의 내부 자료처럼 읽히지 않도록 "비공개 문서"로 쓴다."""
    word = "사" + "내"  # 이 파일 자체에 걸리지 않게 나눠 쓴다
    for path in git("ls-files").splitlines() + ["CLAUDE.md", "README.md"]:
        file = ROOT / path
        if file.suffix in {".md", ".py", ".json", ".csv", ".example"} and file.exists():
            assert word not in file.read_text(encoding="utf-8", errors="ignore"), path


# 첫 실행 준비 (가짜 다운로드·추출로 빠르게)

def write_manifest(pdf_dir: Path, titles: list[str], present: list[bool]) -> None:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for no, (title, exists) in enumerate(zip(titles, present), 1):
        name = f"{no:02d}.pdf"
        if exists:
            (pdf_dir / name).write_bytes(b"%PDF")
        entries.append({"no": no, "title": title, "body_text": "2026/01/16 개정", "files": [{"file": name}]})
    (pdf_dir / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def fake_pipeline(monkeypatch):
    broken = set()

    def extract(entry, pdf_dir, manual, strict):
        assert strict is False  # 배포에서는 개정이 있어도 멈추지 않는다
        if entry["title"] in broken:
            raise ValueError("형식이 바뀐 PDF")
        return {"title": entry["title"]}

    monkeypatch.setattr(bootstrap, "extract_document", extract)
    monkeypatch.setattr(bootstrap, "load_manual_pages", lambda: {})
    monkeypatch.setattr(bootstrap, "article_chunks", lambda doc: [{"regulation": doc["title"], "text": "x"}])
    return broken


def test_prepare_builds_chunks_and_reports_steps(tmp_path, fake_pipeline):
    pdf_dir, data_dir = tmp_path / "pdf", tmp_path / "data"
    calls, steps = [], []
    result = bootstrap.prepare_data(
        on_step=steps.append, download=lambda: (calls.append(1), write_manifest(pdf_dir, ["가", "나"], [True, True])),
        pdf_dir=pdf_dir, data_dir=data_dir)
    assert result.ok and result.failed == []
    assert steps == ["download", "extract", "chunk"]
    assert bootstrap.is_ready(data_dir)
    assert len(bootstrap.chunk_file(data_dir).read_text(encoding="utf-8").splitlines()) == 2
    # 이미 준비되어 있으면 다시 받지 않는다
    assert bootstrap.prepare_data(download=lambda: calls.append(1), pdf_dir=pdf_dir, data_dir=data_dir).ok
    assert calls == [1]


def test_download_failure_without_pdf_fails_and_can_retry(tmp_path, fake_pipeline):
    def down():
        raise ConnectionError("kleague.com 접속 실패")

    data_dir = tmp_path / "data"
    result = bootstrap.prepare_data(download=down, pdf_dir=tmp_path / "pdf", data_dir=data_dir)
    assert not result.ok
    assert result.error == "규정 문서를 받지 못했습니다"
    assert "ConnectionError" in result.download_error
    assert not bootstrap.is_ready(data_dir)  # 다음 실행에서 다시 시도한다
    assert bootstrap.read_status(data_dir).error == result.error


def test_download_failure_continues_with_pdfs_already_received(tmp_path, fake_pipeline):
    pdf_dir = tmp_path / "pdf"
    write_manifest(pdf_dir, ["가", "나", "다"], [True, False, True])

    def down():
        raise TimeoutError("중간에 끊김")

    result = bootstrap.prepare_data(download=down, pdf_dir=pdf_dir, data_dir=tmp_path / "data")
    assert result.ok
    assert result.failed == ["나"]
    assert "TimeoutError" in result.download_error


def test_failed_extraction_skips_only_that_regulation(tmp_path, fake_pipeline):
    pdf_dir = tmp_path / "pdf"
    write_manifest(pdf_dir, ["가", "나", "다"], [True, True, True])
    fake_pipeline.add("다")
    result = bootstrap.prepare_data(download=lambda: None, pdf_dir=pdf_dir, data_dir=tmp_path / "data")
    assert result.ok and result.failed == ["다"]
    regulations = [json.loads(line)["regulation"]
                   for line in bootstrap.chunk_file(tmp_path / "data").read_text(encoding="utf-8").splitlines()]
    assert regulations == ["가", "나"]
    assert bootstrap.read_status(tmp_path / "data").failed == ["다"]


def test_all_extractions_failing_is_a_failure(tmp_path, fake_pipeline):
    pdf_dir = tmp_path / "pdf"
    write_manifest(pdf_dir, ["가"], [True])
    fake_pipeline.add("가")
    result = bootstrap.prepare_data(download=lambda: None, pdf_dir=pdf_dir, data_dir=tmp_path / "data")
    assert not result.ok and result.failed == ["가"]
    assert not bootstrap.is_ready(tmp_path / "data")


@pytest.mark.slow
def test_empty_environment_first_run_builds_all_17(tmp_path):
    """PDF가 없는 빈 환경에서 첫 실행 시 17개가 받아지고 청크가 만들어진다. 다운로드는 로컬 PDF 복사로 대신한다."""
    source = ROOT / "docs_public"
    pdfs = sorted(source.glob("*.pdf"))
    if len(pdfs) < 17:
        pytest.skip("로컬 PDF 없음: scripts/download_regulations.py를 먼저 실행할 것")
    pdf_dir = tmp_path / "docs_public"

    def download():
        pdf_dir.mkdir()
        shutil.copy(source / "manifest.json", pdf_dir)
        for pdf in pdfs:
            shutil.copy(pdf, pdf_dir)

    result = bootstrap.prepare_data(download=download, pdf_dir=pdf_dir, data_dir=tmp_path / "data")
    assert result.ok and result.failed == []
    assert len(list(pdf_dir.glob("*.pdf"))) == 17
    chunks = bootstrap.chunk_file(tmp_path / "data").read_text(encoding="utf-8").splitlines()
    local = (ROOT / "data" / "chunks" / "article.jsonl")
    if local.exists():  # 로컬 파이프라인과 같은 청크가 만들어진다
        assert chunks == local.read_text(encoding="utf-8").splitlines()
    assert len({json.loads(line)["regulation"] for line in chunks}) == 17


# 시행일 재확인 (15·16번 개정 시)

def test_revised_explicit_rule_does_not_stop_in_deploy():
    rules = load_enforcement_rules()
    no = next(n for n, rule in rules.items() if rule["rule"] == "explicit")
    with pytest.raises(ValueError):  # 로컬 기본은 그대로 멈춘다
        resolve_enforcement(no, "2099-12-31", rules)
    result = resolve_enforcement(no, "2099-12-31", rules, strict=False)
    assert result["date"] == "2099-12-31"
    assert result["recheck"] is True
    assert result["basis"].startswith(RECHECK_NOTICE)


def test_unrevised_rule_has_no_recheck():
    rules = load_enforcement_rules()
    no, rule = next((n, r) for n, r in rules.items() if r["rule"] == "explicit")
    assert "recheck" not in resolve_enforcement(no, rule["as_of"], rules, strict=False)


def test_recheck_notice_overrides_not_yet_enforced():
    chunk = {"enforcement_date": "2099-01-01", "enforcement_recheck": True}
    assert chunk_notice(chunk, date(2026, 11, 9)) == RECHECK_NOTICE
    assert chunk_notice({"enforcement_date": "2099-01-01"}, date(2026, 11, 9)) == "시행 전 (2099-01-01 시행 예정)"


def test_current_chunks_have_no_recheck_flag():
    docs = load_documents()
    if not docs:
        pytest.skip("추출 결과 없음")
    assert not any(doc.get("enforcement_recheck") for doc in docs)


# 서버 전체 사용량 제한

class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def kst(*args) -> float:
    return datetime(*args, tzinfo=KST).timestamp()


def test_minute_limit_blocks_eleventh_question_then_recovers():
    clock = Clock(kst(2026, 11, 9, 12, 0, 0))
    limiter = UsageLimiter(clock=clock)
    assert [limiter.acquire() for _ in range(10)] == [""] * 10
    assert limiter.acquire() == MSG_MINUTE
    clock.now += 60
    assert limiter.acquire() == ""


def test_day_limit_blocks_after_200_and_resets_at_kst_midnight():
    clock = Clock(kst(2026, 11, 9, 0, 0, 0))
    limiter = UsageLimiter(clock=clock)
    for _ in range(200):
        assert limiter.acquire() == ""
        clock.now += 61  # 분당 제한에 걸리지 않게
    assert limiter.acquire() == MSG_DAY
    clock.now = kst(2026, 11, 10, 0, 0, 1)
    assert limiter.acquire() == ""


def test_blocked_questions_are_not_counted():
    clock = Clock(kst(2026, 11, 9, 12, 0, 0))
    limiter = UsageLimiter(per_minute=1, per_day=2, clock=clock)
    assert limiter.acquire() == ""
    for _ in range(5):
        assert limiter.acquire() == MSG_MINUTE
    clock.now += 60
    assert limiter.acquire() == ""
    assert limiter.day_count == 2


def test_limit_messages():
    assert "오늘 데모 사용량이 다 찼습니다" in MSG_DAY
    assert "분당 10회" in MSG_MINUTE and "하루 200회" in MSG_DAY


# 미리 계산한 벡터: 내용 해시가 같은 청크만 재사용하고 바뀐 청크만 계산

class RecordingEncoder:
    name = "org/fake"

    def __init__(self):
        self.encoded: list[list[str]] = []

    def encode_passages(self, texts):
        self.encoded.append(list(texts))
        return np.array([[float(len(t)), 1.0] for t in texts])


def chunks_of(*texts: str) -> list[dict]:
    return [{"text": text} for text in texts]


def test_stored_vectors_are_reused_without_encoding(tmp_path):
    encoder = RecordingEncoder()
    path = tmp_path / "article.npz"
    save_stored(encoder, chunks_of("가", "나다"), np.array([[9.0, 9.0], [8.0, 8.0]]), path)
    vectors, computed = reuse_vectors(encoder, chunks_of("가", "나다"), path)
    assert encoder.encoded == [] and computed == 0
    assert vectors.tolist() == [[9.0, 9.0], [8.0, 8.0]]


def test_only_changed_chunks_are_encoded_in_order(tmp_path):
    """규정이 개정되어 바뀐 청크만 다시 계산하고, 순서는 지금 청크 순서를 따른다."""
    encoder = RecordingEncoder()
    path = tmp_path / "article.npz"
    save_stored(encoder, chunks_of("가", "나다"), np.array([[9.0, 9.0], [8.0, 8.0]]), path)
    vectors, computed = reuse_vectors(encoder, chunks_of("나다", "새 조문", "가"), path)
    assert encoder.encoded == [["새 조문"]] and computed == 1
    assert vectors.tolist() == [[8.0, 8.0], [4.0, 1.0], [9.0, 9.0]]


def test_missing_file_or_other_model_encodes_everything(tmp_path):
    encoder = RecordingEncoder()
    _, computed = reuse_vectors(encoder, chunks_of("가", "나"), tmp_path / "none.npz")
    assert computed == 2
    other = RecordingEncoder()
    other.name = "org/other"
    path = tmp_path / "article.npz"
    save_stored(other, chunks_of("가"), np.array([[9.0, 9.0]]), path)
    _, computed = reuse_vectors(encoder, chunks_of("가"), path)
    assert computed == 1


def test_cached_vectors_uses_stored_file_on_cache_miss(tmp_path):
    encoder = RecordingEncoder()
    stored = tmp_path / "article.npz"
    save_stored(encoder, chunks_of("가"), np.array([[9.0, 9.0]]), stored)
    vectors = cached_vectors(encoder, chunks_of("가", "나"), "article", tmp_path / "cache", stored=stored)
    assert encoder.encoded == [["나"]]
    assert vectors.tolist() == [[9.0, 9.0], [1.0, 1.0]]


def test_build_searcher_uses_committed_vectors(monkeypatch):
    seen = {}

    def fake_cached(encoder, chunks, name, stored=None):
        seen["stored"] = stored
        return np.zeros((len(chunks), 2))

    monkeypatch.setattr(answer, "load_chunks", lambda name: [{"text": "가", "regulation": "선수 규정"}])
    monkeypatch.setattr(answer, "OnnxEncoder", lambda model: RecordingEncoder())
    monkeypatch.setattr(answer, "cached_vectors", fake_cached)
    answer.build_searcher()
    assert seen["stored"] == stored_file(DEFAULT_MODEL) == ROOT / "index" / "article_intfloat_multilingual-e5-small.npz"


def test_committed_vector_file_holds_no_regulation_text():
    """저장소에 올리는 벡터 파일에는 모델 이름, 해시, 숫자 벡터만 있다."""
    path = stored_file(DEFAULT_MODEL)
    assert path.exists()
    ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT).returncode == 0
    assert not ignored  # 배포 대상에 들어간다
    with np.load(path, allow_pickle=False) as stored:
        assert sorted(stored.files) == ["hashes", "model", "vectors"]
        assert str(stored["model"]) == DEFAULT_MODEL
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in stored["hashes"].tolist())
        assert stored["vectors"].dtype.kind == "f"
        assert stored["vectors"].shape == (len(stored["hashes"]), 384)
    assert path.stat().st_size < 5 * 2**20


def test_committed_vectors_cover_current_chunks():
    if not (CHUNK_DIR / "article.jsonl").exists():
        pytest.skip("청크 없음")
    with np.load(stored_file(DEFAULT_MODEL), allow_pickle=False) as stored:
        known = set(stored["hashes"].tolist())
    missing = [c["article_no"] for c in load_chunks("article") if text_hash(c["text"]) not in known]
    assert missing == [], "규정이 바뀌었으면 scripts/build_vectors.py를 다시 실행할 것"


def test_onnx_session_does_not_hold_memory_arena():
    assert search.CPU_MEM_ARENA is False
    assert search.EMBED_BATCH <= 8


# 새 버전을 올렸을 때 서버에 남은 이전 청크를 다시 만든다

def test_data_version_changes_with_manual_tables_and_code(tmp_path):
    for name in ("src/extract.py", "src/chunk.py", "src/tables.py", "src/enforcement.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("code", encoding="utf-8")
    table = tmp_path / "docs_public" / "manual" / "tables" / "16_p57.json"
    table.parent.mkdir(parents=True)
    table.write_text("{}", encoding="utf-8")
    before = bootstrap.data_version(tmp_path)
    table.write_text('{"rows": []}', encoding="utf-8")
    after_table = bootstrap.data_version(tmp_path)
    (tmp_path / "src" / "chunk.py").write_text("code v2", encoding="utf-8")
    assert len({before, after_table, bootstrap.data_version(tmp_path)}) == 3


def test_data_version_ignores_line_endings(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "extract.py").write_bytes(b"a\nb\n")
    lf = bootstrap.data_version(tmp_path)
    (tmp_path / "src" / "extract.py").write_bytes(b"a\r\nb\r\n")  # Windows 체크아웃과 Linux 서버가 같게
    assert bootstrap.data_version(tmp_path) == lf


def test_stale_chunks_from_older_version_are_rebuilt(tmp_path, fake_pipeline):
    pdf_dir, data_dir = tmp_path / "pdf", tmp_path / "data"
    download = lambda: write_manifest(pdf_dir, ["가"], [True])  # noqa: E731
    assert bootstrap.prepare_data(download=download, pdf_dir=pdf_dir, data_dir=data_dir).ok
    assert bootstrap.read_status(data_dir).version == bootstrap.data_version()
    status = data_dir / bootstrap.STATUS_FILE
    status.write_text(json.dumps({"ok": True, "failed": [], "error": "", "download_error": "", "version": "old"}),
                      encoding="utf-8")
    assert not bootstrap.is_ready(data_dir)
    steps = []
    assert bootstrap.prepare_data(on_step=steps.append, download=download, pdf_dir=pdf_dir, data_dir=data_dir).ok
    assert steps == ["download", "extract", "chunk"]
    assert bootstrap.is_ready(data_dir)


def test_status_without_version_counts_as_old_but_local_build_without_status_is_ready(tmp_path):
    data_dir = tmp_path / "data"
    bootstrap.chunk_file(data_dir).parent.mkdir(parents=True)
    bootstrap.chunk_file(data_dir).write_text("{}\n", encoding="utf-8")
    assert bootstrap.is_ready(data_dir)  # 로컬에서 src/chunk.py로 만든 경우
    (data_dir / bootstrap.STATUS_FILE).write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert not bootstrap.is_ready(data_dir)  # 지문 기능 이전에 서버가 만든 청크
    assert bootstrap.read_status(data_dir).ok


def test_searcher_cache_is_keyed_by_data_version():
    import inspect
    import app
    assert "version" in inspect.signature(app.load_searcher).parameters
    source = inspect.getsource(app)
    assert source.count("load_searcher(bootstrap.data_version())") == 2


def test_license_holder_uses_public_handle():
    """작성자 실명을 공개하지 않는다. 저작권자는 GitHub에서 쓰는 표기와 같게 둔다 (사용자 결정 2026-11-09)."""
    lines = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()
    assert "Copyright (c) 2026 seoulbam" in lines
    assert "seoulbam" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
