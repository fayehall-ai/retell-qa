---
name: retell-qa
description: Run a QA test round on a Retell voice agent with real phone calls, using the retell-qa MCP tools. Use when the user wants to make tester cards, score test calls, check the scoresheet or pass bar, add a transfer's ring time, score a cold call, decide what to fix or re-run, or asks whether a voice agent is ready to ship.
argument-hint: "What stage? e.g. 'cards for 5 testers', 'score today', 'where are we'"
---

# Retell QA round

The retell-qa MCP server does the measuring. Verdicts come from its code, never from you. Your job is to run the round in order, keep the answer key away from testers, and turn failures into one fix at a time.

A round goes: **preflight → cards → testers call → score → resolve leftovers → fix → re-run → ship check**. If the user's request names a stage, start there, but run preflight first in a new session.

## 1. Preflight (before cards or scoring)

The server reads `config.yaml` and `scenarios.yaml` from the QA folder: the plugin's **QA folder** setting (`${user_config.qa_dir}`), or the folder Claude Code was started in when that's empty. If either file is missing, offer to start from the examples in `${CLAUDE_PLUGIN_ROOT}/examples/demo-hvac/`, copied into the QA folder, and walk the user through each field.

Read both files and check:

- `demo_number` is the **agent's Retell line**, the number testers call. `actions.transfer_to_owner.args` holds the **owner's cell**, the transfer target. They must be different numbers. If they match, or you aren't sure which is which, stop and ask; do not guess.
- Neither still holds an example placeholder (`555-0100`, `+15555550199`).
- `agent_id` is set. If it's empty, every phone call on the account is pulled in, including real customers.
- `actions.*.tool` matches the agent's real tool names in Retell.
- `scenarios.yaml` has at least one scenario with `emergency: true`, and each caller's name, phone ending and street is unique. 30 scenarios with 6 emergencies is the recommended size; fewer is fine, but mention it if there are under 20 or no emergencies. The pass bar scales to whatever the file holds.

Report anything wrong before going further. Don't edit `expect:` answer keys yourself: the user approves what counts as correct.

## 2. Tester cards

Call `tester_cards` with the number of testers. Then:

- Tell the user where the packets are (`cards/tester-N.pdf` in the QA folder) and that `cards/index.md` is **owner-only**.
- Pass on every warning the tool returns (answer leaks in the wording, names or streets that are hard to match). Suggest a rewording for each leak.
- **Never** paste card contents, the index, scenario IDs, expected actions or which calls are emergencies into anything meant for a tester: a message, email, doc or chat. If the user asks you to send packets, send only the tester's own PDF.

Testers call the demo number from their own phones, using the card's name, number and address exactly.

## 3. Score

Call `score_new_calls` with the right `since` (`today`, `yesterday`, `3h`, or an ISO time). Report:

- Calls scored, and for each one that didn't pass: the check, the detail and the likely cause.
- **Unmatched calls**: show what the caller said and the closest candidates, then ask the user which scenario it was (or `cold`, or ignore it as a real customer). **Never assign one yourself**, even when one candidate looks obvious. Assign with `score_call` once they answer.
- Transfers needing a ring time.

## 4. Resolve leftovers

- **Ring time**: Retell doesn't report it. Ask the user or tester how many seconds until the phone rang, then call `score_call` again with `ring_seconds`. Rescoring replaces that call's row.
- **`review` and `unknown` are not passes.** Ask the user to check the quoted line in the transcript or recording. Forbidden-statement hits are pattern matches and talk-overs use approximate timestamps, so a borderline one may be a false alarm. If it is, the fix is the pattern in `config.yaml` (with the user's OK), not ignoring the check.

## 5. Fix and re-run

Call `scoresheet`. Show each check with its value and pass/fail, and whether every scenario is scored.

Group failures by likely cause and recommend **one** fix, the one that fixes the most calls:

| Likely cause | Where the fix goes |
|---|---|
| `prompt` | Agent prompt: wrong words, missing rule, conflicting rules |
| `turn_settings` | Retell end-of-turn and interruption settings, not the prompt |
| `speech_to_text` | Retell boosted keywords: the misheard street, town or name |
| `transfer_config` | Transfer number format (+1…) and the owner cell's voicemail timing |

Changing several things at once hides which one worked. After the fix, call `tester_cards` with `rerun: true` (failures plus every emergency) and start the next round.

## 6. Ship check

Say the agent is ready only when **all** of these are true:

1. `scoresheet` shows `complete: true` and `passes: true`, from one full run of every scenario on the **final** agent version. Earlier rounds don't count once the agent has changed.
2. No transfers are missing a ring time, and nothing is left at `review`.
3. Two cold calls (unscripted, no card) were made and scored with `scenario_id: "cold"`, and both are clean.

If any is missing, list exactly what's left. Passing most checks is not passing.
