"""보안 하네스 설정 검증."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "path",
    ["docs_private/secret.pdf", ".env", ".env.local", ".venv/pyvenv.cfg", "venv/pyvenv.cfg"],
)
def test_sensitive_paths_are_git_ignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{path}가 .gitignore에서 제외되지 않음"


@pytest.mark.parametrize(
    "rule",
    ["Read(./docs_private)", "Read(./docs_private/**)", "Read(./.env)", "Read(./.env.*)"],
)
def test_read_deny_rules_exist(rule):
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert rule in settings["permissions"]["deny"]


def test_docs_private_not_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "docs_private", ".env"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert tracked.stdout.strip() == ""
