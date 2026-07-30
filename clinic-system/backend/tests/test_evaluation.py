"""The evaluation harness's own correctness — everything except the model calls.

`scripts/evaluate.py` produces the accuracy figures the evaluation chapter
quotes, so the parts of it that decide *what counts as correct* have to be
right, or the numbers are quietly meaningless. Those parts are pure: value
normalisation, which scenarios are scored, and whether the gold labels still
line up with the field names the document agent actually asks for.

The model calls themselves are not tested here — they cost money and are
nondeterministic, which is exactly why they live in a script and not in pytest.
"""
import importlib.util
import json
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    """Import `scripts/evaluate.py`, which is a script rather than a package."""
    spec = importlib.util.spec_from_file_location(
        "evaluate_harness", BACKEND_ROOT / "scripts" / "evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()
GOLD = json.loads((BACKEND_ROOT / "tests" / "gold_documents.json").read_text(encoding="utf-8"))
SCENARIOS = json.loads((BACKEND_ROOT / "tests" / "scenarios.json").read_text(encoding="utf-8"))


# ---- Value normalisation ----
#
# A model that reads the right value off the page but writes it back with a
# trailing full stop has not made an extraction error, and scoring it as one
# would understate accuracy for a formatting difference nobody cares about.

@pytest.mark.parametrize("raw,expected", [
    ('  "Dr. Arben Hoxha".  ', "dr. arben hoxha"),
    ("DR.  ARBEN   HOXHA", "dr. arben hoxha"),
    ("'2026-07-20',", "2026-07-20"),
    ("Fjolla Berisha;", "fjolla berisha"),
    (None, ""),
    ("", ""),
])
def test_normalise_ignores_formatting_but_not_content(raw, expected):
    assert harness._normalise(raw) == expected


def test_normalise_still_separates_genuinely_different_values():
    """Normalisation must not be so aggressive that a wrong answer scores."""
    assert harness._normalise("Dr. Arben Hoxha") != harness._normalise("Dr. Blerta Kelmendi")
    assert harness._normalise("2026-07-20") != harness._normalise("2026-07-22")


# ---- Which scenarios are scored ----

def test_public_booking_scenarios_are_excluded_from_routing():
    """They never reach the supervisor — `/public/booking/chat` runs the public
    agent directly — so counting them as routing failures would report a defect
    that does not exist."""
    assert "booking_agent" in harness.ROUTING_SKIP_AGENTS
    booking = {s["id"] for s in SCENARIOS if s.get("expected_agent") == "booking_agent"}
    assert booking, "fixture drift: no booking scenarios left to exclude"


def test_the_fallback_alias_maps_to_a_real_supervisor_choice():
    """`orchestrator_fallback` is the scenario file's spelling; the supervisor
    only ever answers with an agent name, 'finish' or 'fallback'."""
    assert harness.EXPECTED_AGENT_ALIASES["orchestrator_fallback"] == "fallback"


def test_every_scored_scenario_accepts_only_reachable_routes():
    """A gold answer the supervisor structurally cannot give would fail forever.

    Guards against a typo in `ROUTING_OVERRIDES` — an accepted route that is
    not a real node silently makes that scenario unpassable, which would look
    like a routing defect rather than a benchmark bug.
    """
    from app.agents.orchestrator import AGENT_NAMES

    reachable = set(AGENT_NAMES) | {"finish", "fallback"}
    for accepted in harness.ROUTING_OVERRIDES.values():
        assert accepted <= reachable, f"unreachable route in overrides: {accepted - reachable}"

    for s in SCENARIOS:
        expected = s.get("expected_agent")
        if not expected or expected in harness.ROUTING_SKIP_AGENTS:
            continue
        resolved = harness.ROUTING_OVERRIDES.get(
            s["id"], {harness.EXPECTED_AGENT_ALIASES.get(expected, expected)}
        )
        assert resolved <= reachable, f"{s['id']} expects unreachable {resolved - reachable}"


# ---- Gold labels vs the agent's real field set ----

def test_gold_fields_match_what_the_document_agent_actually_extracts():
    """The single most likely way this benchmark rots.

    `tool_extract_fields` asks the model for a fixed list of field names per
    document type. If that list is edited and the gold file is not, extraction
    accuracy silently collapses to zero for the renamed field and the number
    looks like a model regression instead of a fixture that moved.
    """
    import inspect
    from app.agents import document_agent

    source = inspect.getsource(document_agent.tool_extract_fields)
    for doc in GOLD["documents"]:
        doc_type = doc["expected_doc_type"]
        assert f'"{doc_type}"' in source, f"unknown doc_type in gold file: {doc_type}"
        for field in doc["fields"]:
            assert f'"{field}"' in source, (
                f"gold file expects '{field}' for {doc_type}, but "
                "tool_extract_fields never asks for it"
            )


def test_every_gold_document_has_a_specimen_pdf():
    missing = [
        d["filename"] for d in GOLD["documents"]
        if not (harness.SAMPLE_DOCS / d["filename"]).exists()
    ]
    assert not missing, f"run scripts/make_sample_documents.py — missing: {missing}"


def test_the_injection_specimen_is_probed_for_leaks():
    """The injection document is the one specimen whose *absence* of certain
    output matters as much as its extracted fields."""
    probes = [d for d in GOLD["documents"] if d.get("injection_probe")]
    assert probes, "no injection specimen is being checked for leaked content"
    for probe in probes:
        assert probe["must_not_appear"], f"{probe['filename']} probes for nothing"


def test_accepted_values_are_never_empty():
    """An empty accept list would score every answer — including a blank — as
    correct, which is worse than scoring none of them."""
    for doc in GOLD["documents"]:
        for field, accepted in doc["fields"].items():
            assert accepted, f"{doc['filename']}:{field} has no accepted values"
            assert all(str(a).strip() for a in accepted), f"{doc['filename']}:{field} accepts a blank"
