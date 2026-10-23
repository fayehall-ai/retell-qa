"""Match a call to its scenario from what the caller said.

Testers use the card's name, phone number and address, so those words show up
in the caller's side of the transcript. Each scenario is scored on how many of
its identifiers appear; the match must be clearly ahead of the runner-up, or
the call is returned as unmatched for the owner to assign. It never guesses.
"""
from __future__ import annotations

import difflib
import re

from .cards import given, street_words
from .scoring import digits_in, spoken_text

MIN_SCORE = 4      # e.g. last name + phone ending, or last name + street
MIN_MARGIN = 3     # best must beat the runner-up by this much


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _heard(word: str, tokens: list[str], cutoff: float = 0.8) -> bool:
    """Exact or near match, so 'Lopes' still counts for Lopez."""
    w = word.lower()
    return w in tokens or bool(difflib.get_close_matches(w, tokens, n=1, cutoff=cutoff))


def signals(call: dict, scenario: dict) -> tuple[int, list[str]]:
    caller_text = " ".join(spoken_text(call, "user"))
    tokens = _tokens(caller_text)
    digits = digits_in(caller_text)
    c = scenario.get("caller") or {}
    score, why = 0, []

    name = c.get("name").split() if given(c.get("name")) else []
    if name and len(name[-1]) > 2 and _heard(name[-1], tokens):
        score += 3
        why.append(f"last name {name[-1]}")
    if len(name) > 1 and len(name[0]) > 2 and _heard(name[0], tokens):
        score += 1
        why.append(f"first name {name[0]}")

    street = [w for w in street_words(c.get("address", "")) if len(w) > 2]
    hit = [w for w in street if _heard(w, tokens)]
    if hit:
        score += min(2 * len(hit), 4)
        why.append(f"street {' '.join(hit)}")

    phone = re.sub(r"\D", "", c.get("phone", ""))[-10:]
    if len(phone) == 10 and phone in digits:
        score += 4
        why.append("full phone number")
    elif len(phone) >= 4 and phone[-4:] in digits:
        score += 3
        why.append(f"phone ending {phone[-4:]}")
    return score, why


def match(call: dict, scenarios: list[dict]) -> dict:
    ranked = sorted(((*signals(call, s), s["id"]) for s in scenarios), reverse=True)
    best = ranked[0] if ranked else (0, [], None)
    runner = ranked[1][0] if len(ranked) > 1 else 0
    candidates = [{"scenario": sid, "score": sc, "evidence": why}
                  for sc, why, sid in ranked[:3] if sc > 0]
    if best[0] >= MIN_SCORE and best[0] - runner >= MIN_MARGIN:
        return {"scenario": best[2], "evidence": best[1], "candidates": candidates}
    return {"scenario": None, "evidence": [], "candidates": candidates}
