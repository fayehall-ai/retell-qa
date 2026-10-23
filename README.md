# Retell QA

A Claude Code plugin for testing a [Retell](https://www.retellai.com) voice agent with real phone calls. It makes cards for human testers, matches each call to its card, scores it the same way every time against a fixed pass bar, and says which kind of fix each failure needs.

All verdicts come from code; nothing is judged by an LLM. Anything the code can't measure is marked `review` or `unknown`, never passed.

This is an independent project. It isn't made by, affiliated with or endorsed by Retell AI.

The plugin has two parts:

- **An MCP server** with four tools: `tester_cards`, `score_new_calls`, `score_call` and `scoresheet`.
- **A `retell-qa` skill** that runs a test round in order: preflight checks, cards, scoring, leftovers, one fix at a time, re-runs, and a strict ship check.

## Requirements

- [Claude Code](https://code.claude.com)
- [uv](https://docs.astral.sh/uv/) (the server's Python dependencies install automatically on first run)
- A Retell account, API key and agent

## Install

```sh
claude plugin marketplace add fayehall-ai/retell-qa
claude plugin install retell-qa@retell-qa
```

Claude Code asks for your **Retell API key** (stored in the system keychain) and an optional **QA folder**. Change either later with `/config`.

## Set up an agent to test

1. **Make a QA folder** for the agent, for example `clients/acme-hvac/`, and set it as the plugin's QA folder. If you leave the setting empty, the folder you start Claude Code in is used.
2. **Copy the examples** from `examples/demo-hvac/` into it, or ask Claude: "set up retell-qa for my agent".
3. **Edit `config.yaml`:** business name, the agent's Retell number (`demo_number`), agent ID, your agent's tool names, the owner's cell on `transfer_to_owner`, and anything the agent is allowed to say that would otherwise look forbidden.
4. **Write `scenarios.yaml`:** 30 scenarios with 6 marked `emergency: true` is a good size, but any count works; the pass bar scales to the file. Each needs a unique caller name, phone ending and street, and a `situation` in the caller's words.
5. **In Retell**, add a post-call analysis field named `call_type`. Its values must match `expect.call_type` in your scenarios.

`config.yaml`, `scenarios.yaml`, `scores.csv` and `cards/` hold your agent's details and test results. Keep them out of public repositories.

## A test round

1. **"Make tester cards for 5 testers."** Send each tester their own packet. Keep `cards/index.md` to yourself.
2. **Testers call** the agent's number, using the card's name, number and address.
3. **"Score today's calls."** Unmatched calls are shown to you to assign; the tool never guesses.
4. **Add ring times:** "call_abc rang in 12 seconds."
5. **"Where are we on the scoresheet?"** Fix one thing, then **"make re-run cards"** and repeat.
6. **Before shipping:** one full run of every scenario on the final version, plus two unscripted cold calls.

| Likely cause | Fix |
|---|---|
| `prompt` | Wrong words, missing rule or conflicting rules: fix the prompt |
| `turn_settings` | Talked over the caller or slow gap: end-of-turn and interruption settings |
| `speech_to_text` | Misheard a number or street: add boosted keywords |
| `transfer_config` | Transfer failed: number format (+1…) and the owner cell's voicemail timing |

## Limits

- **Matching needs the card's details.** If a tester makes up a different name and number, the call comes back unmatched.
- **Every ended phone call to the agent since the given time is considered.** Real customer calls show up as unmatched; ignore them.
- **Ring time** isn't reported by Retell; you or the tester enter it.
- **Forbidden statements** are pattern matches. Every hit quotes the line to confirm; tune the patterns to your business.
- **Talk-overs** use Retell's approximate word timestamps. Check borderline cases on the recording.
- **One QA folder at a time.** To switch agents, change the QA folder setting or start Claude Code in that agent's folder.

## What it runs, sends and stores

- **Runs:** a local Python MCP server, started with `uv run --frozen` from the plugin's `server/` folder. On first start, uv installs the exact package versions pinned in `server/uv.lock` from PyPI into `server/.venv` inside the installed plugin.
- **Sends:** requests only to the Retell API (`https://api.retellai.com`), authenticated with your Retell API key, to list your agent's ended phone calls and read their transcripts and tool calls. It never changes anything in your Retell account. Nothing is sent anywhere else.
- **Stores:** your API key in the system keychain, through Claude Code. `scores.csv`, tester cards (`cards/`) and the owner-only card index are written to your QA folder on your own computer.
- **Reads:** `config.yaml` and `scenarios.yaml` in your QA folder.

## Privacy

See the [Privacy Policy](PRIVACY.md). In short: the plugin runs on your computer, reads calls from your Retell account, and sends nothing to the author or anyone else.

## Develop

```sh
cd server
uv run --group dev pytest -q
```

## License

MIT
