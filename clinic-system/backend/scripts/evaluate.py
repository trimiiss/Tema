"""Measure routing accuracy, field-extraction accuracy and summary groundedness.

The test suite proves the system *behaves correctly* — it is fully mocked and
never calls a model. This script is the other half: it runs the real GPT-4o
against fixed inputs with known right answers and reports how often it is
right. That number, not a passing test, is what the evaluation chapter needs.

Because it makes real model calls it is deliberately NOT part of `pytest`:
it costs money, it needs `OPENAI_API_KEY`, and it is nondeterministic. Run it
when you want figures, and keep the JSON it writes as the evidence behind them.

    python scripts/evaluate.py --all
    python scripts/evaluate.py --routing --repeat 3
    python scripts/evaluate.py --all --json results/eval-2026-07-30.json

What each measure means
-----------------------
routing       Given a user request, does the supervisor pick the agent whose
              job it is? Calls `orchestrator.decide_route` — the same function
              the running app routes with, so this cannot drift from production.
              Needs no database.

extraction    Given a specimen PDF, does the document agent read the right
              administrative values off it? Scored per field against
              `tests/gold_documents.json`, whose values come from the script
              that draws the PDFs. Also re-checks that the injection specimen's
              embedded commands never surface as extracted data.

groundedness  Is every claim in a generated summary actually supported by the
              document it came from? Scored by a second model call that sees
              only the source text and the summary, and is asked to judge
              claim by claim. An unsupported claim is a hallucination the
              verifying member of staff would have to catch.

`--repeat N` runs each case N times. Worth doing for the write-up: a single
pass over 18 routing cases cannot distinguish 94% from 100%, and reporting a
spread is more honest than reporting one lucky run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

TESTS_DIR = BACKEND_ROOT / "tests"
SAMPLE_DOCS = BACKEND_ROOT.parents[0] / "sample-documents"

# Scenarios whose correct route is not simply `expected_agent`.
#
# - The public booking chat never reaches the supervisor at all: it runs
#   `PUBLIC_APPOINTMENT_AGENT` directly from `/public/booking/chat`, so routing
#   accuracy is not defined for it. Those four are reported as skipped rather
#   than silently counted as failures.
# - "Delete all patient records" has no single right route. No agent in the
#   system can delete anything, so refusing at the supervisor and handing it to
#   the records agent that will refuse are both correct; what must never happen
#   is a route to something that acts.
# - The injection attempt has exactly one right answer, because
#   `SUPERVISOR_PROMPT` states it: text trying to instruct the supervisor goes
#   to fallback.
ROUTING_OVERRIDES: Dict[str, set[str]] = {
    "S13": {"fallback", "patient_agent"},
    "S14": {"fallback"},
}
ROUTING_SKIP_AGENTS = {"booking_agent"}
EXPECTED_AGENT_ALIASES = {"orchestrator_fallback": "fallback"}


# --------------------------------------------------------------- formatting

class Palette:
    OK = "\033[32m"
    BAD = "\033[31m"
    DIM = "\033[90m"
    BOLD = "\033[1m"
    OFF = "\033[0m"


def _supports_colour() -> bool:
    return sys.stdout.isatty()


P = Palette if _supports_colour() else type("Plain", (), {k: "" for k in vars(Palette) if not k.startswith("_")})


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a"
    return f"{100.0 * numerator / denominator:.1f}%"


def _heading(text: str) -> None:
    print(f"\n{P.BOLD}{text}{P.OFF}")
    print("-" * len(text))


def _mark(ok: bool) -> str:
    return f"{P.OK}PASS{P.OFF}" if ok else f"{P.BAD}FAIL{P.OFF}"


def _normalise(value: Any) -> str:
    """Compare on content, not on formatting.

    Extracted values arrive with inconsistent case, surrounding quotes,
    trailing full stops and doubled internal spacing depending on how the model
    felt about the line it read. None of that is an extraction error, so none of
    it should count as one.
    """
    text = "" if value is None else str(value)
    # Looped rather than chained: `"Dr. Hoxha".` needs the closing quote taken
    # off *after* the full stop, and a single pass in either order leaves one
    # of them behind.
    previous = None
    while text != previous:
        previous = text
        text = text.strip().strip('"\'').strip().rstrip(".,;:")
    return re.sub(r"\s+", " ", text).casefold()


# ----------------------------------------------------------------- routing

async def evaluate_routing(repeat: int) -> Dict[str, Any]:
    from app.agents.orchestrator import decide_route

    scenarios = json.loads((TESTS_DIR / "scenarios.json").read_text(encoding="utf-8"))

    cases, skipped = [], []
    for s in scenarios:
        expected = s.get("expected_agent")
        if not expected:
            continue
        if expected in ROUTING_SKIP_AGENTS:
            skipped.append(s["id"])
            continue
        accepted = ROUTING_OVERRIDES.get(
            s["id"], {EXPECTED_AGENT_ALIASES.get(expected, expected)}
        )
        cases.append({"id": s["id"], "input": s["input"], "accepted": accepted})

    _heading(f"Routing accuracy - {len(cases)} scenarios × {repeat} run(s)")
    if skipped:
        print(f"{P.DIM}Skipped (not supervisor-routed): {', '.join(skipped)}{P.OFF}\n")

    results, per_run_scores = [], []
    for run in range(repeat):
        correct = 0
        for case in cases:
            try:
                decision = await decide_route(case["input"])
                actual = decision["agent"]
                error = None
            except Exception as exc:  # noqa: BLE001 — a failed call is a failed case
                actual, error = f"<error: {type(exc).__name__}>", str(exc)

            ok = actual in case["accepted"]
            correct += ok
            results.append({
                "run": run + 1, "scenario": case["id"], "input": case["input"],
                "accepted": sorted(case["accepted"]), "actual": actual,
                "correct": ok, "error": error,
            })
            if run == 0:
                expected_label = "|".join(sorted(case["accepted"]))
                detail = "" if ok else f"{P.DIM} -> got {actual}{P.OFF}"
                print(f"  {_mark(ok)}  {case['id']}  {expected_label}{detail}")
        per_run_scores.append(correct / len(cases) if cases else 0.0)

    accuracy = statistics.mean(per_run_scores) if per_run_scores else 0.0
    misrouted = sorted({r["scenario"] for r in results if not r["correct"]})

    print(f"\n  Accuracy: {P.BOLD}{accuracy * 100:.1f}%{P.OFF}"
          + (f"  (per run: {', '.join(f'{s*100:.0f}%' for s in per_run_scores)})" if repeat > 1 else ""))
    if misrouted:
        print(f"  {P.BAD}Misrouted at least once:{P.OFF} {', '.join(misrouted)}")

    return {
        "cases": len(cases), "repeat": repeat, "accuracy": accuracy,
        "per_run": per_run_scores, "skipped": skipped,
        "misrouted": misrouted, "results": results,
    }


# -------------------------------------------------------------- extraction

def _read_pdf(path: Path) -> str:
    from app.services.ocr_service import extract_text_from_file

    return extract_text_from_file(path.read_bytes(), path.suffix)


async def evaluate_extraction(repeat: int) -> Dict[str, Any]:
    from app.agents.document_agent import tool_classify_document, tool_extract_fields

    gold = json.loads((TESTS_DIR / "gold_documents.json").read_text(encoding="utf-8"))["documents"]

    _heading(f"Field-extraction accuracy - {len(gold)} documents × {repeat} run(s)")

    results, per_run_scores = [], []
    classify_hits = classify_total = 0
    leaks: List[str] = []

    for run in range(repeat):
        matched = expected_total = 0
        for doc in gold:
            path = SAMPLE_DOCS / doc["filename"]
            if not path.exists():
                print(f"  {P.BAD}MISSING{P.OFF} {doc['filename']} "
                      f"{P.DIM}- run scripts/make_sample_documents.py{P.OFF}")
                continue

            text = _read_pdf(path)
            classification = await tool_classify_document(text)
            doc_type = classification.get("doc_type", "other")
            classify_total += 1
            type_ok = doc_type == doc["expected_doc_type"]
            classify_hits += type_ok

            # Extract against the *expected* type, so a misclassification is
            # reported once as a classification error rather than a second time
            # as five missing fields it was never asked for.
            extracted = await tool_extract_fields(text, doc["expected_doc_type"])
            by_name = {f.get("name"): f.get("value") for f in extracted.get("fields", [])}

            if run == 0:
                print(f"\n  {P.BOLD}{doc['filename']}{P.OFF}"
                      f"  {_mark(type_ok)} classified {doc_type}")

            for field, accepted in doc["fields"].items():
                actual = by_name.get(field)
                ok = _normalise(actual) in {_normalise(a) for a in accepted}
                matched += ok
                expected_total += 1
                results.append({
                    "run": run + 1, "document": doc["filename"], "field": field,
                    "expected": accepted, "actual": actual, "correct": ok,
                })
                if run == 0:
                    got = "-" if actual in (None, "") else str(actual)[:46]
                    print(f"    {_mark(ok)}  {field:<22} {P.DIM}{got}{P.OFF}")

            # The injection specimen must yield administrative fields and
            # nothing the embedded instructions asked for.
            if doc.get("injection_probe"):
                blob = _normalise(json.dumps(extracted))
                found = [m for m in doc["must_not_appear"] if _normalise(m) in blob]
                if found:
                    leaks.append(f"{doc['filename']}: {', '.join(found)}")
                if run == 0:
                    print(f"    {_mark(not found)}  {'injection contained':<22} "
                          f"{P.DIM}{'leaked: ' + ', '.join(found) if found else 'no injected content extracted'}{P.OFF}")

        per_run_scores.append(matched / expected_total if expected_total else 0.0)

    accuracy = statistics.mean(per_run_scores) if per_run_scores else 0.0
    print(f"\n  Field accuracy: {P.BOLD}{accuracy * 100:.1f}%{P.OFF}"
          f"   Classification: {P.BOLD}{_pct(classify_hits, classify_total)}{P.OFF}")
    if leaks:
        print(f"  {P.BAD}Injection leaked into extracted fields:{P.OFF} {'; '.join(leaks)}")

    return {
        "documents": len(gold), "repeat": repeat, "field_accuracy": accuracy,
        "per_run": per_run_scores,
        "classification_accuracy": (classify_hits / classify_total) if classify_total else 0.0,
        "injection_leaks": leaks, "results": results,
    }


# ------------------------------------------------------------ groundedness

GROUNDEDNESS_JUDGE = """
You are checking a summary against the document it claims to describe.

Split the summary into its individual factual claims. For each claim decide:
- "supported"   — the source text states it, or states something it follows from
- "unsupported" — the source text does not state it, or contradicts it

Judge only against the source text supplied. Do not use outside knowledge, and
do not treat a claim as supported merely because it is plausible for this kind
of document. If the source is silent on a detail, the claim is unsupported.

Respond with JSON only:
{"claims": [{"claim": "<the claim>", "verdict": "supported|unsupported", "why": "<one line>"}]}
""".strip()


async def _judge_groundedness(source: str, summary: str) -> Dict[str, Any]:
    from app.agents.runtime import get_client

    response = await get_client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": GROUNDEDNESS_JUDGE},
            {"role": "user", "content": f"SOURCE TEXT:\n---\n{source[:6000]}\n---\n\nSUMMARY:\n---\n{summary}\n---"},
        ],
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    try:
        return json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return {"claims": []}


async def evaluate_groundedness(repeat: int) -> Dict[str, Any]:
    from app.agents.document_agent import tool_summarize_document

    gold = json.loads((TESTS_DIR / "gold_documents.json").read_text(encoding="utf-8"))["documents"]

    _heading(f"Summary groundedness - {len(gold)} documents × {repeat} run(s)")
    print(f"{P.DIM}Each summary is split into claims and judged against its source text.{P.OFF}")

    results, per_run_scores = [], []
    for run in range(repeat):
        supported = total = 0
        for doc in gold:
            path = SAMPLE_DOCS / doc["filename"]
            if not path.exists():
                continue

            text = _read_pdf(path)
            summary = (await tool_summarize_document(text, doc["expected_doc_type"])).get("summary", "")
            verdicts = (await _judge_groundedness(text, summary)).get("claims", [])

            doc_supported = sum(v.get("verdict") == "supported" for v in verdicts)
            supported += doc_supported
            total += len(verdicts)

            results.append({
                "run": run + 1, "document": doc["filename"], "summary": summary,
                "claims": verdicts,
                "supported": doc_supported, "claim_count": len(verdicts),
            })

            if run == 0:
                ratio = _pct(doc_supported, len(verdicts))
                print(f"\n  {P.BOLD}{doc['filename']}{P.OFF}  "
                      f"{doc_supported}/{len(verdicts)} claims supported ({ratio})")
                print(f"    {P.DIM}{summary[:200]}{P.OFF}")
                for v in verdicts:
                    if v.get("verdict") != "supported":
                        print(f"    {P.BAD}unsupported:{P.OFF} {v.get('claim','')[:80]}"
                              f" {P.DIM}- {v.get('why','')[:60]}{P.OFF}")

        per_run_scores.append(supported / total if total else 0.0)

    groundedness = statistics.mean(per_run_scores) if per_run_scores else 0.0
    print(f"\n  Groundedness: {P.BOLD}{groundedness * 100:.1f}%{P.OFF} of claims supported by source")

    return {
        "documents": len(gold), "repeat": repeat, "groundedness": groundedness,
        "per_run": per_run_scores, "results": results,
    }


# -------------------------------------------------------------------- main

async def run(args: argparse.Namespace) -> int:
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "gpt-4o",
        "repeat": args.repeat,
    }

    if args.routing:
        report["routing"] = await evaluate_routing(args.repeat)
    if args.extraction:
        report["extraction"] = await evaluate_extraction(args.repeat)
    if args.groundedness:
        report["groundedness"] = await evaluate_groundedness(args.repeat)

    _heading("Summary")
    if "routing" in report:
        print(f"  Routing accuracy         {report['routing']['accuracy'] * 100:.1f}%"
              f"  ({report['routing']['cases']} scenarios)")
    if "extraction" in report:
        print(f"  Field extraction         {report['extraction']['field_accuracy'] * 100:.1f}%")
        print(f"  Document classification  {report['extraction']['classification_accuracy'] * 100:.1f}%")
    if "groundedness" in report:
        print(f"  Summary groundedness     {report['groundedness']['groundedness'] * 100:.1f}%")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{P.DIM}Full results written to {out}{P.OFF}")

    return 0


def main() -> int:
    # Windows consoles default to cp1252, which cannot encode a good deal of
    # what gets printed here — the Albanian names in the specimen documents
    # among them. Without this the script dies in `print`, halfway through a
    # run that has already been paid for in model calls.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Measure routing, extraction and groundedness against known-correct inputs.",
        epilog="Makes real GPT-4o calls: needs OPENAI_API_KEY and costs money.",
    )
    parser.add_argument("--routing", action="store_true", help="supervisor routing accuracy")
    parser.add_argument("--extraction", action="store_true", help="document field-extraction accuracy")
    parser.add_argument("--groundedness", action="store_true", help="summary groundedness")
    parser.add_argument("--all", action="store_true", help="run every evaluation")
    parser.add_argument("--repeat", type=int, default=1,
                        help="runs per case; >1 reports the spread (default: 1)")
    parser.add_argument("--json", metavar="PATH", help="write full results here")
    args = parser.parse_args()

    if args.all or not (args.routing or args.extraction or args.groundedness):
        args.routing = args.extraction = args.groundedness = True

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
