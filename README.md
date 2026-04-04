# WebAI Monitor — AI-driven project management via WhatsApp

A Python daemon that monitors WhatsApp Web for client messages and replies using the Claude AI CLI. Each software project registers a `PM.md` file containing the client's WhatsApp contact name and a system prompt describing the project context. When a new message arrives from that contact, Claude generates a reply and sends it back automatically.

> **Architecture note:** This tool was developed using architecture-led AI development — a systems architect defined the design, data flow, and interfaces, then directed Claude to write the implementation. The design decisions drive the code, not the other way around.

## What it does

1. Scans `/var/www/*/PM.md` and `~/Software/*/PM.md` for registered contacts.
2. Opens WhatsApp Web via Playwright using a saved browser session.
3. Polls each contact's chat every 5 seconds for new incoming messages.
4. Passes the last 10 messages as context to `claude -p` (non-interactive) with the project system prompt.
5. Sends Claude's reply back to the contact via WhatsApp Web.

## Prerequisites

- Python 3.10+
- Claude CLI (`claude`) installed at `/usr/local/bin/claude` (available via `npm install -g @anthropic-ai/claude-code`)
- Firefox with Playwright support

## Installation

```bash
bash install.sh
pip install -r requirements.txt
playwright install firefox
```

## Setup

1. Start WhatsApp Web in the Playwright browser profile and scan the QR code once:
   ```bash
   python3 monitor.py
   ```
   If no saved session is found, a `qr_required.flag` file is created and the browser opens for you to scan.

2. Create a `PM.md` file in each project directory you want to monitor:
   ```markdown
   ## Contact
   WhatsApp name: John Smith

   ## Context
   This is the Hotel booking app. The client asks about feature status,
   bugs, and delivery dates. Respond professionally and concisely.
   ```

3. Run the monitor as a cron job or background process:
   ```bash
   python3 monitor.py
   ```

## PM.md format

| Section | Required | Description |
|---------|----------|-------------|
| `## Contact` / `WhatsApp name:` | Yes | Exact name as shown in WhatsApp |
| `## Context` | Yes | System prompt passed to Claude for this project |

## State

Message history is stored in `state.json` in the script directory. Delete it to reset the seen-message cache.

## Known limitations

- WhatsApp Web only — no WhatsApp Business API.
- Requires a persistent browser session; the QR code must be re-scanned if the session expires.
- Claude CLI must be authenticated (`claude auth`) before use.
- Scans only `PM.md` files in `/var/www/*/` and `~/Software/*/` — subdirectories are not traversed.
- Polling interval is 5 seconds per contact. For large contact lists this can be slow.

## Important notes

**WhatsApp Terms of Service:** Automated messaging via WhatsApp Web may violate WhatsApp's Terms of Service. This tool is intended for personal/internal project management use where all parties are aware of and consent to AI-assisted replies. It is not intended for bulk messaging or spam. Use responsibly.

## License

MIT
