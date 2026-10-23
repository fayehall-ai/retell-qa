"""Tester cards: what a caller needs to play a scenario, and nothing about the answer.

A card is built ONLY from the scenario's tester-facing fields (caller,
situation, play). The answer key (expect, emergency, title, id) never reaches
it. lint() also warns when tester-facing text leaks the answer in words.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

PLAY = {
    "fast": "Talk fast, like you're in a hurry.",
    "interrupt": "Interrupt the agent at least once while it's mid-sentence.",
    "pieces": "Give your phone number in pieces, with pauses and an \"uh\": {pieces}.",
    "car": "Call from a car with the window down, or somewhere with background noise.",
    "mumble": "Say your street address once quickly or mumbled, as people do.",
    "spanish": "Speak Spanish for the whole call. (Give this card to a tester who speaks Spanish.)",
    "accent": "Speak naturally in your own accent. (Give this card to a tester with a strong accent.)",
}

NONE = "none"


def given(value) -> bool:
    """A caller field the tester actually says (not blank, not 'none')."""
    return bool(value) and str(value).strip().lower() != NONE

STREET_SUFFIXES = {"ln", "lane", "dr", "drive", "ave", "avenue", "st", "street", "rd",
                   "road", "blvd", "pkwy", "parkway", "way", "ct", "court", "cir",
                   "trl", "trail", "pl", "w", "e", "n", "s"}

INSTRUCTIONS = """\
## How to make a test call

You're testing an AI phone receptionist for {business}. Play a real customer: don't try to trick it, and don't help it.

1. **Read your card first**, then put it where you can glance at it. Use your own words; don't read it aloud.
2. **Call {demo_number} from your own phone.** Not a web or computer call.
3. **Use the card's name, phone number and address exactly.** When it asks for your number, give the card's number, not your own. That's how your call gets matched to the card.
4. **Follow the "How to play it" lines.** If it asks something the card doesn't cover, make up a normal answer.
5. **Don't hint at what you think it should do.** Real customers don't know what it's supposed to do.
6. **If it says it's connecting you to someone,** count the seconds until you hear ringing or a person. If it goes to voicemail, note that and hang up.
7. **Right after you hang up,** write on the card anything that felt off: it talked over you, a long pause, it got your name or street wrong, it said something strange.
8. **Send the owner** the card number, the time you called, and your notes.

**Everything is role-play.** Never actually call 911 or the gas company, even if it tells you to. If it connects you to someone, that's the owner's real phone, so tell the owner when you're testing.

Calls are recorded. Don't use your real name, number or address; use the card's.
"""


def street_words(address: str) -> list[str]:
    if not given(address):
        return []
    street = address.split(",")[0]
    return [w for w in re.findall(r"[A-Za-z]+", street) if w.lower() not in STREET_SUFFIXES]


def pieces(phone: str) -> str:
    d = re.sub(r"\D", "", phone)[-10:]
    if len(d) != 10:
        return phone
    return f"\"{d[:3]}... {d[3:6]}... uh, {d[6:]}\""


def card_number(scenarios: list[dict], sid: str) -> int:
    """Stable card number: the scenario's position in the full file."""
    return next(i for i, s in enumerate(scenarios, 1) if s["id"] == sid)


def card_text(n: int, scenario: dict, config: dict) -> str:
    c = scenario.get("caller") or {}
    show = lambda field, missing: c.get(field) if given(c.get(field)) else missing
    lines = [
        f"# Card {n:02d}",
        "",
        f"**Call:** {config.get('demo_number', '(demo number not set)')}",
        "",
        f"**You are:** {show('name', 'no name given')}",
        f"**Your phone number:** {show('phone', 'none: you will not give one')}",
        f"**Your address:** {show('address', 'none: you will not give one')}",
        "",
        "**What's going on:**",
        " ".join(str(scenario.get("situation", "")).split()),
        "",
    ]
    play = [p for p in scenario.get("play") or [] if p != "pieces" or given(c.get("phone"))]
    if play:
        lines.append("**How to play it:**")
        for p in play:
            lines.append("- " + PLAY.get(p, p).format(pieces=pieces(c.get("phone", ""))))
        lines.append("")
    lines += [
        "**After the call** (circle or write):",
        "1. Time you called: ________",
        "2. Connected you to someone? yes / no. Seconds until it rang: ____",
        "3. Talked over you? yes / no",
        "4. Long pause before it answered you? yes / no",
        "5. Got your name, number or street wrong? ______________________",
        "6. Anything else: ______________________________________________",
    ]
    return "\n".join(lines)


def lint(scenarios: list[dict], config: dict) -> list[str]:
    """Warnings: missing tester fields, answer leaks, names that can't be matched."""
    warnings = []
    leak_words = {"transfer", "transferred", "expected", "the agent will", "the agent should"}
    for s in scenarios:
        expect = s.get("expect") or {}
        leak_words |= {str(expect.get("call_type", "")).replace("_", " ").lower(),
                       str(expect.get("action", "")).replace("_", " ").lower()}
    for name, spec in (config.get("actions") or {}).items():
        if spec and spec.get("tool"):
            leak_words.add(spec["tool"].replace("_", " ").lower())
    leak_words -= {"", "none", "other", "no transfer"}   # too common to signal a leak

    for s in scenarios:
        sid, c = s["id"], s.get("caller") or {}
        for field in ("name", "phone", "address"):
            if not c.get(field):
                warnings.append(f"{sid}: caller.{field} is missing (write \"none\" if the "
                                "caller doesn't give it)")
        if not any(given(c.get(f)) for f in ("name", "phone", "address")):
            warnings.append(f"{sid}: caller gives no name, number or address, so this call "
                            "can't be matched automatically; assign it with score_call")
        if not s.get("situation"):
            warnings.append(f"{sid}: no situation; the card would tell the tester nothing")
        text = " ".join(str(s.get("situation", "")).split()).lower()
        for w in sorted(leak_words):
            if re.search(rf"\b{re.escape(w)}\b", text):
                warnings.append(f"{sid}: situation contains \"{w}\", which may hint at the "
                                "expected answer; describe what happened, not what to do")
        for p in s.get("play") or []:
            if p not in PLAY:
                warnings.append(f"{sid}: unknown play \"{p}\" (printed as written)")

    def dupes(key):
        counts = Counter(key(s) for s in scenarios if key(s))
        return [k for k, n in counts.items() if n > 1]

    def last_name(s):
        name = (s.get("caller") or {}).get("name")
        return name.split()[-1].lower() if given(name) else ""

    for name in dupes(last_name):
        warnings.append(f"last name \"{name}\" is used by more than one scenario; "
                        "calls can't be matched by name")
    for street in dupes(lambda s: " ".join(street_words((s.get("caller") or {}).get("address", ""))).lower()):
        warnings.append(f"street \"{street}\" is used by more than one scenario; "
                        "calls can't be matched by address")
    for phone in dupes(lambda s: re.sub(r"\D", "", (s.get("caller") or {}).get("phone", ""))[-4:]):
        warnings.append(f"phone ending {phone} is used by more than one scenario")
    return warnings


def build(scenarios: list[dict], config: dict, ids: list[str] | None,
          testers: int, out_dir: Path) -> dict:
    by_id = {s["id"]: s for s in scenarios}
    ids = ids or [s["id"] for s in scenarios]
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(f"Unknown scenario ids: {unknown}")
    testers = max(1, min(testers, len(ids)))
    out_dir.mkdir(parents=True, exist_ok=True)

    packets = {t: [] for t in range(1, testers + 1)}
    for i, sid in enumerate(ids):          # round-robin spreads emergencies across testers
        packets[i % testers + 1].append(sid)

    instructions = INSTRUCTIONS.format(
        business=config.get("business_name", "the business"),
        demo_number=config.get("demo_number", "(demo number not set)"))
    files, index_rows = [], []
    for t, sids in packets.items():
        cards = [card_text(card_number(scenarios, sid), by_id[sid], config) for sid in sids]
        md = instructions + "\n\n---\n\n" + "\n\n---\n\n".join(cards) + "\n"
        md_path = out_dir / f"tester-{t}.md"
        md_path.write_text(md)
        files.append(str(md_path))
        pdf = _pdf(instructions, cards, out_dir / f"tester-{t}.pdf")
        if pdf:
            files.append(pdf)
        for sid in sids:
            s = by_id[sid]
            e = s.get("expect") or {}
            index_rows.append((card_number(scenarios, sid), t, sid, s.get("title", ""),
                               (s.get("caller") or {}).get("name", ""),
                               e.get("call_type", ""), e.get("action", ""),
                               "; ".join(m.replace("|", " / ") for m in e.get("must_say") or [])))

    index_rows.sort()
    index = ["# Card index and answer key (owner only: do not share)", "",
             "| Card | Tester | Scenario | Title | Caller | Expected type | Expected action | Must say |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    index += [f"| {n:02d} | {t} | {sid} | {title} | {name} | {ct} | {act} | {ms} |"
              for n, t, sid, title, name, ct, act, ms in index_rows]
    index_path = out_dir / "index.md"
    index_path.write_text("\n".join(index) + "\n")

    return {
        "files": files,
        "owner_index": str(index_path),
        "packets": {f"tester-{t}": [f"Card {card_number(scenarios, s):02d}" for s in sids]
                    for t, sids in packets.items()},
        "warnings": lint([by_id[i] for i in ids], config),
        "pdf": any(f.endswith(".pdf") for f in files),
    }


def _pdf(instructions: str, cards: list[str], path: Path) -> str | None:
    """One page of instructions, then one page per card. Skipped if reportlab is missing."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return None
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontSize, body.leading = 12, 17

    def inline(s: str) -> str:
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    def flow(md: str) -> list:
        out = []
        for line in md.splitlines():
            if line.startswith("# "):
                out.append(Paragraph(inline(line[2:]), styles["Title"]))
            elif line.startswith("## "):
                out.append(Paragraph(inline(line[3:]), styles["Heading1"]))
            elif line.startswith("- "):
                out.append(Paragraph(inline(line[2:]), body, bulletText="•"))
            elif re.match(r"\d+\. ", line):
                num, rest = line.split(" ", 1)
                out.append(Paragraph(inline(rest), body, bulletText=num))
            elif line.strip():
                out.append(Paragraph(inline(line), body))
            else:
                out.append(Spacer(1, 8))
        return out

    story = flow(instructions)
    for card in cards:
        story += [PageBreak()] + flow(card)
    SimpleDocTemplate(str(path), pagesize=letter, leftMargin=60, rightMargin=60,
                      topMargin=54, bottomMargin=54).build(story)
    return str(path)
