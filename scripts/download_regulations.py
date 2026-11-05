"""K리그 공개 규정 문서 다운로드 스크립트.

목록(regulations.do)에서 번호/규정명/등록일/seq를 읽고, 상세(regulations_view.do?seq=N)의
첨부파일을 docs_public/에 "번호_규정명_등록일.확장자"로 저장한다.
메타데이터는 docs_public/manifest.json에 기록한다.

등록일은 개정해도 바뀌지 않으므로 갱신 판단에 쓰지 않는다. 개정 메모(body_text),
첨부 URL, 파일 해시(sha256)를 비교해 달라졌을 때만 받고, 받은 뒤에도 내용이 같으면
파일을 교체하지 않는다. URL과 메모가 그대로인 채 서버 파일만 바뀐 경우까지 확인하려면
--verify-hash로 실행한다.
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

BASE = "https://www.kleague.com"
LIST_URL = f"{BASE}/about/regulations.do"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs_public"
MANIFEST = OUT_DIR / "manifest.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (kleague-reg-rag downloader)"}


def polite_sleep():
    time.sleep(random.uniform(1.0, 2.0))


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    return re.sub(r"\s+", " ", s).strip().replace(" ", "_")


def encode_url(url: str) -> str:
    """공백/한글이 포함된 첨부파일 URL을 퍼센트 인코딩."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path), p.query, p.fragment))


def parse_list(session: requests.Session) -> list[dict]:
    r = session.get(LIST_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for tr in soup.select("tbody tr"):
        a = tr.select_one('a[href*="regulations_view.do"]')
        tds = tr.find_all("td")
        if not a or len(tds) < 3:
            continue
        m = re.search(r"seq=(\d+)", a["href"])
        items.append({
            "no": int(tds[0].get_text(strip=True)),
            "title": a.get_text(strip=True),
            "seq": int(m.group(1)),
            "registered": tds[2].get_text(strip=True),
            "view_url": urljoin(BASE, a["href"]),
        })
    return items


def parse_view(session: requests.Session, url: str) -> dict:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    con = soup.select_one(".board-con")
    files = [
        {"name": a.get_text(strip=True), "url": a["href"].strip()}
        for a in soup.select(".board-file a[href]")
    ]
    return {"body_text": con.get_text(" ", strip=True) if con else "", "attachments": files}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fetch_reason(
    old_entry: dict | None, old_file: dict | None, body_text: str, url: str, dest: Path
) -> str | None:
    """다시 받아야 하는 이유를 돌려준다. 건너뛰어도 되면 None."""
    if not dest.exists() or dest.stat().st_size == 0:
        return "로컬 파일 없음"
    if old_entry is None or old_file is None:
        return "이전 기록 없음"
    if old_entry.get("body_text") != body_text:
        return "개정 메모 변경"
    if old_file.get("url") != url:
        return "첨부 URL 변경"
    stored = old_file.get("sha256")
    if stored and sha256_file(dest) != stored:
        return "로컬 해시 불일치"
    return None


def save_if_changed(content: bytes, dest: Path) -> bool:
    """내용이 다를 때만 원자적으로 저장한다. 저장했으면 True."""
    if dest.exists() and sha256_file(dest) == sha256_bytes(content):
        return False
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)
    return True


def remove_renamed(old_file: dict | None, dest: Path) -> None:
    """파일명이 바뀌면 이전 판을 지운다. 남겨두면 옛 규정이 함께 색인된다."""
    if old_file and old_file.get("file") and old_file["file"] != dest.name:
        (dest.parent / Path(old_file["file"]).name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-hash", action="store_true", help="모든 첨부를 받아 해시로 대조한다"
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    old = {}
    if MANIFEST.exists():
        old = {e["seq"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))}

    session = requests.Session()
    session.headers.update(HEADERS)

    items = parse_list(session)
    print(f"목록 {len(items)}건")
    manifest = []
    for it in items:
        polite_sleep()
        view = parse_view(session, it["view_url"])
        old_entry = old.get(it["seq"])
        body_text = view["body_text"]
        if not body_text and old_entry:
            print(f"[{it['no']:02d}] 경고: 개정 메모를 읽지 못해 이전 값을 유지")
            body_text = old_entry.get("body_text", "")
        entry = {**it, "body_text": body_text, "files": []}
        if not view["attachments"]:
            print(f"[{it['no']:02d}] {it['title']}: 첨부파일 없음")
        date = it["registered"].replace(".", "")
        for i, att in enumerate(view["attachments"], 1):
            ext = Path(urlsplit(att["url"]).path).suffix.lower() or ".bin"
            suffix = f"_{i}" if len(view["attachments"]) > 1 else ""
            fname = f"{it['no']:02d}_{safe_name(it['title'])}_{date}{suffix}{ext}"
            dest = OUT_DIR / fname
            old_files = old_entry["files"] if old_entry else []
            old_file = old_files[i - 1] if i <= len(old_files) else None
            reason = fetch_reason(old_entry, old_file, body_text, att["url"], dest)
            if reason is None and args.verify_hash:
                reason = "해시 검증"
            if reason is None:
                print(f"[{it['no']:02d}] 건너뜀 (변경 없음): {fname}")
            else:
                polite_sleep()
                resp = session.get(encode_url(att["url"]), timeout=120)
                resp.raise_for_status()
                if save_if_changed(resp.content, dest):
                    remove_renamed(old_file, dest)
                    print(f"[{it['no']:02d}] 저장 ({reason}): {fname} ({len(resp.content):,} bytes)")
                else:
                    print(f"[{it['no']:02d}] 내용 동일 ({reason}): {fname}")
            entry["files"].append({
                "file": fname,
                "format": ext.lstrip(".").upper(),
                "original_name": att["name"],
                "url": att["url"],
                "size": dest.stat().st_size,
                "sha256": sha256_file(dest),
            })
        manifest.append(entry)

    # 목록에서 사라진 이전 항목도 보존
    seen = {e["seq"] for e in manifest}
    manifest += [e for s, e in old.items() if s not in seen]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest 저장: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
