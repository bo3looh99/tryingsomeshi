# TikTok DM Parser & Viewer

A two-part tool for archiving your TikTok direct messages and browsing them in a modern desktop GUI.

- **`tiktok_dm_parser.py`** — stealth browser scraper that logs into TikTok, extracts your full DM history, and saves it to JSON
- **`tiktok_dm_viewer.py`** — desktop GUI to browse conversations and read messages with a chat-style bubble interface

---

## Features

### Parser (v6.1)
- Stealth login via `sessionid` cookie — no username/password stored
- Undetected Chrome driver — no bot fingerprint, no automation flags
- **Manual chromedriver path** for when uc's auto-download is flaky (Chrome 147 ready)
- **Login retry loop** — up to 5 attempts, optionally verifies your profile URL
- **Resume from crash** — progress saved after every conversation; just rerun to continue
- **Human-like pacing** — randomized delays, jittered scrolls, periodic 3-7 min breaks
- **Rate-limited** — 15-30 conversations per hour to avoid bans
- **Inbox count + ETA** — reads the conversation count from TikTok's tab badge and gives you a finish-time estimate
- **DM Requests folder** — optionally scrape Message Requests, tagged separately in output
- **Story reactions flagged** — messages classified as `text` / `story_reaction` / `shared_post` / `media` / `system` so you can tell real messages from "X reacted to your story"
- Slow-connection safe — waits for chats to fully stabilize before extracting
- Per-conversation error handling with traceback log
- Optional proxy support
- Downloads contact avatars into `tiktok_avatars/`

### Viewer
- Auto-loads the latest JSON file on startup
- Searchable conversation list with circular contact avatars
- Chat-style bubble thread — blue right-aligned bubbles for you, dark left-aligned for them
- Contact avatar shown inline next to each received message
- `File > Open` to load any JSON file

---

## Requirements

- Python 3.11+
- Google Chrome installed (the parser is currently pinned to Chrome 147)
- Linux / macOS / Windows

---

## Installation

```bash
git clone <repo>
cd tiktok

python -m venv myenv
source myenv/bin/activate        # Linux / macOS
# myenv\Scripts\activate         # Windows

pip install selenium undetected-chromedriver requests customtkinter Pillow

# Linux only — tkinter system package
sudo apt-get install python3-tk python3.11-tk
```

### Optional: pin chromedriver manually

If `undetected-chromedriver`'s auto-download fails or grabs the wrong build, edit the two constants near the top of `tiktok_dm_parser.py`:

```python
_CHROMEDRIVER_PATH   = r"C:\drivers\chromedriver-win64\chromedriver.exe"
_CHROME_VERSION_MAIN = 147
```

Download a matching driver from <https://googlechromelabs.github.io/chrome-for-testing/>. Leave `_CHROMEDRIVER_PATH` empty to use the auto-downloader.

---

## Getting Your Session ID

1. Open TikTok in your browser and log in
2. Open DevTools → Application → Cookies → `https://www.tiktok.com`
3. Copy the value of the `sessionid` cookie

> The session ID grants full account access. Treat it like a password — never share it.

---

## Usage

### Scrape your DMs

```bash
python tiktok_dm_parser.py --sessionid YOUR_SESSION_ID
```

**All options:**

| Flag | Default | Description |
|---|---|---|
| `--sessionid` | *(required)* | Your TikTok `sessionid` cookie value |
| `--username` | none | Your `@handle` (no `@` needed). Used for stronger login verification by visiting `https://www.tiktok.com/@<username>`, and stored in the output |
| `--headless` | off | Run Chrome invisibly in the background |
| `--proxy` | none | Proxy server — `host:port` or `user:pass@host:port` |
| `--max-convos` | `0` (all) | Per-folder cap on conversations |
| `--chat-scrolls` | `15` | Scroll passes per chat — more = older messages |
| `--include-requests` | off | Also scrape the Message Requests folder |
| `--reset` | off | Wipe the resume state file and start over |

**Examples:**

```bash
# Scrape all conversations, headless, with manual username verification
python tiktok_dm_parser.py --sessionid abc123 --username myhandle --headless

# Include Message Requests as well
python tiktok_dm_parser.py --sessionid abc123 --include-requests

# Only the first 5 conversations of the inbox
python tiktok_dm_parser.py --sessionid abc123 --max-convos 5

# Resume after a crash — just rerun the same command
python tiktok_dm_parser.py --sessionid abc123

# Force a clean run from scratch
python tiktok_dm_parser.py --sessionid abc123 --reset
```

### Resume after a crash

The parser saves progress to `tiktok_dms_state.json` after **every** conversation. If your network drops, the script crashes, or you hit `Ctrl+C`, just rerun the same command — already-completed conversations are skipped and it picks up where it left off. Use `--reset` to wipe state and start fresh.

### Browse your DMs

```bash
python tiktok_dm_viewer.py
```

The viewer auto-loads the most recently created `tiktok_dms_full_*.json` file in the current directory. Use **File > Open** to load a different file.

---

## Output

### Files written

```
tiktok_dms_full_YYYY-MM-DD.json   ← final output (consumed by viewer)
tiktok_dms_state.json             ← resume state (delete or use --reset)
tiktok_dms_errors.log             ← per-conversation error tracebacks
tiktok_avatars/                   ← downloaded contact avatars
```

If a final-output file for today already exists, a counter suffix is added (`_2`, `_3`, …).

### Structure

```json
{
  "owner_profile": {
    "username": "myhandle"
  },
  "summary": {
    "inbox": 87,
    "requests": 12,
    "story_reactions": 34
  },
  "conversations": [
    {
      "username":      "some_user",
      "source":        "inbox",
      "is_request":    false,
      "avatar_url":    "https://...",
      "avatar_path":   "tiktok_avatars/some_user.jpg",
      "message_count": 84,
      "message_type_counts": {"text": 78, "story_reaction": 4, "media": 2},
      "scraped_at":    "2026-04-27T15:42:11",
      "messages": [
        {
          "is_me":             false,
          "sender":            "Them",
          "text":              "Hey!",
          "timestamp":         "10:30 AM",
          "message_type":      "text",
          "is_story_reaction": false
        },
        {
          "is_me":             false,
          "sender":            "Them",
          "text":              "Reacted ❤ to your story",
          "timestamp":         "10:32 AM",
          "message_type":      "story_reaction",
          "is_story_reaction": true
        }
      ]
    },
    {
      "username":   "stranger_42",
      "source":     "requests",
      "is_request": true,
      "messages":   [...],
      "...":        "..."
    }
  ]
}
```

`owner_profile` is empty unless you pass `--username`. The parser no longer auto-scrapes profile metadata — that step was unreliable and slowed everything down.

### Message types

Each message is classified into one of:

| Type | Meaning |
|---|---|
| `text` | Normal text message |
| `story_reaction` | Reaction or reply to a story (not a real message) |
| `shared_post` | A shared TikTok video link |
| `media` | Image / video attachment |
| `system` | System message (blocked, can't message, etc.) |

---

## Notes

- **Session expiry** — TikTok sessions typically last weeks. If scraping stops working, get a fresh `sessionid`.
- **Rate limiting** — The parser enforces 15-30 conversations per hour with periodic long breaks. Going faster risks an account ban; the limits aren't a CLI flag for that reason. If you must change them, edit `_MIN_SECONDS_PER_CONVO` / `_MAX_SECONDS_PER_CONVO` at the top of the file.
- **Login flakiness** — TikTok occasionally rejects the first login attempt; the script retries up to 5 times. If you pass `--username`, login is also verified by loading your profile page, which is more reliable than DOM heuristics.
- **Slow connections** — The parser waits for each chat to *stabilize* (message count stops changing for 3 consecutive checks) before extracting, so a half-loaded chat won't be saved as truncated.
- **Headless on Linux** — Requires a display server or virtual framebuffer (`Xvfb`) if running on a headless server. The viewer always requires a desktop environment.
- **Viewer on Linux** — Requires `python3-tk` and the matching `python3.X-tk` system package (e.g. `python3.11-tk`).
- **Selectors may drift** — TikTok's web app changes class names occasionally. If the Message Requests tab isn't detected or story reactions aren't flagged, inspect the relevant DOM element in your browser — selectors live near the top of `open_requests_tab()` and `_classify_message()`.
