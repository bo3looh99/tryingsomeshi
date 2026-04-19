# TikTok DM Parser & Viewer

A two-part tool for archiving your TikTok direct messages and browsing them in a modern desktop GUI.

- **`tiktok_dm_parser.py`** — headless browser scraper that logs into TikTok, extracts your full DM history, and saves it to JSON
- **`tiktok_dm_viewer.py`** — desktop GUI to browse conversations and read messages with a chat-style bubble interface

---

## Features

### Parser
- Stealth login via `sessionid` cookie — no username/password stored
- Undetected Chrome driver — no bot fingerprint, no automation flags
- Optional proxy support
- Extracts owner account profile from page source + internal APIs:
  - `@username`, display name, bio
  - Followers, following, video count
  - Account region and store region (e.g. `AE`)
  - Linked phone (masked) and email (masked)
  - Account creation date, app store platform
  - User ID (`uid`) and secure user ID (`secUid`)
  - Privacy status (public / private)
- Scrapes every conversation one by one, scrolling up to load full history
- Downloads contact avatars into `tiktok_avatars/`
- Saves everything to a timestamped JSON file

### Viewer
- Auto-loads the latest JSON file on startup
- Owner profile card with avatar at the top of the sidebar
- Searchable conversation list with circular contact avatars
- Chat-style bubble thread — blue right-aligned bubbles for you, dark left-aligned for them
- Contact avatar shown inline next to each received message
- `File > Open` to load any JSON file

---

## Requirements

- Python 3.11+
- Google Chrome installed
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
| `--headless` | off | Run Chrome invisibly in the background |
| `--proxy` | none | Proxy server — `host:port` or `user:pass@host:port` |
| `--max-convos` | `0` (all) | Limit number of conversations to scrape |
| `--chat-scrolls` | `15` | Scroll passes per chat — more = older messages |
| `--skip-profile` | off | Skip the account profile scrape |
| `--debug` | off | Dump raw page source and API responses to files |

**Examples:**

```bash
# Scrape all conversations, headless, through a proxy
python tiktok_dm_parser.py --sessionid abc123 --headless --proxy 1.2.3.4:8080

# Only the first 5 conversations, visible browser window
python tiktok_dm_parser.py --sessionid abc123 --max-convos 5

# Re-scrape DMs only (skip slow profile step)
python tiktok_dm_parser.py --sessionid abc123 --skip-profile

# Debug mode — dumps page source if profile extraction fails
python tiktok_dm_parser.py --sessionid abc123 --debug
```

### Browse your DMs

```bash
python tiktok_dm_viewer.py
```

The viewer auto-loads the most recently created `tiktok_dms_full_*.json` file in the current directory. Use **File > Open** to load a different file.

---

## Output

### File

```
tiktok_dms_full_YYYY-MM-DD.json
```

If a file for today already exists, a counter suffix is added (`_2`, `_3`, …).

### Structure

```json
{
  "owner_profile": {
    "username": "h_la7soooni",
    "nickname": "H_la7soni",
    "uid": "6572191588984963077",
    "sec_uid": "MS4wLjABAAAA...",
    "region": "AE",
    "store_region": "AE",
    "is_private": true,
    "followers_count": "1200",
    "following_count": "340",
    "video_count": "42",
    "phone_masked": "+971*****89",
    "email_masked": "h***@gmail.com",
    "app_store": "iOS App Store",
    "account_created": "2018-03-15",
    "avatar_url": "https://...",
    "avatar_path": "tiktok_avatars/owner_h_la7soooni.jpg",
    "scraped_at": "2026-04-19T14:30:00"
  },
  "conversations": [
    {
      "username": "some_user",
      "avatar_url": "https://...",
      "avatar_path": "tiktok_avatars/some_user.jpg",
      "message_count": 84,
      "messages": [
        {
          "is_me": false,
          "sender": "Them",
          "text": "Hey!",
          "timestamp": "10:30 AM"
        },
        {
          "is_me": true,
          "sender": "Me",
          "text": "Hey, what's up?",
          "timestamp": "10:31 AM"
        }
      ]
    }
  ]
}
```

### Avatars

Downloaded to `tiktok_avatars/` as JPEG files:

```
tiktok_avatars/
  owner_h_la7soooni.jpg
  some_user.jpg
  another_user.jpg
```

---

## Notes

- **Session expiry** — TikTok sessions typically last weeks. If scraping stops working, get a fresh `sessionid`.
- **Rate limiting** — The parser uses randomised delays between actions to avoid detection. Do not reduce `--chat-scrolls` below `5`.
- **Phone / email** — These are only available if TikTok exposes them in your account settings API. They appear masked (e.g. `+1****5678`).
- **Account creation date** — TikTok sets this to `0` for many accounts. If it appears as `null` in the output, TikTok is not exposing it.
- **Headless on Linux** — Requires a display server or virtual framebuffer (`Xvfb`) if running on a headless server. The viewer always requires a desktop environment.
- **Viewer on Linux** — Requires `python3-tk` and the matching `python3.X-tk` system package (e.g. `python3.11-tk`).
