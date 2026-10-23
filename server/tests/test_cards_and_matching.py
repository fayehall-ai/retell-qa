import copy
from pathlib import Path

import yaml

from retell_qa import cards, match, scoring, server, sheet
from tests.test_scoring import CONFIG, SCEN, gas_call_good, quote_call_bad, utt

ROOT = Path(__file__).resolve().parents[2] / "examples" / "demo-hvac"
SCENARIOS = yaml.safe_load((ROOT / "scenarios.yaml").read_text())["scenarios"]


# ---------- cards ----------

def test_cards_never_show_the_answer(tmp_path):
    out = cards.build(SCENARIOS, CONFIG, None, 1, tmp_path)
    text = (tmp_path / "tester-1.md").read_text()
    for s in SCENARIOS:
        assert s["id"] not in text                       # E01 would hint "emergency"
        assert s["title"] not in text
        assert s["caller"]["name"] in text
    for word in ["emergency", "transfer_to_owner", "book_appointment", "take_message",
                 "quote_request", "existing_job", "must_say"]:
        assert word not in text.lower().replace("connecting you", "")
    assert "702... 555... uh, 0102" in text              # 'pieces' uses the card's own number
    assert out["pdf"] and (tmp_path / "tester-1.pdf").exists()
    assert "| 01 | 1 | E01 |" in (tmp_path / "index.md").read_text()


def test_round_robin_spreads_emergencies(tmp_path):
    out = cards.build(SCENARIOS, CONFIG, None, 2, tmp_path)
    p1, p2 = out["packets"]["tester-1"], out["packets"]["tester-2"]
    assert len(p1) == len(p2) == 15
    assert "Card 01" in p1 and "Card 02" in p2       # emergencies split across testers


def test_lint_flags_leaks_and_duplicates():
    s = copy.deepcopy(SCENARIOS)
    s[1]["situation"] = "You need to be transferred to the owner."
    s[2]["caller"]["name"] = "Ana Lopez"                 # same last name as E01
    w = cards.lint(s, CONFIG)
    assert any("E02" in x and "transfer" in x for x in w)
    assert any("lopez" in x for x in w)
    clean = cards.lint(SCENARIOS, CONFIG)            # shipped scenarios: only the silent call
    assert len(clean) == 1 and clean[0].startswith("O07")


# ---------- matching ----------

def test_match_by_name_street_and_phone():
    m = match.match(gas_call_good(), SCENARIOS)
    assert m["scenario"] is None or m["scenario"] == "E01"
    call = gas_call_good()
    call["transcript_object"].insert(1, utt("user", "this is Maria Lopes", 2.0))  # misheard
    m = match.match(call, SCENARIOS)
    assert m["scenario"] == "E01"
    assert "last name Lopez" in m["evidence"]


def test_unmatched_call_is_not_guessed():
    call = {"transcript_object": [utt("user", "hi I have a question about my bill", 1.0)]}
    m = match.match(call, SCENARIOS)
    assert m["scenario"] is None


# ---------- cold calls ----------

def test_cold_call_scored_without_answer_key(tmp_path):
    r = scoring.score_cold(quote_call_bad(), CONFIG)
    assert r.scenario_id.startswith("COLD-")
    assert r.get("forbidden").result == "fail"
    assert r.get("action").result == "n/a"
    path = tmp_path / "s.csv"
    sheet.upsert(path, r.row())
    t = sheet.tally(sheet.load(path))
    assert t["calls_scored"] == 0                       # cold calls don't count toward the 30
    assert t["cold_calls"]["calls"] == 1 and t["cold_calls"]["clean"] == 0


# ---------- the whole score_new_calls flow, Retell mocked ----------

def test_score_new_calls_end_to_end(tmp_path, monkeypatch):
    good = gas_call_good()
    good["transcript_object"].insert(1, utt("user", "this is Maria Lopez", 2.0))
    stranger = {"call_id": "call_x", "start_timestamp": 1792600000000,
                "transcript_object": [utt("user", "is this the pizza place", 1.0)]}
    calls = {"call_good": good, "call_x": stranger}
    cfg = copy.deepcopy(CONFIG)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(server, "CONFIG", tmp_path / "config.yaml")
    monkeypatch.setattr(server, "SCENARIOS", ROOT / "scenarios.yaml")
    monkeypatch.setattr(server, "SHEET", tmp_path / "scores.csv")
    monkeypatch.setattr(server, "list_ended_phone_calls",
                        lambda since, agent: [{"call_id": c} for c in calls])
    monkeypatch.setattr(server, "fetch_call", lambda cid: calls[cid])

    out = server.score_new_calls("today")
    assert [s["scenario"] for s in out["scored"]] == ["E01"]
    assert out["unmatched"][0]["call_id"] == "call_x"
    assert out["unmatched"][0]["caller_said"] == ["is this the pizza place"]
    assert out["transfers_needing_ring_time"] == ["call_good"]

    again = server.score_new_calls("today")             # already scored: skipped
    assert again["already_scored"] == 1 and again["scored"] == []

    server.score_call("call_good", "E01", ring_seconds=11)   # add ring time
    t = server.scoresheet()
    assert t["transfers_missing_ring_time"] == []
    assert t["checks"]["emergencies"]["value"] == "1/1"


def test_rerun_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG", ROOT / "config.yaml")
    monkeypatch.setattr(server, "SCENARIOS", ROOT / "scenarios.yaml")
    monkeypatch.setattr(server, "SHEET", tmp_path / "scores.csv")
    monkeypatch.setattr(server, "CARDS", tmp_path / "cards")
    bad = scoring.score_call(quote_call_bad(), SCEN["Q01"], CONFIG)
    sheet.upsert(tmp_path / "scores.csv", bad.row())
    out = server.tester_cards(rerun=True)
    assert out["packets"] == {"tester-1": ["Card 15"]}
