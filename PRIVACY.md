# Privacy Policy

Last updated: October 22, 2026

Retell QA is an open-source Claude Code plugin by Faye Hall. It runs entirely on your own computer. The author doesn't operate a server or service for it, and receives no data from it.

## What the plugin reads

- **From your Retell account**, using the API key you provide: the list of ended phone calls to your agent, and each call's transcript, phone numbers, tool calls, timing and post-call analysis. Transcripts can contain personal data, such as the caller's name, phone number and address. Usually these are test details from tester cards, but calls from real customers to the same agent are read too.
- **From your QA folder:** `config.yaml` and `scenarios.yaml`.

## What the plugin stores

Everything is stored on your computer only:

- **Your Retell API key**, in the system keychain, through Claude Code.
- **`scores.csv`** in your QA folder: one row per scored call, with the scenario, call ID, agent version, pass/fail results, timing figures and any notes you add. It doesn't store transcripts, caller names, phone numbers or addresses.
- **Tester cards and the card index** in your QA folder, made from your `scenarios.yaml`.

You can delete any of these files at any time. Uninstalling the plugin removes its code. Your QA folder and its files stay until you delete them.

## What the plugin sends, and to whom

- **Retell** (`https://api.retellai.com`): requests to list and read calls, authenticated with your API key. The plugin only reads from your Retell account; it never changes anything there. Retell's own privacy policy covers its handling of your data.
- **PyPI**, through uv, on first start: downloads of the exact package versions pinned in `server/uv.lock`. No personal data is sent.
- **Claude:** the results the plugin's tools return, such as scores, check details and the opening lines of unmatched calls, go into your Claude Code conversation. Anthropic's privacy policy covers that conversation.

Nothing is sent to the author or to any other party. The plugin has no analytics or tracking.

## Data retention

The author retains no data, because none is received. Data on your computer stays until you delete it.

## Children

The plugin is a business tool and isn't intended for anyone under 18.

## Changes and contact

Changes to this policy are published in this file in the plugin's repository. For questions, open an issue at https://github.com/fayehall-ai/retell-qa/issues.
