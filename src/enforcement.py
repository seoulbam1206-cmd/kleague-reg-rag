"""규정의 시행일을 계산하고, 아직 시행 전이면 표시 문구를 만든다.

기준일(effective_date)은 개정 메모의 날짜이고, 시행일(enforcement_date)은 실제로 효력이
생기는 날이다. 시행일은 docs_public/manual/enforcement.json의 규칙으로 구한다.
"""

import json
from datetime import date, timedelta
from pathlib import Path

ENFORCEMENT_FILE = Path(__file__).resolve().parent.parent / "docs_public" / "manual" / "enforcement.json"


def load_enforcement_rules(path: Path = ENFORCEMENT_FILE) -> dict[int, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(no): rule for no, rule in raw.items() if not no.startswith("_")}


def resolve_enforcement(no: int, effective_date: str, rules: dict[int, dict]) -> dict:
    """규정 번호와 기준일로 {"date", "basis"}를 돌려준다."""
    rule = rules[no]
    kind = rule["rule"]
    if kind == "explicit":
        if rule["as_of"] != effective_date:
            raise ValueError(
                f"{no}번 규정이 개정됨: enforcement.json의 시행일을 다시 확인할 것 "
                f"({rule['as_of']} → {effective_date})"
            )
        return {"date": rule["date"], "basis": rule["basis"]}
    if kind == "next_day":
        next_day = date.fromisoformat(effective_date) + timedelta(days=1)
        return {"date": next_day.isoformat(), "basis": rule["basis"]}
    if kind in ("same_day", "unknown"):
        return {"date": effective_date, "basis": rule["basis"]}
    raise ValueError(f"알 수 없는 시행일 규칙: {kind}")


def enforcement_notice(enforcement_date: str, today: date | None = None) -> str:
    """시행일이 오늘 이후면 표시 문구를, 이미 시행 중이면 빈 문자열을 돌려준다."""
    today = today or date.today()
    if date.fromisoformat(enforcement_date) > today:
        return f"시행 전 ({enforcement_date} 시행 예정)"
    return ""
