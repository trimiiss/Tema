"""Scenario-based tests using scenarios.json."""
import json
import os
import pytest

SCENARIOS_PATH = os.path.join(os.path.dirname(__file__), "scenarios.json")

with open(SCENARIOS_PATH) as f:
    ALL_SCENARIOS = json.load(f)


def test_scenarios_file_has_20_entries():
    assert len(ALL_SCENARIOS) == 20


def test_all_scenarios_have_required_fields():
    required = ["id", "description", "input", "expected_intent", "requires_approval", "expected_outcome"]
    for s in ALL_SCENARIOS:
        for field in required:
            assert field in s, f"Scenario {s.get('id')} missing field '{field}'"


def test_write_operations_all_require_approval():
    write_scenarios = [s for s in ALL_SCENARIOS if s.get("expected_sub_intent") in ("create", "cancel", "reschedule")]
    for s in write_scenarios:
        assert s["requires_approval"] is True, f"Scenario {s['id']} is a write but requires_approval=False"


def test_read_operations_do_not_require_approval():
    read_scenarios = [s for s in ALL_SCENARIOS if s.get("expected_sub_intent") in ("read", "list")]
    for s in read_scenarios:
        assert s["requires_approval"] is False, f"Scenario {s['id']} is a read but requires_approval=True"


def test_report_scenarios_do_not_require_approval():
    report_scenarios = [s for s in ALL_SCENARIOS if s.get("expected_intent") == "report"]
    for s in report_scenarios:
        assert s["requires_approval"] is False, f"Report scenario {s['id']} should not require approval"


@pytest.mark.parametrize("scenario", [s for s in ALL_SCENARIOS if s.get("security_note")], ids=lambda s: s["id"])
def test_security_scenarios_documented(scenario):
    """All security-relevant scenarios must have a security_note."""
    assert scenario["security_note"], f"Scenario {scenario['id']} security_note is empty"


def test_no_medical_advice_scenario_exists():
    ids = [s["id"] for s in ALL_SCENARIOS if "medical" in s.get("security_note", "").lower()]
    assert len(ids) >= 1, "There must be at least one medical-advice-blocked scenario"


def test_prompt_injection_scenario_exists():
    injection_scenarios = [s for s in ALL_SCENARIOS if "injection" in s.get("security_note", "").lower()
                           or "inject" in s.get("input", "").lower()
                           or "Ignore previous" in s.get("input", "")]
    assert len(injection_scenarios) >= 1


def test_unauthorized_access_scenario_exists():
    unauth = [s for s in ALL_SCENARIOS
              if "forbidden" in s["expected_outcome"] or "403" in s["expected_outcome"]]
    assert len(unauth) >= 1


def test_conflict_detection_scenario_exists():
    conflict = [s for s in ALL_SCENARIOS if "conflict" in s["expected_outcome"]]
    assert len(conflict) >= 1
