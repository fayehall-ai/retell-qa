"""MCP server: test a Retell voice agent with real phone calls against a fixed pass bar.

Run:  RETELL_API_KEY=key_... python -m retell_qa.server
Files live in the QA folder: RETELL_QA_DIR, or the current directory when it's
unset or empty. Override each with env vars: scenarios.yaml, config.yaml,
scores.csv, cards/
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

from . import cards, match, scoring, sheet

# Not the package folder: in a plugin that's replaced on every update.
ROOT = Path(os.environ.get("RETELL_QA_DIR") or Path.cwd()).expanduser()
SCENARIOS = Path(os.environ.get("RETELL_QA_SCENARIOS", ROOT / "scenarios.yaml"))
CONFIG = Path(os.environ.get("RETELL_QA_CONFIG", ROOT / "config.yaml"))
SHEET = Path(os.environ.get("RETELL_QA_SHEET", ROOT / "scores.csv"))
CARDS = Path(os.environ.get("RETELL_QA_CARDS", ROOT / "cards"))
API = "https://api.retellai.com"

mcp = MCPServer(
    "retell-qa",
    instructions=(
        "Tests a Retell voice agent with real phone calls against a fixed pass bar. "
        "Workflow: tester_cards makes printable cards for human testers (never showing the "
        "expected answer); testers call; score_new_calls pulls the new calls, matches each "
        "to its scenario by the caller's name, number and address, and scores it; scoresheet "
        "shows the bar and what to re-run. Use score_call for one call, or with "
        "scenario_id='cold' for an unscripted call. Verdicts come from code; 'review' items "
        "need the user to check the transcript or recording. Never show the card index or "
        "expected answers to testers."
    ),
)


# ---------- Retell API ----------

def _request(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("RETELL_API_KEY")
    if not key:
        raise RuntimeError("Set RETELL_API_KEY to your Retell API key.")
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_call(call_id: str) -> dict:
    return _request("GET", f"/v2/get-call/{call_id}")


def list_ended_phone_calls(since_ms: int, agent_id: str | None) -> list[dict]:
    """Ended phone calls since a time, oldest first, all pages."""
    filters = {
        "call_type": {"type": "enum", "op": "in", "value": ["phone_call"]},
        "call_status": {"type": "enum", "op": "in", "value": ["ended"]},
        "start_timestamp": {"type": "number", "op": "ge", "value": since_ms},
    }
    if agent_id:
        filters["agent"] = [{"agent_id": agent_id}]
    body = {"filter_criteria": filters, "sort_order": "ascending", "limit": 200}
    calls = []
    while True:
        page = _request("POST", "/v3/list-calls", body)
        calls += page.get("items", [])
        if not page.get("has_more") or not page.get("pagination_key"):
            return calls
        body["pagination_key"] = page["pagination_key"]


# ---------- helpers ----------

def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _scenarios() -> list[dict]:
    return _load(SCENARIOS)["scenarios"]


def _tally() -> dict:
    scenarios = _scenarios()
    return sheet.tally(sheet.load(SHEET), total_expected=len(scenarios),
                       emergencies_expected=sum(bool(s.get("emergency")) for s in scenarios))


def _scenario(scenario_id: str) -> dict:
    for s in _scenarios():
        if s["id"].lower() == scenario_id.lower():
            return s
    raise ValueError(f"No scenario '{scenario_id}' in {SCENARIOS.name}")


def _since_ms(since: str) -> int:
    now = datetime.now().astimezone()
    s = since.strip().lower()
    if s == "today":
        t = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif s == "yesterday":
        t = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    elif s.endswith("h") and s[:-1].isdigit():
        t = now - timedelta(hours=int(s[:-1]))
    else:
        t = datetime.fromisoformat(since)
        if t.tzinfo is None:
            t = t.astimezone()
    return int(t.timestamp() * 1000)


def _summary(result: scoring.CallScore, extra: dict | None = None) -> dict:
    failing = [c for c in result.checks if c.result in ("fail", "review", "unknown")]
    out = {
        "scenario": result.scenario_id,
        "call_id": result.call_id,
        "passed_all": not failing,
        "needs_attention": [
            {"check": c.name, "result": c.result, "detail": c.detail,
             "likely_cause": c.likely_cause} for c in failing
        ],
        "e2e_p50_ms": result.e2e_p50_ms,
    }
    out.update(extra or {})
    return out


def _score(call: dict, scenario_id: str, ring_seconds: float | None, notes: str):
    config = _load(CONFIG)
    if scenario_id.lower() == "cold":
        result = scoring.score_cold(call, config, ring_seconds)
    else:
        result = scoring.score_call(call, _scenario(scenario_id), config, ring_seconds)
    sheet.upsert(SHEET, result.row(notes))
    return result


def _opening_lines(call: dict, n: int = 2) -> list[str]:
    return [l.strip() for l in scoring.spoken_text(call, "user")[:n]]


# ---------- tools ----------

@mcp.tool()
def score_call(call_id: str, scenario_id: str, ring_seconds: float | None = None,
               notes: str = "") -> dict:
    """Score one finished Retell test call and add it to the scoresheet.

    Use this for a single call, to assign a call that score_new_calls couldn't
    match, or to add the ring time to a transfer (scoring the same call again
    replaces its row).

    Checks: call type, action, forbidden statements, emergency handling, phone
    number read-back, talking over the caller, response gap, transfer, and
    whether expected names were heard. Each failure names its likely cause:
    prompt, turn_settings, speech_to_text, or transfer_config.

    Args:
        call_id: Retell call ID.
        scenario_id: ID from scenarios.yaml (e.g. "E01"), or "cold" for an
            unscripted call, which is scored only on forbidden statements,
            talk-overs, gap and transfer.
        ring_seconds: seconds until a phone rang on a transfer; Retell does not
            report this. Without it a transfer is marked review.
        notes: what the tester noticed (e.g. "long pause after address").
    """
    return _summary(_score(fetch_call(call_id), scenario_id, ring_seconds, notes),
                    {"scoresheet": str(SHEET)})


@mcp.tool()
def score_new_calls(since: str = "today") -> dict:
    """Find test calls not yet scored, match each to its scenario, and score it.

    Pulls ended phone calls to the agent (agent_id in config.yaml) since the
    given time, skips calls already on the scoresheet, and matches each to a
    scenario by the caller's name, phone number and street in the transcript.
    A call is scored only when one scenario is clearly the match; otherwise it
    is listed under `unmatched` with its opening lines and closest candidates,
    for the user to assign with score_call (or score as "cold").

    Args:
        since: "today", "yesterday", "3h" (last 3 hours), or an ISO date/time
            such as "2026-10-21T09:00".
    """
    config = _load(CONFIG)
    scenarios = _scenarios()
    done = sheet.scored_call_ids(sheet.load(SHEET))
    listed = list_ended_phone_calls(_since_ms(since), config.get("agent_id") or None)
    scored, unmatched = [], []
    for item in listed:
        if item["call_id"] in done:
            continue
        call = fetch_call(item["call_id"])
        m = match.match(call, scenarios)
        if m["scenario"]:
            result = _score(call, m["scenario"], None, "")
            scored.append(_summary(result, {"matched_on": m["evidence"]}))
        else:
            started = call.get("start_timestamp")
            unmatched.append({
                "call_id": call.get("call_id"),
                "started": datetime.fromtimestamp(started / 1000).astimezone().isoformat(
                    timespec="minutes") if started else None,
                "from_number": call.get("from_number"),
                "caller_said": _opening_lines(call),
                "closest": m["candidates"],
            })
    return {
        "since": since,
        "calls_found": len(listed),
        "already_scored": sum(i["call_id"] in done for i in listed),
        "scored": scored,
        "unmatched": unmatched,
        "transfers_needing_ring_time": [
            s["call_id"] for s in scored
            if any(a["check"] == "transfer" and a["result"] == "review"
                   for a in s["needs_attention"])],
    }


@mcp.tool()
def scoresheet() -> dict:
    """Progress against the pass bar, using the latest attempt at each scenario.

    Returns each of the eight checks with its value and pass/fail, whether
    every scenario in scenarios.yaml is scored, whether the whole bar passes, which scenarios to
    re-run next (failures plus all emergencies), transfers still missing a
    ring time, and a separate summary of cold calls.
    """
    return _tally()


@mcp.tool()
def tester_cards(scenario_ids: list[str] | None = None, testers: int = 1,
                 rerun: bool = False) -> dict:
    """Make printable cards for human testers, split into one packet per tester.

    Each packet starts with a one-page "how to make a test call" sheet, then one
    card per call: the number to call, who to be, what happened to them, and
    how to play it (talk fast, interrupt, number in pieces...). Cards never show
    the scenario ID, the expected call type or action, or whether it's an
    emergency. Also writes an owner-only index (card number to scenario), which
    must not be shared with testers. Returns warnings for answer leaks in the
    wording and for names or streets that would make calls hard to match.

    Args:
        scenario_ids: which scenarios; default all.
        testers: number of packets; cards are dealt round-robin so emergencies
            spread across testers.
        rerun: if true, ignore scenario_ids and make cards for the scoresheet's
            re-run list (failures plus all emergencies).
    """
    ids = scenario_ids
    if rerun:
        ids = _tally()["rerun_next"]
        if not ids:
            return {"message": "Nothing to re-run: the scoresheet has no failures yet."}
    result = cards.build(_scenarios(), _load(CONFIG), ids, testers, CARDS)
    if not result["pdf"]:
        result["note"] = "PDFs skipped: run `pip install reportlab` to get printable PDFs."
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
