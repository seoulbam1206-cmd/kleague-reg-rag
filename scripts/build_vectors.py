"""배포용 벡터를 미리 계산해 index/에 저장한다.

배포 서버는 청크 내용 해시가 같은 벡터는 이 파일에서 가져오고, 규정이 개정되어 바뀐 청크만
새로 계산한다. 파일에는 모델 이름, 청크 내용의 해시, 벡터만 들어 있고 규정 원문은 없다.

규정을 다시 받아 청크가 바뀌었으면 이 스크립트를 다시 실행하고 결과 파일을 커밋한다.
--check는 계산하지 않고 지금 청크 중 몇 개가 파일에 있는지만 알려준다.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from search import DEFAULT_MODEL, OnnxEncoder, load_chunks, save_stored, stored_file, text_hash  # noqa: E402


def coverage(chunks: list[dict], path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, len(chunks)
    with np.load(path, allow_pickle=False) as stored:
        known = set(stored["hashes"].tolist())
    return sum(text_hash(chunk["text"]) in known for chunk in chunks), len(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--check", action="store_true", help="계산 없이 파일이 지금 청크를 덮는지만 확인")
    args = parser.parse_args()

    chunks = load_chunks("article")
    path = stored_file(args.model)
    if args.check:
        found, total = coverage(chunks, path)
        print(f"{path.relative_to(ROOT)}: 청크 {total}개 중 {found}개 있음")
        return 0 if found == total else 1

    encoder = OnnxEncoder(args.model)
    vectors = encoder.encode_passages([chunk["text"] for chunk in chunks])
    save_stored(encoder, chunks, vectors, path)
    print(f"저장: {path.relative_to(ROOT)} (청크 {len(chunks)}개, {path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
