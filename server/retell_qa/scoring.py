"""Deterministic scoring of one Retell call against one scenario.

Every verdict here comes from code. Nothing calls an LLM. Anything the code
cannot measure reliably is reported as "unknown" or "review", never as a pass.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
AMBIGUOUS_ZERO = {"oh", "o"}  # only digits when next to other digits
TEENS = {"ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
         "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19"}
TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6",
        "seventy": "7", "eighty": "8", "ninety": "9"}


@dataclass
class Check:
    name: str
    result: str              # pass | fail | review | unknown | n/a
    detail: str = ""
    likely_cause: str = ""   # prompt | turn_settings | speech_to_text | transfer_config


@dataclass
class CallScore:
    call_id: str
    scenario_id: str
    agent_version: str | None
    checks: list[Check] = field(default_factory=list)
    e2e_p50_ms: float | None = None
    e2e_max_ms: float | None = None
    interruptions: int = 0

    def get(self, name: str) -> Check:
        return next(c for c in self.checks if c.name == name)

    def row(self, notes: str = "") -> dict:
        """One scoresheet row: the protocol's columns first, then the extras."""
        yn = lambda r: {"pass": "y", "fail": "n"}.get(r, r)
        forbidden = self.get("forbidden").result
        causes = sorted({c.likely_cause for c in self.checks
                         if c.result in ("fail", "review") and c.likely_cause})
        return {
            "scenario": self.scenario_id,
            "call_type_right": yn(self.get("call_type").result),
            "action_right": yn(self.get("action").result),
            "anything_forbidden": {"pass": "n", "fail": "y"}.get(forbidden, forbidden),
            "interrupted": "y" if self.interruptions else "n",
            "felt_slow": {"pass": "n", "fail": "y"}.get(self.get("latency").result,
                                                      self.get("latency").result),
            "notes": notes,
            "call_id": self.call_id,
            "agent_version": self.agent_version or "",
            "emergency": self.get("emergency").result,
            "readback": self.get("phone_readback").result,
            "address_readback": self.get("address_readback").result,
            "transfer": self.get("transfer").result,
            "interruption_count": self.interruptions,
            "e2e_p50_ms": self.e2e_p50_ms if self.e2e_p50_ms is not None else "",
            "e2e_max_ms": self.e2e_max_ms if self.e2e_max_ms is not None else "",
            "likely_cause": ";".join(causes),
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [asdict(c) for c in self.checks]
        return d


# ---------- helpers ----------

def utterances(call: dict, role: str) -> list[dict]:
    return [u for u in call.get("transcript_object") or [] if u.get("role") == role]


def spoken_text(call: dict, role: str) -> list[str]:
    return [u.get("content", "") for u in utterances(call, role)]


def digits_in(text: str) -> str:
    """All digits in spoken order, including 'seven oh two' and 'twenty three' style words."""
    tokens = re.findall(r"[a-zA-Z]+|\d", text.lower())
    out = []
    for i, tok in enumerate(tokens):
        if tok.isdigit():
            out.append(tok)
        elif tok in TEENS:
            out.append(TEENS[tok])
        elif tok in TENS:
            next_ = tokens[i + 1] if i + 1 < len(tokens) else ""
            unit = next_ in DIGIT_WORDS and next_ not in AMBIGUOUS_ZERO and next_ != "zero"
            out.append(TENS[tok] + ("" if unit else "0"))
        elif tok in DIGIT_WORDS:
            if tok in AMBIGUOUS_ZERO:
                prev_ = tokens[i - 1] if i else ""
                next_ = tokens[i + 1] if i + 1 < len(tokens) else ""
                near = lambda t: t.isdigit() or (t in DIGIT_WORDS and t not in AMBIGUOUS_ZERO)
                if not (near(prev_) or near(next_)):
                    continue
            out.append(DIGIT_WORDS[tok])
    return "".join(out)


STREET_SUFFIXES = {"st", "street", "ave", "avenue", "rd", "road", "dr", "drive", "ln", "lane",
                   "ct", "court", "way", "blvd", "boulevard", "pkwy", "parkway", "pl", "place",
                   "cir", "circle", "ter", "terrace", "trl", "trail", "loop"}
DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}


def street_parts(address: str) -> tuple[str, str]:
    """'1420 Desert Rose Ln, Henderson' -> ('1420', 'desert rose')."""
    first = address.split(",")[0].lower().replace(".", "")
    m = re.match(r"\s*(\d+)\s+(.*)", first)
    if not m:
        return "", ""
    words = m.group(2).split()
    if words and words[0] in DIRECTIONS:
        words = words[1:]
    if words and words[-1] in STREET_SUFFIXES:
        words = words[:-1]
    return m.group(1), " ".join(words)


def without_phone_numbers(text: str) -> str:
    """Drop digit runs of 7+ digits, however they're spaced ('702 555 1420',
    '702-555-1420'), so a phone number's last digits aren't taken for a house number."""
    return re.sub(r"\d[\d\s.()-]*\d",
                  lambda m: " " if len(re.sub(r"\D", "", m.group())) >= 7 else m.group(), text)


def house_number_in(text: str, number: str) -> bool:
    """The house number as digits ('1420') or words ('fourteen twenty'), outside phone numbers."""
    text = without_phone_numbers(text)
    return bool(re.search(rf"(?<![\d-]){number}(?![\d-])", text)
                or number in digits_in(re.sub(r"\d+", " ", text)))


def stacked_after_readback(line: str, is_readback) -> bool:
    """True when a turn reads something back, asks about it, then keeps going
    ('...1234, is that right? I also need your address.'), so the caller never
    gets to confirm."""
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", line.strip()) if s]
    start = next((i for i, s in enumerate(sentences) if is_readback(s)), None)
    if start is None:
        return False
    ask = next((i for i in range(start, len(sentences)) if sentences[i].endswith("?")), None)
    return ask is not None and any(re.search(r"[a-zA-Z]", s) for s in sentences[ask + 1:])


def tool_calls(call: dict) -> list[dict]:
    """Tool invocations from transcript_with_tool_calls, with their results."""
    events = call.get("transcript_with_tool_calls") or []
    results = {e.get("tool_call_id"): e for e in events if e.get("role") == "tool_call_result"}
    calls = []
    for e in events:
        if e.get("role") == "tool_call_invocation":
            res = results.get(e.get("tool_call_id"), {})
            calls.append({
                "name": e.get("name"),
                "type": e.get("type"),
                "arguments": e.get("arguments") or "",
                "successful": res.get("successful"),
                "time_sec": e.get("time_sec"),
            })
    return calls


def transfer_connected(call: dict) -> bool:
    """Retell sets transfer_destination (and ends the call this way) only when a transfer connects."""
    return bool(call.get("transfer_destination")) or \
        call.get("disconnection_reason") in ("call_transfer", "transfer_bridged")


# ---------- checks ----------

def check_call_type(call, scenario, config) -> Check:
    want = scenario["expect"].get("call_type")
    data = (call.get("call_analysis") or {}).get("custom_analysis_data") or {}
    got = data.get(config.get("call_type_field", "call_type"))
    if got is None:
        return Check("call_type", "unknown",
                     f"post-call field '{config.get('call_type_field')}' missing; "
                     "add it to the agent's post-call analysis", "")
    ok = str(got).strip().lower() == str(want).strip().lower()
    return Check("call_type", "pass" if ok else "fail",
                 f"expected {want}, got {got}", "" if ok else "prompt")


def check_action(call, scenario, config) -> Check:
    action = scenario["expect"].get("action", "none")
    spec = (config.get("actions") or {}).get(action)
    if spec is None:
        return Check("action", "unknown", f"action '{action}' not in config.yaml actions")
    calls = tool_calls(call)
    tool = spec.get("tool")
    if tool is None:
        # Ending the call is not an action; anything else (a transfer) is.
        acted = [c for c in calls if "end_call" not in (c["name"], c["type"])]
        ok = not acted
        return Check("action", "pass" if ok else "fail",
                     "expected no transfer or other tool call" +
                     ("" if ok else f", got {[c['name'] for c in acted]}"),
                     "" if ok else "prompt")
    matches = [c for c in calls if tool in (c["name"], c["type"])]
    if not matches:
        return Check("action", "fail",
                     f"expected tool {tool}; agent called {[c['name'] for c in calls] or 'nothing'}",
                     "prompt")
    # Retell's built-in transfer keeps the number in the tool definition, not the
    # arguments; the call records where it went in transfer_destination, but only
    # if it connected. An unconnected transfer is still the right decision; the
    # transfer check fails it.
    if "transfer" in tool and not transfer_connected(call):
        return Check("action", "pass", f"{tool} called (transfer never connected; see transfer)")
    seen = [c["arguments"] + " " + (call.get("transfer_destination") or "") for c in matches]
    for needle in spec.get("args") or []:
        if not any(needle in s for s in seen):
            return Check("action", "fail",
                         f"{tool} went to {call.get('transfer_destination') or '?'}, "
                         f"not '{needle}': {matches[0]['arguments']}",
                         "transfer_config" if "transfer" in tool else "prompt")
    return Check("action", "pass", f"{tool} called")


def check_forbidden(call, scenario, config) -> Check:
    allowed = [a.lower() for a in
               (config.get("allowed_phrases") or config.get("allowed_prices") or [])]
    hits = []
    for line in spoken_text(call, "agent"):
        scrubbed = line.lower()
        for a in allowed:
            scrubbed = scrubbed.replace(a, "")
        for kind, patterns in (config.get("forbidden") or {}).items():
            if any(re.search(p, scrubbed) for p in patterns):
                hits.append(f"[{kind}] {line.strip()}")
    if hits:
        return Check("forbidden", "fail",
                     "confirm in transcript: " + " | ".join(hits), "prompt")
    return Check("forbidden", "pass", "no forbidden patterns in agent speech")


def check_emergency(call, scenario, config, action: Check,
                    transfer: Check | None = None) -> Check:
    if not scenario.get("emergency"):
        return Check("emergency", "n/a")
    problems = []
    if action.result != "pass":
        problems.append(f"action: {action.detail}")
    elif transfer is not None and transfer.result == "fail":
        problems.append(f"transfer: {transfer.detail}")
        action = transfer
    agent = " ".join(spoken_text(call, "agent")).lower()
    must = scenario["expect"].get("must_say") or []
    missing = [p for p in must if not re.search(p, agent)]
    if missing:
        problems.append(f"never said any of: {missing}")
    if problems:
        cause = action.likely_cause if action.result != "pass" else "prompt"
        return Check("emergency", "fail", "; ".join(problems), cause)
    return Check("emergency", "pass", "handled: right action and required safety lines")


def check_readback(call, scenario, config) -> Check:
    want = digits_in(scenario["caller"].get("phone", ""))[-10:]
    if len(want) < 7:
        return Check("phone_readback", "n/a", "scenario has no phone number")
    agent_lines = [(l, digits_in(l)) for l in spoken_text(call, "agent")]
    user_digits = digits_in(" ".join(spoken_text(call, "user")))
    heard = want in user_digits
    read = [l for l, d in agent_lines if want in d]
    if read:
        if not heard:
            return Check("phone_readback", "review",
                         f"agent read back {want}, but the transcript only has the caller "
                         f"saying '{user_digits or 'no digits'}'. Listen: did the caller say "
                         "every digit, or did the agent fill some in?", "prompt")
        stacked = [l.strip() for l in read
                   if stacked_after_readback(l, lambda s: len(digits_in(s)) >= 7)]
        if stacked:
            return Check("phone_readback", "fail",
                         "read the number back, then kept talking before the caller could "
                         f"confirm: {stacked}", "prompt")
        return Check("phone_readback", "pass", f"agent read back {want}")
    wrong = [l.strip() for l, d in agent_lines if len(d) >= 7]
    if wrong:
        cause = "prompt" if heard else "speech_to_text"
        why = ("transcript heard the number right, agent read it back wrong"
               if heard else "speech-to-text never captured the full number")
        return Check("phone_readback", "fail", f"{why}: {wrong}", cause)
    return Check("phone_readback", "fail",
                 "agent never read the number back" +
                 ("" if heard else " (and speech-to-text never captured it)"),
                 "prompt" if heard else "speech_to_text")


def check_address_readback(call, scenario, config) -> Check:
    number, street = street_parts(scenario["caller"].get("address") or "")
    if not street:
        return Check("address_readback", "n/a", "scenario has no street address")
    # An address is mentioned when the house number (on its own, not inside a
    # phone number) or the street's first word comes up.
    first = street.split()[0]
    has_number = lambda l: house_number_in(l, number)
    mentions = lambda l: bool(has_number(l) or re.search(rf"\b{re.escape(first)}\b", l.lower()))
    caller = " ".join(spoken_text(call, "user")).lower()
    gave = mentions(caller)
    agent = [l for l in spoken_text(call, "agent") if mentions(l)]
    right = [l for l in agent if street in l.lower() and has_number(l)]
    if right:
        stacked = [l.strip() for l in right
                   if stacked_after_readback(l, lambda s: street in s.lower())]
        if stacked:
            return Check("address_readback", "fail",
                         "read the address back, then kept talking before the caller could "
                         f"confirm: {stacked}", "prompt")
        return Check("address_readback", "pass", f"agent read back '{number} {street}'")
    if agent:
        street_ok = any(street in l.lower() for l in agent)
        part, said_right = (("house number", has_number(caller)) if street_ok
                            else ("street", street in caller))
        why = (f"transcript heard the {part} right, agent read it back wrong" if said_right
               else f"speech-to-text never wrote the {part} ('{number if street_ok else street}'), "
                    f"and the agent read back the wrong {part}")
        return Check("address_readback", "fail", f"{why}: {[l.strip() for l in agent]}",
                     "prompt" if said_right else "speech_to_text")
    if gave:
        return Check("address_readback", "fail",
                     "caller gave a street address; agent never read it back", "prompt")
    return Check("address_readback", "n/a", "caller never gave a street address")


def count_interruptions(call, config) -> tuple[int, list[str]]:
    th = config.get("thresholds") or {}
    cut_in = th.get("cut_in_min_overlap_s", 0.25)
    no_yield = th.get("no_yield_overlap_s", 1.0)
    echo_max = th.get("echo_max_speech_s", 0.7)
    users = utterances(call, "user")
    agents = utterances(call, "agent")
    user_words = [w for u in users for w in u.get("words") or []]
    agent_words = [w for a in agents for w in a.get("words") or []]
    events = []
    for a in agents:
        words = a.get("words") or []
        if not words:
            continue
        a_start, a_end = words[0]["start"], words[-1]["end"]
        # A: agent starts talking while the caller keeps going
        overlap = [w for w in user_words if w["start"] <= a_start < w["end"]
                   or a_start <= w["start"] < a_start + cut_in]
        if overlap and max(w["end"] for w in overlap) - a_start >= cut_in:
            events.append(f"cut in at {a_start:.1f}s: '{a.get('content', '')[:60]}'")
            continue
        # B: caller barges in, agent keeps talking over them
        barge = [w for w in user_words if a_start < w["start"] < a_end]
        # A short burst that only repeats the agent's own words ("I'm sorry" over
        # "I'm sorry...") is the caller echoing it, not trying to take the turn.
        # Retell can split one agent sentence across utterances, so compare with
        # everything the agent said in the 3s before the overlap.
        said = {t for w in agent_words if barge and barge[0]["start"] - 3 <= w["start"] <= barge[-1]["end"]
                for t in re.findall(r"[a-z']+", w["word"].lower())}
        echo = (sum(w["end"] - w["start"] for w in barge) < echo_max
                and all(set(re.findall(r"[a-z']+", w["word"].lower())) <= said for w in barge))
        if barge and not echo and a_end - barge[0]["start"] >= no_yield:
            events.append(f"did not yield at {barge[0]['start']:.1f}s "
                          f"(kept talking {a_end - barge[0]['start']:.1f}s)")
    return len(events), events


def check_latency(call, config) -> tuple[Check, float | None, float | None]:
    th = config.get("thresholds") or {}
    e2e = (call.get("latency") or {}).get("e2e") or {}
    p50, mx = e2e.get("p50"), e2e.get("max")
    if p50 is None:
        return Check("latency", "unknown", "Retell returned no e2e latency for this call"), None, None
    slow = p50 > th.get("slow_e2e_p50_ms", 1000) or (mx or 0) > th.get("slow_e2e_max_ms", 2500)
    detail = f"e2e p50 {p50:.0f} ms, max {mx:.0f} ms" if mx is not None else f"e2e p50 {p50:.0f} ms"
    return Check("latency", "fail" if slow else "pass", detail,
                 "turn_settings" if slow else ""), p50, mx


def check_transfer(call, scenario, config, ring_seconds: float | None) -> Check:
    transfers = [c for c in tool_calls(call) if "transfer" in f"{c['name']}{c['type']}"]
    expected = "transfer" in (config["actions"].get(scenario["expect"].get("action", ""), {}).get("tool") or "")
    if not expected and not transfers:
        return Check("transfer", "n/a")
    if not transfers:
        return Check("transfer", "fail", "transfer expected but never attempted", "prompt")
    reason = call.get("disconnection_reason")
    failed = [c for c in transfers if c["successful"] is False]
    if not failed and reason != "transfer_cancelled" and not transfer_connected(call):
        started = transfers[0].get("time_sec")
        ended = (call.get("duration_ms") or 0) / 1000
        after = f" {ended - started:.0f}s after it started" if started is not None and ended else ""
        return Check("transfer", "fail",
                     f"transfer never connected: call ended ({reason}){after}. If the caller "
                     "was waiting on ringing, check the number and your cell's voicemail "
                     "timing; if they hung up early, re-run it",
                     "transfer_config")
    if failed or reason == "transfer_cancelled":
        return Check("transfer", "fail",
                     f"transfer attempted but failed (disconnection_reason={reason}); "
                     "check +1 number format and your cell's voicemail timing",
                     "transfer_config")
    limit = (config.get("thresholds") or {}).get("transfer_ring_max_s", 25)
    if ring_seconds is None:
        return Check("transfer", "review",
                     f"transfer connected (disconnection_reason={reason}); Retell does not "
                     f"report ring time, so enter it: did it ring within {limit}s?")
    ok = ring_seconds <= limit
    return Check("transfer", "pass" if ok else "fail",
                 f"rang in {ring_seconds:.0f}s (limit {limit}s)",
                 "" if ok else "transfer_config")


def check_listen_for(call, scenario) -> Check:
    words = scenario.get("listen_for") or []
    if not words:
        return Check("heard_names", "n/a")
    heard = " ".join(spoken_text(call, "user")).lower()
    missing = [w for w in words if w.lower() not in heard]
    if missing:
        return Check("heard_names", "fail",
                     f"speech-to-text never wrote {missing}; check the transcript for "
                     "near-misses (Henderson → Anderson) and add them as expected words",
                     "speech_to_text")
    return Check("heard_names", "pass", f"heard {words}")


def score_cold(call: dict, config: dict, ring_seconds: float | None = None) -> CallScore:
    """A call with no card (the cold calls at the end). No answer key, so only the
    checks that don't need one: forbidden statements, talk-overs, gap, transfer."""
    cid = call.get("call_id", "")
    score = CallScore(cid, f"COLD-{cid[-6:]}", call.get("agent_version"))
    pseudo = {"id": score.scenario_id, "caller": {}, "expect": {"action": "none"}}
    latency, p50, mx = check_latency(call, config)
    n, events = count_interruptions(call, config)
    na = "cold call: no answer key"
    score.checks = [
        Check("call_type", "n/a", na),
        Check("action", "n/a", na),
        check_forbidden(call, pseudo, config),
        Check("emergency", "n/a", na),
        Check("phone_readback", "n/a", na),
        Check("address_readback", "n/a", na),
        Check("interruptions", "pass" if n == 0 else "fail",
              "; ".join(events) or "agent never talked over the caller",
              "turn_settings" if n else ""),
        latency,
        check_transfer(call, pseudo, config, ring_seconds),
        Check("heard_names", "n/a", na),
    ]
    score.e2e_p50_ms, score.e2e_max_ms, score.interruptions = p50, mx, n
    return score


def score_call(call: dict, scenario: dict, config: dict,
               ring_seconds: float | None = None) -> CallScore:
    score = CallScore(call.get("call_id", ""), scenario["id"], call.get("agent_version"))
    action = check_action(call, scenario, config)
    transfer = check_transfer(call, scenario, config, ring_seconds)
    latency, p50, mx = check_latency(call, config)
    n, events = count_interruptions(call, config)
    score.checks = [
        check_call_type(call, scenario, config),
        action,
        check_forbidden(call, scenario, config),
        check_emergency(call, scenario, config, action, transfer),
        check_readback(call, scenario, config),
        check_address_readback(call, scenario, config),
        Check("interruptions", "pass" if n == 0 else "fail",
              "; ".join(events) or "agent never talked over the caller",
              "turn_settings" if n else ""),
        latency,
        transfer,
        check_listen_for(call, scenario),
    ]
    score.e2e_p50_ms, score.e2e_max_ms, score.interruptions = p50, mx, n
    return score
