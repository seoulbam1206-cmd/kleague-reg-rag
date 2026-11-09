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


RECHECK_NOTICE = "시행일 재확인 필요"


def resolve_enforcement(no: int, effective_date: str, rules: dict[int, dict], strict: bool = True) -> dict:
    """규정 번호와 기준일로 {"date", "basis"}를 돌려준다.

    시행일을 직접 적어 둔 규정(explicit)이 개정되면 기본은 오류로 멈춘다. 배포 앱은 strict=False로 불러
    멈추지 않고 기준일을 시행일로 대신 쓰며 recheck=True를 붙인다. 화면에는 "시행일 재확인 필요"가 나온다.
    """
    rule = rules[no]
    kind = rule["rule"]
    if kind == "explicit":
        if rule["as_of"] != effective_date and not strict:
            return {"date": effective_date, "basis": f"{RECHECK_NOTICE}: 개정됨 ({rule['as_of']} → {effective_date})",
                    "recheck": True}
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


def chunk_notice(chunk: dict, today: date | None = None) -> str:
    """청크에 붙일 시행 관련 표시. 시행일 재확인이 필요하면 그 문구가 우선한다."""
    if chunk.get("enforcement_recheck"):
        return RECHECK_NOTICE
    return enforcement_notice(chunk["enforcement_date"], today)


def enforcement_notice(enforcement_date: str, today: date | None = None) -> str:
    """시행일이 오늘 이후면 표시 문구를, 이미 시행 중이면 빈 문자열을 돌려준다."""
    today = today or date.today()
    if date.fromisoformat(enforcement_date) > today:
        return f"시행 전 ({enforcement_date} 시행 예정)"
    return ""
