# TikTok DM Parser & Viewer

A two-part tool for archiving your TikTok direct messages and browsing them in a chat-style interface.

- **`tiktok_dm_parser.py`** — stealth browser scraper that logs into TikTok and saves your full DM history to JSON
- **`tiktok_dm_viewer.html`** — browser-based viewer (open in any modern browser, load the JSON, done)
- **`tiktok_dm_viewer.py`** — alternative desktop GUI (tkinter / customtkinter)

---

## Features (v7.0)

- Per-account output folder — everything for one account lives in `dms_<username>/`
- Stealth login via `sessionid` cookie — no username/password stored
- Manual chromedriver path option for when uc's auto-download is flaky (Chrome 147 ready)
- Login retry loop with optional profile-page verification
- Resume from crash — progress saved after every conversation; just rerun to continue
- Human-like pacing (15-30 conversations/hour) with periodic 3-7 minute breaks
- Inbox count via actual DOM enumeration (the badge was unreliable)
- Avatar capture from inbox list items — fixes the bleed-through bug where late conversations all shared the same avatar
- DM Requests folder support (opt-in via `--include-requests`)
- Story-reaction detection — including TikTok's "Download TikTok app" placeholder
- Optional media download — saves photos and videos to `tiktok_media/`
- Auto-detect your @username from the nav if `--username` isn't supplied
- Per-conversation error handling with traceback log

---

## Requirements

- Python 3.11+
- Google Chrome (parser pinned to Chrome 147 by default)
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

# Linux only — tkinter for the optional desktop viewer
sudo apt-get install python3-tk python3.11-tk
```

### Optional: pin chromedriver manually

If `undetected-chromedriver`'s auto-download fails or grabs the wrong build, edit two constants near the top of `tiktok_dm_parser.py`:

```python
_CHROMEDRIVER_PATH   = r"C:\drivers\chromedriver-win64\chromedriver.exe"
_CHROME_VERSION_MAIN = 147
```

Download a matching driver from <https://googlechromelabs.github.io/chrome-for-testing/>. Leave `_CHROMEDRIVER_PATH` empty to use the auto-downloader.

---

## Getting your session ID

1. Open TikTok in your browser and log in
2. Open DevTools → Application → Cookies → `https://www.tiktok.com`
3. Copy the value of the `sessionid` cookie

> The session ID grants full account access. Treat it like a password.

---

## Usage

### Scrape your DMs

```bash
python tiktok_dm_parser.py --sessionid YOUR_SESSION_ID --username yourhandle
```

| Flag | Default | Description |
|---|---|---|
| `--sessionid` | *(required)* | Your TikTok `sessionid` cookie value |
| `--username` | auto-detected | Your `@handle`. Used for stronger login verification, picks the output folder name (`dms_<username>/`), and is stored in the JSON. If omitted, the script tries to detect it from the nav after login. |
| `--output-dir` | auto | Override the output folder name |
| `--headless` | off | Run Chrome invisibly |
| `--proxy` | none | `host:port` or `user:pass@host:port` |
| `--max-convos` | `0` (all) | Per-folder cap |
| `--chat-scrolls` | `15` | Scroll passes per chat — more = older messages |
| `--include-requests` | off | Also scrape the Message Requests folder |
| `--download-media` | off | Save images/videos found in conversations to `tiktok_media/` |
| `--reset` | off | Wipe the resume state file and start over |

**Examples:**

```bash
# Standard run
python tiktok_dm_parser.py --sessionid abc123 --username yourhandle

# Headless + media download + Message Requests
python tiktok_dm_parser.py --sessionid abc123 --username yourhandle \
    --headless --download-media --include-requests

# Just the first 5 conversations
python tiktok_dm_parser.py --sessionid abc123 --username yourhandle --max-convos 5

# Resume after a crash — same command, it picks up where it left off
python tiktok_dm_parser.py --sessionid abc123 --username yourhandle

# Force a clean run from scratch
python tiktok_dm_parser.py --sessionid abc123 --username yourhandle --reset
```

### Browse your DMs

Open `tiktok_dm_viewer.html` in any modern browser, then click "Load tiktok_dms_full_*.json" and pick the JSON file from your output folder. Avatars and media load locally — keep the JSON next to its `tiktok_avatars/` and `tiktok_media/` folders.

For the desktop tkinter viewer instead:

```bash
python tiktok_dm_viewer.py
```

---

## Output

### Folder layout

```
dms_<username>/
  tiktok_dms_full_YYYY-MM-DD.json   ← final output (load this in the viewer)
  tiktok_dms_state.json             ← resume state
  tiktok_dms_errors.log             ← per-conversation error tracebacks
  tiktok_avatars/                   ← downloaded contact avatars
  tiktok_media/                     ← only if --download-media
```

Without `--username`, the folder is `dms_run_<timestamp>/` until the script auto-detects your handle, after which it switches to `dms_<detected>/`.

### JSON structure

```json
{
  "owner_profile": { "username": "yourhandle" },
  "summary": {
    "inbox": 87, "requests": 12,
    "story_reactions": 34, "media_items": 18
  },
  "conversations": [
    {
      "username":      "some_user",
      "source":        "inbox",
      "is_request":    false,
      "avatar_path":   "tiktok_avatars/some_user.jpg",
      "message_count": 84,
      "media_count":   3,
      "message_type_counts": {"text": 78, "story_reaction": 4, "media": 2},
      "scraped_at":    "2026-04-27T15:42:11",
      "messages": [
        {
          "is_me": false, "sender": "Them",
          "text": "Hey!",
          "timestamp": "10:30 AM",
          "message_type": "text",
          "is_story_reaction": false,
          "media": []
        },
        {
          "is_me": false, "sender": "Them",
          "text": "",
          "timestamp": "10:32 AM",
          "message_type": "story_reaction",
          "is_story_reaction": true,
          "media": []
        },
        {
          "is_me": true, "sender": "Me",
          "text": "haha that's great",
          "timestamp": "10:33 AM",
          "message_type": "text",
          "is_story_reaction": false,
          "media": [
            {"type": "image", "url": "https://...",
             "local_path": "tiktok_media/some_user_image_4_a3f201.jpg"}
          ]
        }
      ]
    }
  ]
}
```

`owner_profile.username` is filled in either from `--username` or from auto-detection.

### Message types

| `message_type` | Meaning |
|---|---|
| `text` | Normal text message |
| `story_reaction` | Reaction or reply to a story (the viewer shows a heart badge instead of the placeholder text) |
| `shared_post` | A shared TikTok video link |
| `media` | Image / video attachment |
| `system` | System message (blocked, can't message, etc.) |

---

## Notes

- **Session expiry** — TikTok sessions typically last weeks. If scraping stops working, get a fresh `sessionid`.
- **Rate limiting** — Hard floor of 15-30 conversations/hour with periodic long breaks. Going faster risks an account ban; the limits aren't a CLI flag for that reason. To change them, edit `_MIN_SECONDS_PER_CONVO` / `_MAX_SECONDS_PER_CONVO` at the top of the file.
- **Login flakiness** — TikTok occasionally rejects the first login; the script retries up to 5 times. With `--username`, it also verifies by loading your profile page.
- **Slow connections** — The parser waits for each chat to stabilize (message count constant for 3 consecutive checks) before extracting, so half-loaded chats aren't saved as truncated.
- **Avatar bleed-through (fixed in v7)** — Avatars are now read from inbox list items *before* clicking, so they can't drift from the previous chat's header.
- **Sender direction** — Detected via class names + `data-e2e` + computed style. If a particular chat still shows everything on one side, the viewer prints a warning banner; inspect that chat's message DOM in your browser and add the missing class to `_detect_is_me()` in the parser.
- **Selectors may drift** — TikTok's web app changes class names occasionally. If Message Requests aren't found or story reactions aren't flagged, inspect the relevant element and update selectors near the top of `open_requests_tab()` / `_classify_message()` / `_detect_is_me()`.
- **Headless on Linux** — Requires a display server or `Xvfb`.
