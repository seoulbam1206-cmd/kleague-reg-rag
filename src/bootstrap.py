"""배포 환경의 첫 실행 준비: 규정 PDF 다운로드 → 텍스트 추출 → 조문 청킹.

규정 PDF와 추출·청크 산출물은 저장소에 없다. 배포 서버는 재시작하면 파일이 사라지므로
청크 파일이 없을 때마다 공식 사이트에서 다시 받아 만든다. 임베딩 벡터는 build_searcher가 만든다.

- 다운로드가 실패해도 이미 받은 PDF로 계속한다. PDF가 하나도 없으면 실패로 끝낸다.
- 규정 하나의 추출이 실패하면 그 규정만 빼고, 이름을 status.json에 남겨 화면에 알린다.
- 시행일을 직접 적어 둔 규정이 개정되면 멈추지 않고 "시행일 재확인 필요"로 표시한다.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chunk import article_chunks, write_jsonl
from extract import PDF_DIR, extract_document, load_manual_pages

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATUS_FILE = "status.json"

STEPS = {
    "download": "규정 문서를 공식 사이트에서 받는 중... (1~2분)",
    "extract": "규정 문서에서 글자를 읽는 중...",
    "chunk": "조문 단위로 나누는 중...",
}


@dataclass
class Prepared:
    ok: bool
    failed: list[str] = field(default_factory=list)  # 불러오지 못한 규정명
    error: str = ""  # 전체 실패 사유. ok가 False일 때만 쓴다
    download_error: str = ""  # 다운로드 중 오류. 받아 둔 PDF로 계속했을 수 있다


def default_download() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from download_regulations import run

    run()


def chunk_file(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "chunks" / "article.jsonl"


def is_ready(data_dir: Path = DATA_DIR) -> bool:
    return chunk_file(data_dir).exists()


def read_status(data_dir: Path = DATA_DIR) -> Prepared:
    path = data_dir / STATUS_FILE
    if not path.exists():
        return Prepared(ok=is_ready(data_dir))
    return Prepared(**json.loads(path.read_text(encoding="utf-8")))


def write_status(result: Prepared, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / STATUS_FILE).write_text(json.dumps(result.__dict__, ensure_ascii=False), encoding="utf-8")


def prepare_data(on_step: Callable[[str], None] | None = None,
                 download: Callable[[], None] = default_download,
                 pdf_dir: Path = PDF_DIR, data_dir: Path = DATA_DIR) -> Prepared:
    """청크 파일이 없으면 만든다. 이미 있으면 받지 않고 지난 결과를 돌려준다."""
    if is_ready(data_dir):
        return read_status(data_dir)
    step = on_step or (lambda name: None)

    step("download")
    download_error = ""
    try:
        download()
    except Exception as error:  # 사이트 접속 실패 등. 받아 둔 PDF가 있으면 그것으로 계속한다
        download_error = f"{type(error).__name__}: {error}"

    manifest_path = pdf_dir / "manifest.json"
    entries = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    entries = sorted(entries, key=lambda e: e["no"])
    available = [e for e in entries if e.get("files") and (pdf_dir / e["files"][0]["file"]).exists()]
    if not available:
        result = Prepared(ok=False, error="규정 문서를 받지 못했습니다", download_error=download_error)
        write_status(result, data_dir)
        return result

    step("extract")
    manual = load_manual_pages()
    failed = [e["title"] for e in entries if e not in available]
    docs = []
    for entry in available:
        try:
            docs.append(extract_document(entry, pdf_dir=pdf_dir, manual=manual, strict=False))
        except Exception:
            failed.append(entry["title"])

    step("chunk")
    chunks = []
    for doc in docs:
        try:
            chunks += article_chunks(doc)
        except Exception:
            failed.append(doc["title"])
    if not chunks:
        result = Prepared(ok=False, failed=failed, error="규정 문서를 읽지 못했습니다", download_error=download_error)
        write_status(result, data_dir)
        return result

    chunk_file(data_dir).parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(chunk_file(data_dir), chunks)
    result = Prepared(ok=True, failed=failed, download_error=download_error)
    write_status(result, data_dir)
    return result
