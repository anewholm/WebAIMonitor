#!/usr/bin/env python3
"""
WebAI WhatsApp Monitor Daemon

Polls WhatsApp Web every 5 seconds for new incoming messages from contacts
discovered via PM.md files in project directories. When a new message arrives,
invokes `claude -p` (non-interactive) to generate a reply and sends it back.

Usage:
    python3 monitor.py

Requires:
    - Playwright Firefox browser (run install.sh first)
    - .browser_profile/ directory with a WhatsApp Web session
    - claude CLI at /usr/local/bin/claude
"""

import asyncio
import glob
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page

# ── Constants ────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "state.json"
BROWSER_PROFILE = SCRIPT_DIR / ".browser_profile"
QR_FLAG_FILE = SCRIPT_DIR / "qr_required.flag"

POLL_INTERVAL = 5       # seconds between polls
HISTORY_COUNT = 10      # number of messages to pass as context to Claude
CLAUDE_BIN = "/usr/local/bin/claude"
WHATSAPP_URL = "https://web.whatsapp.com"

SCAN_PATTERNS = [
    "/var/www/*/PM.md",
    str(Path.home() / "Software/*/PM.md"),
]

# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Contact:
    name: str            # "Jozsef Toth" — as it appears in WhatsApp
    project_dir: Path    # e.g. ~/Software/Hotel
    system_prompt: str   # from PM.md ## Context section
    page: Optional[Page] = field(default=None, repr=False)


@dataclass
class State:
    last_message: dict = field(default_factory=dict)     # name → "time::text" key
    first_reply_done: dict = field(default_factory=dict) # name → bool


# ── PM.md discovery ──────────────────────────────────────────────────────────

def discover_contacts() -> list[Contact]:
    contacts = []
    for pattern in SCAN_PATTERNS:
        for pm_path in glob.glob(pattern):
            contact = parse_pm_file(Path(pm_path))
            if contact:
                contacts.append(contact)
                logging.info(f"Discovered contact: '{contact.name}' in {contact.project_dir}")
    return contacts


def parse_pm_file(pm_path: Path) -> Optional[Contact]:
    try:
        text = pm_path.read_text(encoding="utf-8")
    except OSError as e:
        logging.warning(f"Could not read {pm_path}: {e}")
        return None

    # Find WhatsApp contact name in the Communication table
    m = re.search(r'\|\s*WhatsApp\s*\|\s*([^|]+?)\s*\|', text, re.IGNORECASE)
    if not m:
        return None
    name = m.group(1).strip()

    # Extract ## Context section (everything up to next ## or end of file)
    ctx_match = re.search(r'##\s+Context\s*\n(.*?)(?=\n##|\Z)', text, re.DOTALL)
    system_prompt = ctx_match.group(1).strip() if ctx_match else ""

    return Contact(name=name, project_dir=pm_path.parent, system_prompt=system_prompt)


# ── State persistence ────────────────────────────────────────────────────────

def load_state() -> State:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return State(
                last_message=data.get("last_message", {}),
                first_reply_done=data.get("first_reply_done", {}),
            )
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Could not load state file: {e} — starting fresh")
    return State()


def save_state(state: State):
    try:
        STATE_FILE.write_text(json.dumps({
            "last_message": state.last_message,
            "first_reply_done": state.first_reply_done,
        }, indent=2))
    except OSError as e:
        logging.error(f"Could not save state: {e}")


# ── Browser setup ────────────────────────────────────────────────────────────

async def setup_browser():
    pw = await async_playwright().start()
    if not BROWSER_PROFILE.exists():
        logging.error(
            f"Browser profile not found at {BROWSER_PROFILE}. "
            "Run install.sh first to copy the MCP Firefox profile."
        )
        await pw.stop()
        sys.exit(1)

    context = await pw.firefox.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE),
        headless=True,
    )
    return pw, context


# ── WhatsApp Web navigation ───────────────────────────────────────────────────

async def is_qr_shown(page: Page) -> bool:
    return await page.locator('[data-testid="qrcode"]').count() > 0


async def navigate_to_contact(page: Page, name: str) -> bool:
    try:
        await page.goto(WHATSAPP_URL, wait_until="networkidle", timeout=30_000)
    except Exception as e:
        logging.error(f"Failed to load WhatsApp Web: {e}")
        return False

    if await is_qr_shown(page):
        logging.error(
            "WhatsApp Web is not logged in — QR code is showing. "
            f"Please open {WHATSAPP_URL} in Firefox, scan the QR code, "
            "then restart this daemon."
        )
        QR_FLAG_FILE.write_text("QR scan required\n")
        return False

    # Wait for the chat list to appear (session is active)
    try:
        await page.wait_for_selector('[data-testid="chat-list"]', timeout=20_000)
    except Exception:
        logging.error("Chat list did not appear — WhatsApp Web may not be fully loaded")
        return False

    # Search for the contact
    try:
        search = page.locator('[data-testid="search-bar-input"]')
        await search.click()
        await search.fill(name)
        await page.wait_for_timeout(1_500)  # let search results populate

        # Click the first matching chat cell
        cell = page.locator('[data-testid="cell-frame-container"]').filter(has_text=name).first
        await cell.click(timeout=5_000)

        # Wait for the message list
        await page.wait_for_selector('[data-testid="msg-container"]', timeout=10_000)
        logging.info(f"Opened conversation with '{name}'")
        return True
    except Exception as e:
        logging.error(f"Could not open conversation with '{name}': {e}")
        return False


# ── Message extraction ────────────────────────────────────────────────────────

async def extract_messages(page: Page, count: int = HISTORY_COUNT) -> list[dict]:
    """Return the last `count` messages as [{direction, text, time}]."""
    try:
        await page.wait_for_selector('[data-testid="msg-container"]', timeout=5_000)
    except Exception:
        return []

    messages = await page.evaluate("""
        (count) => {
            const rows = Array.from(document.querySelectorAll(
                '.message-in, .message-out'
            ));
            const last = rows.slice(-count);
            return last.map(row => {
                const isIncoming = row.classList.contains('message-in');
                const textEl = row.querySelector('span.selectable-text');
                const timeEl = row.querySelector('[data-testid="msg-meta"] span')
                             || row.querySelector('span[class*="time"]');
                return {
                    direction: isIncoming ? 'in' : 'out',
                    text: textEl ? textEl.innerText.trim() : '',
                    time: timeEl ? timeEl.innerText.trim() : '',
                };
            }).filter(m => m.text !== '');
        }
    """, count)

    return messages


def get_last_incoming(messages: list[dict]) -> Optional[dict]:
    for msg in reversed(messages):
        if msg["direction"] == "in":
            return msg
    return None


def message_key(msg: dict) -> str:
    """Stable deduplication key — combines time and text."""
    return f"{msg.get('time', '')}::{msg.get('text', '')}"


# ── Claude invocation ─────────────────────────────────────────────────────────

def build_prompt(contact: Contact, messages: list[dict], new_message: str, is_first: bool) -> str:
    history_lines = []
    for msg in messages:
        if message_key(msg) == message_key({"time": msg["time"], "text": new_message}):
            continue  # skip the new message itself from history
        speaker = contact.name if msg["direction"] == "in" else "You (developer)"
        history_lines.append(f"{speaker}: {msg['text']}")
    history = "\n".join(history_lines) if history_lines else "(no prior messages)"

    intro = (
        "IMPORTANT: This is your first reply to this contact. "
        "Start by briefly introducing yourself: explain you are Claude (an AI assistant), "
        "that the developer has set you up to respond to WhatsApp messages, "
        "and that you are happy to help.\n\n"
    ) if is_first else ""

    return f"""{intro}You are responding to a WhatsApp message on behalf of the developer.

PROJECT CONTEXT:
{contact.system_prompt}

RECENT CONVERSATION (last {HISTORY_COUNT} messages):
{history}

NEW MESSAGE FROM {contact.name.upper()}:
{new_message}

Reply naturally and concisely. Output only the reply text — it will be sent directly into WhatsApp."""


def invoke_claude_sync(contact: Contact, messages: list[dict], new_message: str, is_first: bool) -> Optional[str]:
    prompt = build_prompt(contact, messages, new_message, is_first)
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", "--no-session-persistence", "--allowedTools", "Read,Glob"],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(contact.project_dir),
            timeout=60,
        )
        if result.returncode != 0:
            logging.error(f"Claude exited {result.returncode}: {result.stderr[:300]}")
            return None
        return result.stdout.strip() or None
    except subprocess.TimeoutExpired:
        logging.error("Claude invocation timed out after 60s")
        return None
    except FileNotFoundError:
        logging.error(f"Claude CLI not found at {CLAUDE_BIN}")
        return None


async def invoke_claude(contact: Contact, messages: list[dict], new_message: str, is_first: bool) -> Optional[str]:
    return await asyncio.to_thread(invoke_claude_sync, contact, messages, new_message, is_first)


# ── Sending a reply ───────────────────────────────────────────────────────────

async def send_reply(page: Page, text: str) -> bool:
    try:
        input_box = page.locator('[data-testid="conversation-compose-box-input"]')
        await input_box.click()
        await input_box.fill(text)
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        return True
    except Exception as e:
        logging.error(f"Failed to send reply: {e}")
        return False


# ── Poll loop ─────────────────────────────────────────────────────────────────

async def poll_contact(contact: Contact, state: State):
    if contact.page is None:
        return

    try:
        messages = await extract_messages(contact.page)
    except Exception as e:
        logging.warning(f"[{contact.name}] Error extracting messages: {e}")
        return

    last_in = get_last_incoming(messages)
    if last_in is None:
        return

    key = message_key(last_in)
    if key == state.last_message.get(contact.name):
        return  # no new message

    logging.info(f"[{contact.name}] New message: {last_in['text'][:80]}")

    is_first = not state.first_reply_done.get(contact.name, False)
    reply = await invoke_claude(contact, messages, last_in["text"], is_first)

    if reply:
        sent = await send_reply(contact.page, reply)
        if sent:
            logging.info(f"[{contact.name}] Replied ({len(reply)} chars)")
            state.first_reply_done[contact.name] = True
        else:
            logging.warning(f"[{contact.name}] Reply generated but send failed — will retry")
            return  # don't update state; retry next poll
    else:
        logging.warning(f"[{contact.name}] Claude returned no reply")

    # Update state regardless of send result (if Claude failed, avoid retry loop)
    state.last_message[contact.name] = key
    save_state(state)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(SCRIPT_DIR / "monitor.log"),
        ],
    )

    contacts = discover_contacts()
    if not contacts:
        logging.error(
            "No contacts discovered. Add a PM.md with a WhatsApp row to a project under "
            "/var/www/ or ~/Software/ and restart."
        )
        sys.exit(1)

    logging.info(f"Found {len(contacts)} contact(s): {[c.name for c in contacts]}")
    state = load_state()

    pw, context = await setup_browser()

    # Close the default blank page Playwright opens
    for p in context.pages:
        await p.close()

    # Open one tab per contact
    for contact in contacts:
        page = await context.new_page()
        ok = await navigate_to_contact(page, contact.name)
        if ok:
            contact.page = page
        else:
            await page.close()

    active = [c for c in contacts if c.page is not None]
    if not active:
        logging.error("No contact tabs opened successfully. Check logs above.")
        await context.close()
        await pw.stop()
        sys.exit(1)

    logging.info(f"Monitoring {len(active)} contact(s). Polling every {POLL_INTERVAL}s...")

    try:
        while True:
            for contact in active:
                await poll_contact(contact, state)
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Shutting down.")
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
