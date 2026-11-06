"""시행일 계산과 시행 전 표시 검증. 시행일 기대값은 각 규정의 시행 조항에서 확인했다."""

from datetime import date

import pytest

from enforcement import enforcement_notice, load_enforcement_rules, resolve_enforcement

# 규정별 기준일 (tests/test_extract.py의 EFFECTIVE와 같은 manifest 값)
EFFECTIVE = {
    1: "2020-11-05", 2: "2020-11-05", 3: "2026-06-17", 4: "2026-01-16", 5: "2026-01-16",
    6: "2026-01-16", 7: "2024-02-23", 8: "2026-06-17", 9: "2026-06-17", 10: "2026-06-17",
    11: "2026-01-16", 12: "2024-01-15", 13: "2026-01-16", 14: "2024-11-29", 15: "2026-01-16",
    16: "2024-11-29", 17: "2026-06-29",
}
ENFORCEMENT = {
    **EFFECTIVE,
    5: "2026-01-17",  # 의결 다음날
    9: "2026-06-18",  # 의결 다음날
    13: "2026-01-17",  # 의결 다음날
    16: "2027-01-01",  # 제28조
}


def test_all_17_regulations_have_rules():
    assert set(load_enforcement_rules()) == set(range(1, 18))


def test_every_rule_states_basis():
    assert all(rule["basis"] for rule in load_enforcement_rules().values())


def test_enforcement_dates():
    rules = load_enforcement_rules()
    actual = {no: resolve_enforcement(no, d, rules)["date"] for no, d in EFFECTIVE.items()}
    assert actual == ENFORCEMENT


def test_next_day_crosses_month_end():
    rules = {1: {"rule": "next_day", "basis": "부칙"}}
    assert resolve_enforcement(1, "2026-01-31", rules)["date"] == "2026-02-01"


def test_revised_explicit_rule_requires_review():
    rules = load_enforcement_rules()
    with pytest.raises(ValueError, match="다시 확인"):
        resolve_enforcement(16, "2027-03-01", rules)


def test_unknown_rule_is_rejected():
    with pytest.raises(ValueError):
        resolve_enforcement(1, "2026-01-16", {1: {"rule": "later", "basis": ""}})


def test_notice_before_enforcement():
    notice = enforcement_notice("2027-01-01", today=date(2026, 11, 6))
    assert notice == "시행 전 (2027-01-01 시행 예정)"


@pytest.mark.parametrize("today", [date(2027, 1, 1), date(2027, 1, 2)])
def test_no_notice_from_enforcement_day(today):
    assert enforcement_notice("2027-01-01", today=today) == ""


def test_only_doc_16_is_pending_today():
    today = date(2026, 11, 6)
    pending = [no for no, d in ENFORCEMENT.items() if enforcement_notice(d, today=today)]
    assert pending == [16]
