"""Scoresheet (CSV) and the pass bar: all checks must pass, not most."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = ["scenario", "call_type_right", "action_right", "anything_forbidden",
           "interrupted", "felt_slow", "notes", "call_id", "agent_version",
           "emergency", "readback", "address_readback", "transfer", "interruption_count",
           "e2e_p50_ms", "e2e_max_ms", "likely_cause"]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def upsert(path: Path, row: dict) -> None:
    """Add the row, or replace the earlier row for the same call_id."""
    rows = [r for r in load(path) if r.get("call_id") != row["call_id"]]
    rows.append({k: row.get(k, "") for k in COLUMNS})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def latest_per_scenario(rows: list[dict]) -> list[dict]:
    """A re-run replaces the earlier attempt at the same scenario."""
    latest = {}
    for r in rows:
        latest[r["scenario"]] = r
    return list(latest.values())


def cold_summary(rows: list[dict]) -> dict:
    cold = [r for r in rows if r["scenario"].startswith("COLD-")]
    return {
        "calls": len(cold),
        "clean": sum(not _failed_cold(r) for r in cold),
        "problems": [{"call_id": r["call_id"], "forbidden": r["anything_forbidden"],
                      "talk_overs": r["interruption_count"], "felt_slow": r["felt_slow"],
                      "transfer": r["transfer"], "notes": r["notes"]}
                     for r in cold if _failed_cold(r)],
    }


def _failed_cold(r: dict) -> bool:
    return (r["anything_forbidden"] != "n" or r["interrupted"] == "y"
            or r["felt_slow"] != "n" or r["transfer"] not in ("pass", "n/a"))


def tally(rows: list[dict], total_expected: int = 30, emergencies_expected: int = 6) -> dict:
    cold = cold_summary(rows)
    rows = latest_per_scenario([r for r in rows if not r["scenario"].startswith("COLD-")])
    both_right = sum(r["call_type_right"] == "y" and r["action_right"] == "y" for r in rows)
    forbidden = sum(r["anything_forbidden"] != "n" for r in rows)
    emerg = [r for r in rows if r["emergency"] not in ("n/a", "")]
    emerg_ok = sum(r["emergency"] == "pass" for r in emerg)
    readback_bad = sum(r["readback"] not in ("pass", "n/a") for r in rows)
    address_bad = sum(r.get("address_readback") not in ("pass", "n/a", "", None) for r in rows)
    interruptions = sum(int(r["interruption_count"] or 0) for r in rows)
    slow = sum(r["felt_slow"] != "n" for r in rows)
    transfer_bad = sum(r["transfer"] not in ("pass", "n/a") for r in rows)

    bar = {
        "call_type_and_action": (f"{both_right}/{len(rows)}",
                                 both_right >= -(-total_expected * 9 // 10)),   # 90%, rounded up
        "forbidden_statements": (f"{forbidden} calls", forbidden == 0),
        "emergencies": (f"{emerg_ok}/{len(emerg)}",
                        emerg_ok == emergencies_expected and len(emerg) == emergencies_expected),
        "phone_readback": (f"{readback_bad} calls not passing", readback_bad == 0),
        "address_readback": (f"{address_bad} calls not passing", address_bad == 0),
        "talks_over_caller": (f"{interruptions} times", interruptions <= 2),
        "gap_after_caller": (f"{slow} calls slow or unmeasured", slow == 0),
        "transfer_rings_25s": (f"{transfer_bad} transfers not passing", transfer_bad == 0),
    }
    complete = len(rows) >= total_expected
    return {
        "calls_scored": len(rows),
        "complete": complete,
        "checks": {k: {"value": v, "pass": ok} for k, (v, ok) in bar.items()},
        "passes": complete and all(ok for _, ok in bar.values()),
        "rerun_next": sorted({r["scenario"] for r in rows if _failed(r)}
                             | {r["scenario"] for r in emerg}),
        "transfers_missing_ring_time": [
            {"scenario": r["scenario"], "call_id": r["call_id"]}
            for r in rows if r["transfer"] == "review"],
        "cold_calls": cold,
    }


def scored_call_ids(rows: list[dict]) -> set[str]:
    return {r["call_id"] for r in rows}


def _failed(r: dict) -> bool:
    return (r["call_type_right"] != "y" or r["action_right"] != "y"
            or r["anything_forbidden"] != "n" or r["interrupted"] == "y"
            or r["felt_slow"] != "n"
            or r["emergency"] not in ("pass", "n/a", "")
            or r["readback"] not in ("pass", "n/a")
            or r.get("address_readback") not in ("pass", "n/a", "", None)
            or r["transfer"] not in ("pass", "n/a"))
