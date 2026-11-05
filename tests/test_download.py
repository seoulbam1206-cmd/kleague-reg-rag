"""다운로드 건너뛰기 판단 검증. 네트워크를 쓰지 않는다."""

import pytest

from download_regulations import (
    fetch_reason,
    remove_renamed,
    save_if_changed,
    sha256_bytes,
)

URL = "https://example.com/v1/about/rule_260116.pdf"
BODY = "2026/01/16 개정"
CONTENT = b"%PDF-1.4 rule"


@pytest.fixture
def dest(tmp_path):
    path = tmp_path / "15_rule_20240327.pdf"
    path.write_bytes(CONTENT)
    return path


@pytest.fixture
def entry():
    return {"body_text": BODY}


@pytest.fixture
def file_rec(dest):
    return {"file": dest.name, "url": URL, "sha256": sha256_bytes(CONTENT)}


def test_unchanged_is_skipped(entry, file_rec, dest):
    assert fetch_reason(entry, file_rec, BODY, URL, dest) is None


def test_same_registered_date_but_new_memo_is_fetched(entry, file_rec, dest):
    """등록일과 파일명이 그대로여도 개정 메모가 바뀌면 받아야 한다."""
    assert fetch_reason(entry, file_rec, "2026/06/17 개정", URL, dest) == "개정 메모 변경"


def test_new_attachment_url_is_fetched(entry, file_rec, dest):
    new_url = URL.replace("260116", "260617")
    assert fetch_reason(entry, file_rec, BODY, new_url, dest) == "첨부 URL 변경"


def test_corrupted_local_file_is_fetched(entry, file_rec, dest):
    dest.write_bytes(b"corrupted")
    assert fetch_reason(entry, file_rec, BODY, URL, dest) == "로컬 해시 불일치"


def test_missing_and_empty_files_are_fetched(entry, file_rec, dest):
    dest.write_bytes(b"")
    assert fetch_reason(entry, file_rec, BODY, URL, dest) == "로컬 파일 없음"
    dest.unlink()
    assert fetch_reason(entry, file_rec, BODY, URL, dest) == "로컬 파일 없음"


def test_no_previous_record_is_fetched(dest):
    assert fetch_reason(None, None, BODY, URL, dest) == "이전 기록 없음"


def test_old_manifest_without_hash_is_skipped(entry, file_rec, dest):
    """sha256이 없는 기존 manifest는 전체를 다시 받지 않고 건너뛴다."""
    del file_rec["sha256"]
    assert fetch_reason(entry, file_rec, BODY, URL, dest) is None


def test_save_keeps_file_when_content_is_identical(dest):
    before = dest.stat().st_mtime_ns
    assert save_if_changed(CONTENT, dest) is False
    assert dest.stat().st_mtime_ns == before


def test_save_replaces_changed_content_without_leftovers(dest):
    assert save_if_changed(b"new revision", dest) is True
    assert dest.read_bytes() == b"new revision"
    assert [p.name for p in dest.parent.iterdir()] == [dest.name]


def test_renamed_file_removes_previous_version(tmp_path):
    old = tmp_path / "15_rule_20240327.pdf"
    new = tmp_path / "15_rule_20260116.pdf"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    remove_renamed({"file": old.name}, new)
    assert not old.exists() and new.exists()


def test_same_name_is_not_removed(dest):
    remove_renamed({"file": dest.name}, dest)
    assert dest.exists()
