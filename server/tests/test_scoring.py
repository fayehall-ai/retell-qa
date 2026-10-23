from pathlib import Path

import yaml

from retell_qa import scoring, sheet

ROOT = Path(__file__).resolve().parents[2] / "examples" / "demo-hvac"
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())
SCEN = {s["id"]: s for s in yaml.safe_load((ROOT / "scenarios.yaml").read_text())["scenarios"]}
CONFIG["actions"]["transfer_to_owner"]["tool"] = "transfer_call"
CONFIG["actions"]["transfer_to_owner"]["args"] = ["+17025551000"]


def words(text, start, step=0.3):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + step - 0.05, 2)})
        t += step
    return out


def utt(role, text, start):
    return {"role": role, "content": text, "words": words(text, start)}


def gas_call_good():
    return {
        "call_id": "call_good", "agent_version": 3,
        "transcript_object": [
            utt("agent", "Thanks for calling, how can I help", 0.0),
            utt("user", "I smell gas in my kitchen in Henderson on Desert Rose", 3.0),
            utt("agent", "Please leave the house now and call Southwest Gas from outside", 7.0),
            utt("user", "my number is 702 555 1234", 11.5),
            utt("agent", "I have seven oh two five five five one two three four", 14.0),
            utt("agent", "Connecting you to the owner now", 18.0),
            utt("agent", "I have 1420 Desert Rose Lane in Henderson, is that right?", 21.0),
        ],
        "transcript_with_tool_calls": [
            {"role": "tool_call_invocation", "tool_call_id": "t1", "name": "transfer_call",
             "type": "transfer_call", "arguments": '{"number": "+17025551000"}'},
            {"role": "tool_call_result", "tool_call_id": "t1", "successful": True, "content": "ok"},
        ],
        "latency": {"e2e": {"p50": 820, "max": 1400}},
        "call_analysis": {"custom_analysis_data": {"call_type": "emergency"}},
        "disconnection_reason": "call_transfer",
    }


def quote_call_bad():
    return {
        "call_id": "call_bad", "agent_version": 3,
        "transcript_object": [
            utt("agent", "Hi how can I help", 0.0),
            utt("user", "how much is a new water heater installed in Anderson off Horizon Ridge", 2.0),
            # agent starts at 3.5s while the caller is still talking until ~6.3s
            utt("agent", "A new water heater is usually around $1,800 installed", 3.5),
            utt("user", "my number is 702 555 4401", 9.0),
            utt("agent", "Got it seven oh two five five five four four zero one", 11.0),
        ],
        "transcript_with_tool_calls": [],
        "latency": {"e2e": {"p50": 1350, "max": 2900}},
        "call_analysis": {"custom_analysis_data": {"call_type": "quote_request"}},
        "disconnection_reason": "user_hangup",
    }


def test_digit_words():
    assert scoring.digits_in("seven oh two, uh, five five five") == "702555"
    assert scoring.digits_in("Oh, sure, I can help") == ""
    assert scoring.digits_in("seven zero two five five five zero one twenty three") == "7025550123"
    assert scoring.digits_in("fourteen twenty Desert Rose") == "1420"


def test_good_emergency_call_passes_everything_but_ring_review():
    s = scoring.score_call(gas_call_good(), SCEN["E01"], CONFIG)
    results = {c.name: c.result for c in s.checks}
    assert results["call_type"] == "pass"
    assert results["action"] == "pass"
    assert results["forbidden"] == "pass"
    assert results["emergency"] == "pass"
    assert results["phone_readback"] == "pass"
    assert results["address_readback"] == "pass"
    assert results["interruptions"] == "pass"
    assert results["latency"] == "pass"
    assert results["transfer"] == "review"          # ring time not reported by Retell
    s2 = scoring.score_call(gas_call_good(), SCEN["E01"], CONFIG, ring_seconds=14)
    assert s2.get("transfer").result == "pass"


def test_bad_quote_call_fails_with_causes():
    s = scoring.score_call(quote_call_bad(), SCEN["Q01"], CONFIG)
    c = {x.name: x for x in s.checks}
    assert c["forbidden"].result == "fail" and "[price]" in c["forbidden"].detail
    assert c["action"].result == "pass"             # no transfer on a price question: right
    assert c["interruptions"].result == "fail" and s.interruptions == 1
    assert c["latency"].result == "fail" and c["latency"].likely_cause == "turn_settings"
    # caller said ...4401 but scenario number is ...4410: STT/caller issue, not readback
    assert c["phone_readback"].result == "fail"
    assert c["phone_readback"].likely_cause == "speech_to_text"
    assert c["heard_names"].result == "fail"        # Henderson heard as Anderson


def test_transfer_number_read_from_transfer_destination():
    call = gas_call_good()
    call["transcript_with_tool_calls"][0]["arguments"] = '{"execution_message": "Connecting you now."}'
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("action").result == "fail"
    call["transfer_destination"] = "+17025551000"
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("action").result == "pass"


def test_allowed_price_is_not_forbidden():
    call = gas_call_good()
    call["transcript_object"].append(utt("agent", "There is an $89 service call fee", 20.0))
    s = scoring.score_call(call, SCEN["E01"], CONFIG)
    assert s.get("forbidden").result == "pass"


def test_sheet_and_tally(tmp_path):
    path = tmp_path / "scores.csv"
    good = scoring.score_call(gas_call_good(), SCEN["E01"], CONFIG, ring_seconds=12)
    bad = scoring.score_call(quote_call_bad(), SCEN["Q01"], CONFIG)
    sheet.upsert(path, good.row())
    sheet.upsert(path, bad.row("caller was in the car"))
    sheet.upsert(path, good.row())                  # same call_id: replaced, not duplicated
    t = sheet.tally(sheet.load(path))
    assert t["calls_scored"] == 2 and not t["complete"] and not t["passes"]
    assert t["checks"]["forbidden_statements"]["pass"] is False
    assert t["checks"]["talks_over_caller"]["pass"] is True     # 1 interruption <= 2
    assert t["rerun_next"] == ["E01", "Q01"]        # failure + every emergency


def test_transfer_that_never_connects_fails_transfer_and_emergency():
    call = gas_call_good()
    call["transcript_with_tool_calls"] = call["transcript_with_tool_calls"][:1]   # no result
    call["transcript_with_tool_calls"][0]["time_sec"] = 18.0
    call.update(disconnection_reason="user_hangup", duration_ms=40000)
    s = scoring.score_call(call, SCEN["E01"], CONFIG)
    assert s.get("action").result == "pass"            # right decision
    assert s.get("transfer").result == "fail" and "22s after" in s.get("transfer").detail
    assert s.get("emergency").result == "fail"


def test_diagnosis_pattern_stays_within_a_clause():
    call = gas_call_good()
    call["transcript_object"].append(utt(
        "agent", "That sounds like a burning smell, and I'm glad you shut the thermostat off", 20.0))
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("forbidden").result == "pass"
    call["transcript_object"].append(utt("agent", "It sounds like your capacitor went", 25.0))
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("forbidden").result == "fail"


def test_tally_scales_to_scenario_count(tmp_path):
    path = tmp_path / "scores.csv"
    sheet.upsert(path, scoring.score_call(gas_call_good(), SCEN["E01"], CONFIG, ring_seconds=12).row())
    t = sheet.tally(sheet.load(path), total_expected=1, emergencies_expected=1)
    assert t["complete"]
    assert t["checks"]["call_type_and_action"]["pass"] is True     # 1 of 1 meets 90%
    assert t["checks"]["emergencies"]["pass"] is True


def test_safety_advice_beyond_the_gas_line_is_forbidden():
    call = gas_call_good()
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("forbidden").result == "pass"
    call["transcript_object"].append(utt(
        "agent", "Please leave the house now and don't use any lights or switches", 25.0))
    f = scoring.score_call(call, SCEN["E01"], CONFIG).get("forbidden")
    assert f.result == "fail" and "[safety_advice]" in f.detail


def test_wrong_street_read_back_fails():
    call = gas_call_good()
    call["transcript_object"][1] = utt("user", "I smell gas at 1420 Desert Road Lane Henderson", 3.0)
    call["transcript_object"][-1] = utt("agent", "I have 1420 Desert Road, Henderson. Connecting you now.", 21.0)
    c = scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback")
    assert c.result == "fail" and c.likely_cause == "speech_to_text"


def test_address_never_read_back_fails_but_no_address_is_na():
    call = gas_call_good()
    call["transcript_object"].pop()                 # drop the address read-back
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback").result == "fail"
    call["transcript_object"][1] = utt("user", "I smell gas in my kitchen", 3.0)
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback").result == "n/a"


def test_house_number_inside_phone_number_is_not_an_address():
    assert scoring.street_parts("5530 W Sahara Ave, Las Vegas") == ("5530", "sahara")
    call = gas_call_good()
    call["transcript_object"].pop()
    call["transcript_object"][1] = utt("user", "I smell gas in my kitchen", 3.0)
    call["transcript_object"].append(utt("agent", "Calling you back at 702-555-1420", 21.0))
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback").result == "n/a"


def test_readback_followed_by_another_question_fails():
    call = gas_call_good()
    call["transcript_object"][4] = utt(
        "agent", "I have 702-555-1234, is that right? I also need the street address.", 14.0)
    c = scoring.score_call(call, SCEN["E01"], CONFIG).get("phone_readback")
    assert c.result == "fail" and "kept talking" in c.detail
    call["transcript_object"][4] = utt("agent", "I have 702-555-1234. Is that right?", 14.0)
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("phone_readback").result == "pass"


def test_digits_the_caller_never_said_go_to_review():
    call = gas_call_good()
    call["transcript_object"][3] = utt("user", "my number is 555 1234", 11.5)
    c = scoring.score_call(call, SCEN["E01"], CONFIG).get("phone_readback")
    assert c.result == "review" and "fill some in" in c.detail


def test_short_echo_is_not_a_talk_over_but_short_wait_is():
    def call_with(user_text):
        call = gas_call_good()
        call["transcript_object"][2] = utt(
            "agent", "I'm sorry, that sounds scary. Please leave the house now and call Southwest Gas", 7.0)
        call["transcript_object"].insert(3, utt("user", user_text, 7.5, ))
        call["transcript_object"][3]["words"] = words(user_text, 7.5, step=0.25)
        return scoring.score_call(call, SCEN["E01"], CONFIG)
    assert call_with("I'm sorry").interruptions == 0
    assert call_with("wait wait").interruptions == 1


def test_echo_counts_when_retell_splits_the_agent_line():
    call = gas_call_good()
    call["transcript_object"][2:3] = [
        utt("agent", "I'm", 7.0),
        utt("agent", "sorry, that sounds scary. Please leave the house now and call Southwest Gas", 7.2),
    ]
    echo = utt("user", "I'm sorry", 7.5)
    echo["words"] = words("I'm sorry", 7.5, step=0.25)
    call["transcript_object"].insert(4, echo)
    assert scoring.score_call(call, SCEN["E01"], CONFIG).interruptions == 0


def test_wrong_house_number_read_back_fails():
    call = gas_call_good()
    call["transcript_object"][-1] = utt("agent", "I have 1240 Desert Rose Lane in Henderson, is that right?", 21.0)
    c = scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback")
    assert c.result == "fail" and "house number" in c.detail
    call["transcript_object"][-1] = utt("agent", "I have fourteen twenty Desert Rose Lane, is that right?", 21.0)
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback").result == "pass"


def test_phone_number_with_spaces_is_not_a_house_number():
    call = gas_call_good()
    call["transcript_object"].pop()
    call["transcript_object"][1] = utt("user", "I smell gas in my kitchen", 3.0)
    call["transcript_object"][3] = utt("user", "my number is 702 555 1420", 11.5)
    assert scoring.score_call(call, SCEN["E01"], CONFIG).get("address_readback").result == "n/a"
