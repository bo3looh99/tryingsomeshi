"""
TikTok DM Parser v7.0
─────────────────────────────────────────────────────────────────────────────
What's new vs v6.1:
  • Per-account output folder — everything related to one account (state,
    avatars, JSON, optional media) lives in dms_<username>/
  • Avatar capture from inbox list items, not chat header — fixes the
    bleed-through bug where late conversations all shared one avatar
  • Inbox count now from actual DOM enumeration (badge was inaccurate)
  • Story-reaction detection now catches the "This message type isn't
    supported. Download TikTok app to view this message." web placeholder
  • Stronger sender (is_me) detection: class + data-e2e + computed style
  • Stronger timestamp extraction: DOM selectors, attributes, in-text regex
  • Optional --download-media flag — saves images/videos from chats locally
  • Auto-detect your @username from the nav if --username isn't supplied

Output folder layout (auto-created, one per account):
  dms_<username>/
    tiktok_dms_full_<date>.json   ← final output
    tiktok_dms_state.json         ← resume state
    tiktok_dms_errors.log
    tiktok_avatars/
    tiktok_media/                 ← only created with --download-media
"""

import argparse
import json
import os
import random
import re
import time
import traceback
from datetime import datetime, timedelta

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Chromedriver: set if undetected-chromedriver's auto-download is flaky ───
# Download a matching build from:
#   https://googlechromelabs.github.io/chrome-for-testing/
# Then put the absolute path below. Leave empty to let uc auto-fetch.
_CHROMEDRIVER_PATH   = ""    # e.g. r"C:\drivers\chromedriver-win64\chromedriver.exe"
_CHROME_VERSION_MAIN = 147   # Pin to your installed Chrome major version

# Rate limit: 15-30 convos/hr  →  120-240s per convo
_MIN_SECONDS_PER_CONVO = 120
_MAX_SECONDS_PER_CONVO = 240

_LONG_BREAK_EVERY_MIN = 6
_LONG_BREAK_EVERY_MAX = 10
_LONG_BREAK_SECS_MIN  = 180
_LONG_BREAK_SECS_MAX  = 420

_LOGIN_MAX_ATTEMPTS = 5


# ─────────────────────────────────────────────────────────────────────────────
#  Output folder management — everything contained in one folder
# ─────────────────────────────────────────────────────────────────────────────

class Paths:
    """All output paths — set once in main() based on --username / detected."""
    output_dir:    str = "."
    state_file:    str = "tiktok_dms_state.json"
    error_log:     str = "tiktok_dms_errors.log"
    avatar_dir:    str = "tiktok_avatars"
    media_dir:     str = "tiktok_media"
    output_prefix: str = "tiktok_dms_full"

    @classmethod
    def init(cls, root: str):
        cls.output_dir    = root
        os.makedirs(root, exist_ok=True)
        cls.state_file    = os.path.join(root, "tiktok_dms_state.json")
        cls.error_log     = os.path.join(root, "tiktok_dms_errors.log")
        cls.avatar_dir    = os.path.join(root, "tiktok_avatars")
        cls.media_dir     = os.path.join(root, "tiktok_media")
        cls.output_prefix = os.path.join(root, "tiktok_dms_full")


def make_output_dir_name(username: str | None) -> str:
    if username:
        safe = re.sub(r"[^\w\-]", "_", username.lstrip("@"))
        return f"dms_{safe}"
    return f"dms_run_{datetime.now():%Y%m%d_%H%M%S}"


# ─────────────────────────────────────────────────────────────────────────────
#  Human-like timing
# ─────────────────────────────────────────────────────────────────────────────

def _jitter(lo=0.8, hi=2.2):
    time.sleep(random.uniform(lo, hi))


def _think_pause(lo=3.0, hi=7.0):
    time.sleep(random.uniform(lo, hi))


def _take_break(seconds: float):
    if seconds <= 0:
        return
    eta = datetime.now() + timedelta(seconds=seconds)
    print(f"   ☕ Pausing {int(seconds)}s (resumes ~{eta:%H:%M:%S})...")
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(5.0, end - time.time()))


def _human_scroll(driver, container=None, direction="up", times=1):
    for _ in range(times):
        amount = random.randint(300, 1100)
        if direction == "up":
            amount = -amount
        try:
            if container is not None:
                driver.execute_script(
                    "arguments[0].scrollTop += arguments[1];",
                    container, amount,
                )
            else:
                driver.execute_script(f"window.scrollBy(0, {amount});")
        except Exception:
            pass
        _jitter(0.6, 1.6)


# ─────────────────────────────────────────────────────────────────────────────
#  Driver
# ─────────────────────────────────────────────────────────────────────────────

def build_driver(headless: bool, proxy: str | None) -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,900")
    options.add_argument(f"--user-agent={_UA}")
    options.add_argument("--lang=en-US,en;q=0.9")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if proxy:
        if not proxy.startswith("http"):
            proxy = f"http://{proxy}"
        options.add_argument(f"--proxy-server={proxy}")
        print(f"🔒 Proxy: {proxy}")

    uc_kwargs = {
        "options":        options,
        "headless":       headless,
        "use_subprocess": True,
    }
    if _CHROMEDRIVER_PATH:
        uc_kwargs["driver_executable_path"] = _CHROMEDRIVER_PATH
        print(f"🧩 Using chromedriver at: {_CHROMEDRIVER_PATH}")
    if _CHROME_VERSION_MAIN:
        uc_kwargs["version_main"] = _CHROME_VERSION_MAIN

    driver = uc.Chrome(**uc_kwargs)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = { runtime: {} };
        """
    })
    return driver


# ─────────────────────────────────────────────────────────────────────────────
#  Login
# ─────────────────────────────────────────────────────────────────────────────

def _looks_logged_in(driver) -> bool:
    try:
        url = driver.current_url or ""
        if "/login" in url:
            return False
        if driver.find_elements(By.CSS_SELECTOR, "img[class*='ImgAvatar']"):
            return True
        items = driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='DivItemInfo'], div[role='listitem']",
        )
        return len(items) > 0
    except Exception:
        return False


def detect_own_username(driver) -> str | None:
    """Try several strategies to read the logged-in user's @handle."""
    # Strategy 1: any /@username link in the nav
    try:
        for link in driver.find_elements(By.CSS_SELECTOR, "a[href*='/@']"):
            href = link.get_attribute("href") or ""
            if "/@" in href:
                slug = href.split("/@")[-1].split("?")[0].split("/")[0].strip()
                if slug and slug not in ("following", "followers", "explore",
                                          "live", "discover", "tag"):
                    return slug
    except Exception:
        pass

    # Strategy 2: profile data-e2e nav element
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "[data-e2e='nav-profile']"):
            href = el.get_attribute("href") or ""
            if "/@" in href:
                slug = href.split("/@")[-1].split("?")[0].split("/")[0].strip()
                if slug:
                    return slug
    except Exception:
        pass

    # Strategy 3: parse global JS state (works on most TikTok pages)
    try:
        result = driver.execute_script("""
            try {
              const a = window.SIGI_STATE?.AppContext?.user?.uniqueId;
              if (a) return a;
              const b = window.__INITIAL_STATE__?.user?.uniqueId;
              if (b) return b;
              const link = document.querySelector('a[href*="/@"]');
              if (link) {
                const m = link.href.match(/\\/@([^\\/?]+)/);
                if (m) return m[1];
              }
            } catch (e) {}
            return null;
        """)
        if result and isinstance(result, str):
            slug = result.strip().lstrip("@")
            if slug:
                return slug
    except Exception:
        pass

    return None


def _verify_profile_url(driver, username: str) -> bool:
    url = f"https://www.tiktok.com/@{username.lstrip('@')}"
    try:
        driver.get(url)
        _think_pause(4, 7)
        cur = (driver.current_url or "").lower()
        if "/login" in cur:
            return False
        markers = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-e2e='user-title'], [data-e2e='user-subtitle'], "
            "[data-e2e='user-avatar']",
        )
        return len(markers) > 0
    except Exception:
        return False


def login_with_retry(driver, sessionid: str, username: str | None = None) -> bool:
    for attempt in range(1, _LOGIN_MAX_ATTEMPTS + 1):
        print(f"\n🔐 Login attempt {attempt}/{_LOGIN_MAX_ATTEMPTS}...")
        try:
            driver.get("https://www.tiktok.com")
            _think_pause(2, 4)

            try:
                driver.delete_cookie("sessionid")
            except Exception:
                pass

            driver.add_cookie({
                "name":   "sessionid",
                "value":  sessionid,
                "domain": ".tiktok.com",
                "path":   "/",
            })

            driver.refresh()
            _think_pause(4, 7)

            driver.get("https://www.tiktok.com/messages")
            _think_pause(7, 12)

            ok = _looks_logged_in(driver)

            if ok and username:
                print(f"   🔎 Verifying /@{username.lstrip('@')}...")
                if _verify_profile_url(driver, username):
                    print("   ✅ Profile page loaded — login confirmed.")
                else:
                    print("   ⚠️  Profile page didn't load — treating as failed login.")
                    ok = False
                driver.get("https://www.tiktok.com/messages")
                _think_pause(5, 9)

            if ok:
                print("   ✅ Logged in.")
                return True

            print("   ⚠️  Login looks rejected — retrying...")
            _take_break(random.uniform(8, 18))

        except Exception as e:
            print(f"   ❌ Attempt error: {e}")
            _take_break(random.uniform(8, 18))

    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Inbox loading + count + ETA
# ─────────────────────────────────────────────────────────────────────────────

def load_inbox(driver, max_passes: int = 60):
    """Scroll until all conversation list items are loaded."""
    prev = -1
    stagnant = 0
    for _ in range(max_passes):
        _human_scroll(driver, times=1, direction="down")
        _jitter(0.8, 1.6)
        cur = len(driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='DivItemInfo'], div[role='listitem']"))
        if cur == prev:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
        prev = cur


def count_loaded_conversations(driver) -> int:
    """Actual count of conversation list items currently in DOM —
    more reliable than the tab badge. Call after load_inbox()."""
    return len(driver.find_elements(
        By.CSS_SELECTOR,
        "div[class*='DivItemInfo'], div[role='listitem']",
    ))


def print_eta(total: int | None, completed: int):
    if total is None:
        return
    remaining = max(0, total - completed)
    if remaining <= 0:
        print(f"   ⏱️  All {total} conversations done.")
        return
    avg = (_MIN_SECONDS_PER_CONVO + _MAX_SECONDS_PER_CONVO) / 2
    breaks_secs = (remaining / 8) * 300
    seconds = remaining * avg + breaks_secs
    eta = datetime.now() + timedelta(seconds=seconds)
    hrs = seconds / 3600
    print(f"   ⏱️  ETA: {completed}/{total} done · "
          f"~{hrs:.1f}h remaining (rate ~15-30/hr) → "
          f"finish ~{eta:%Y-%m-%d %H:%M}")


def open_requests_tab(driver) -> bool:
    print("\n📂 Opening Message Requests folder...")
    selectors = [
        "[data-e2e='message-requests']",
        "[data-e2e='inbox-requests']",
        "[data-e2e*='request']",
        "[class*='Requests']",
        "[class*='RequestTab']",
        "a[href*='requests']",
    ]
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = (el.text or "").lower()
                if "request" in txt or sel.startswith("[data-e2e"):
                    el.click()
                    _think_pause(4, 7)
                    print(f"   ✅ Clicked requests via selector: {sel}")
                    return True
        except Exception:
            continue

    try:
        for el in driver.find_elements(By.XPATH, "//*[contains(text(), 'Request')]"):
            try:
                el.click()
                _think_pause(4, 7)
                print("   ✅ Clicked requests via text match")
                return True
            except Exception:
                continue
    except Exception:
        pass

    print("   ⚠️  Could not locate Message Requests tab — skipping.")
    return False


def return_to_main_inbox(driver):
    driver.get("https://www.tiktok.com/messages")
    _think_pause(5, 9)


# ─────────────────────────────────────────────────────────────────────────────
#  Resume state
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if not os.path.exists(Paths.state_file):
        return {"completed_usernames": [], "conversations": [], "started_at": None}
    try:
        with open(Paths.state_file, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("completed_usernames", [])
        s.setdefault("conversations", [])
        s.setdefault("started_at", None)
        return s
    except Exception as e:
        print(f"⚠️  Could not load state ({e}) — starting fresh.")
        return {"completed_usernames": [], "conversations": [], "started_at": None}


def save_state(state: dict):
    tmp = Paths.state_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, Paths.state_file)
    except Exception as e:
        print(f"⚠️  Could not save state: {e}")


def clear_state():
    if os.path.exists(Paths.state_file):
        try:
            os.remove(Paths.state_file)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Avatars
# ─────────────────────────────────────────────────────────────────────────────

def _get_list_item_avatar_url(item) -> str | None:
    """Get the avatar URL from a conversation list item BEFORE clicking it.
    Fixes the bleed-through bug where the chat header sometimes still showed
    the previous conversation's avatar."""
    try:
        for sel in (
            "img[class*='Avatar']",
            "img[class*='ImgAvatar']",
            "img[class*='Img']",
            "img",
        ):
            for img in item.find_elements(By.CSS_SELECTOR, sel):
                src = img.get_attribute("src") or ""
                if src.startswith("http") and "tiktok" in src:
                    return src
    except Exception:
        pass
    return None


def _download_avatar(url: str, filename: str) -> str | None:
    os.makedirs(Paths.avatar_dir, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", filename) + ".jpg"
    path = os.path.join(Paths.avatar_dir, safe)
    if os.path.exists(path):
        return path
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception as e:
        print(f"   ⚠️  Avatar download failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Media (--download-media)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_media_from_message(msg_element) -> list[dict]:
    """Return [{type, url}] for downloadable media inside this message element."""
    media = []
    seen = set()
    try:
        for img in msg_element.find_elements(
            By.CSS_SELECTOR,
            "img:not([class*='Avatar']):not([class*='Emoji'])",
        ):
            src = img.get_attribute("src") or ""
            if src.startswith("http") and not src.endswith(".svg") and src not in seen:
                seen.add(src)
                media.append({"type": "image", "url": src})
    except Exception:
        pass
    try:
        for vid in msg_element.find_elements(By.CSS_SELECTOR, "video"):
            src = vid.get_attribute("src") or ""
            if not src:
                try:
                    src = vid.find_element(By.TAG_NAME, "source").get_attribute("src") or ""
                except Exception:
                    pass
            if src.startswith("http") and src not in seen:
                seen.add(src)
                media.append({"type": "video", "url": src})
    except Exception:
        pass
    return media


def _download_media_file(url: str, dest_dir: str, prefix: str) -> str | None:
    os.makedirs(dest_dir, exist_ok=True)
    base = url.split("?")[0]
    last_seg = base.rsplit("/", 1)[-1]
    ext = last_seg.rsplit(".", 1)[-1].lower() if "." in last_seg else "bin"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "mp4", "mov", "webm", "m4v"):
        ext = "bin"
    safe_prefix = re.sub(r"[^\w\-]", "_", prefix)[:80]
    fname = f"{safe_prefix}_{abs(hash(url)) % 10**8}.{ext}"
    path = os.path.join(dest_dir, fname)
    if os.path.exists(path):
        return path
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"},
            timeout=60,
            stream=True,
        )
        if r.status_code == 200:
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return path
        print(f"   ⚠️  Media HTTP {r.status_code} for {url[:80]}")
    except Exception as e:
        print(f"   ⚠️  Media download failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Message classification + timestamp extraction
# ─────────────────────────────────────────────────────────────────────────────

_STORY_REACTION_PATTERNS = [
    re.compile(r"reacted (?:to )?(?:your |an? )?story", re.I),
    re.compile(r"replied to (?:your )?story", re.I),
    re.compile(r"^story\s*[:\-]", re.I),
]

# TikTok web's placeholder for messages it can't render (story reactions,
# certain media, etc.). We treat these as story_reaction so the viewer
# doesn't display the "Download TikTok app" garbage to the user.
_UNSUPPORTED_PLACEHOLDERS = (
    "this message type isn't supported",
    "this message type isn’t supported",  # curly apostrophe
    "download tiktok app to view this message",
    "download tiktok app",
)


def _classify_message(text: str, msg_element) -> str:
    """Return one of: 'text', 'story_reaction', 'shared_post', 'media', 'system'."""
    tl = (text or "").strip()
    tl_lower = tl.lower()

    for marker in _UNSUPPORTED_PLACEHOLDERS:
        if marker in tl_lower:
            return "story_reaction"

    for pat in _STORY_REACTION_PATTERNS:
        if pat.search(tl):
            return "story_reaction"

    try:
        if msg_element.find_elements(
            By.CSS_SELECTOR,
            "video, img[class*='Img']:not([class*='Avatar']), "
            "[class*='SharedPost'], [class*='SharedVideo']",
        ):
            if msg_element.find_elements(
                By.CSS_SELECTOR,
                "[class*='Shared'], a[href*='/video/']",
            ):
                return "shared_post"
            return "media"
    except Exception:
        pass

    if re.search(r'^(?:You|This account|User) (?:can|cannot|blocked|has)', tl, re.I):
        return "system"

    return "text" if tl else "media"


# Match a date/time embedded in TikTok's per-message text, e.g.:
#   "Apr 27, 2026 10:30 AM"  /  "10:30 AM"  /  "Yesterday 14:05"
_TIMESTAMP_REGEX = re.compile(
    r"(?:\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}(?:[\s,]+\d{1,2}:\d{2}(?:\s*[AP]M)?)?"
    r"|\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b)",
    re.I,
)


def _extract_timestamp(msg_element, text: str) -> str:
    # 1. timestamp element by class / data-e2e
    for sel in (
        "[class*='Timestamp']",
        "[class*='TimeStamp']",
        "[data-e2e*='time']",
        "[class*='MessageTime']",
        "[class*='Time']",
        "span[class*='time']",
        "small",
        "time",
    ):
        try:
            for el in msg_element.find_elements(By.CSS_SELECTOR, sel):
                t = (el.text or "").strip()
                if t and re.search(r"\d", t):
                    return t
        except Exception:
            continue

    # 2. tooltip / aria-label on the message itself
    for attr in ("title", "aria-label", "data-tooltip"):
        try:
            v = msg_element.get_attribute(attr) or ""
            if v and re.search(r"\d{1,2}:\d{2}", v):
                return v.strip()
        except Exception:
            pass

    # 3. embedded in the visible text
    if text:
        m = _TIMESTAMP_REGEX.search(text)
        if m:
            return m.group(0).strip()

    return ""


def _strip_timestamp_from_text(text: str, ts: str) -> str:
    if not ts or ts not in text:
        return text
    return text.replace(ts, "").strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Chat extraction
# ─────────────────────────────────────────────────────────────────────────────

def _wait_for_chat_loaded(driver, timeout: int = 35) -> bool:
    end = time.time() + timeout
    last_count = -1
    stable = 0
    while time.time() < end:
        try:
            n = len(driver.find_elements(
                By.CSS_SELECTOR, "div[data-e2e*='message']"))
            if n > 0 and n == last_count:
                stable += 1
                if stable >= 3:
                    return True
            else:
                stable = 0
            last_count = n
        except Exception:
            pass
        time.sleep(1.0)
    return last_count > 0


def _wait_for_chat_username(driver, expected: str, timeout: int = 10) -> bool:
    """Wait for the chat header to reflect the conversation we just opened —
    extra defense against the avatar bleed-through bug."""
    end = time.time() + timeout
    expected_l = (expected or "").lower()
    if not expected_l:
        return False
    while time.time() < end:
        try:
            for sel in ("[data-e2e='chat-header']",
                         "[data-e2e='conversation-header']",
                         "[class*='DivChatHeader']",
                         "[class*='DivConversationHeader']",
                         "[class*='DivHeader']"):
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if expected_l in (el.text or "").lower():
                        return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _detect_is_me(driver, msg, classes: str) -> bool:
    """Multi-strategy is_me detection. Returns True if message is from owner."""
    cl = classes.lower()
    if any(k in cl for k in ("right", "my", "self", "sender-me", "outgoing")):
        return True

    try:
        e2e = (msg.get_attribute("data-e2e") or "").lower()
        if any(k in e2e for k in ("self", "outgoing", "right", "-me")):
            return True
    except Exception:
        pass

    # Computed style — right-aligned bubbles are usually "me"
    try:
        result = driver.execute_script("""
            const el = arguments[0];
            const cs = window.getComputedStyle(el);
            if (cs.alignSelf === 'flex-end') return true;
            if (cs.marginLeft === 'auto' && cs.marginRight !== 'auto') return true;
            const parent = el.parentElement;
            if (parent) {
              const pcs = window.getComputedStyle(parent);
              if (pcs.justifyContent === 'flex-end') return true;
            }
            return false;
        """, msg)
        if result:
            return True
    except Exception:
        pass

    return False


def extract_chat_history(driver, scroll_times: int, download_media: bool,
                          username: str) -> tuple[list, list]:
    """Returns (messages, media_records).
    media_records: [{message_index, type, url, local_path?}]"""
    messages: list = []
    media_records: list = []

    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body")))

        if not _wait_for_chat_loaded(driver, timeout=40):
            print("   ⚠️  Chat did not stabilize — continuing anyway")

        chat_container = None
        try:
            chat_container = driver.find_element(
                By.CSS_SELECTOR, "div[class*='DivMessage']")
            print("   ✅ Chat container found")
        except Exception:
            print("   ⚠️  Chat container missing — falling back to window scroll")

        print(f"   📜 Scrolling up (max {scroll_times} passes)...")
        prev_count = -1
        stagnant = 0
        for _ in range(scroll_times):
            _human_scroll(driver, container=chat_container,
                          direction="up", times=1)
            time.sleep(random.uniform(2.0, 3.5))
            cur = len(driver.find_elements(
                By.CSS_SELECTOR, "div[data-e2e*='message']"))
            if cur == prev_count:
                stagnant += 1
                if stagnant >= 4:
                    print(f"   ✅ History fully loaded ({cur} nodes)")
                    break
            else:
                stagnant = 0
            prev_count = cur

        msg_elements = driver.find_elements(
            By.CSS_SELECTOR, "div[data-e2e*='message']")
        print(f"   ✅ {len(msg_elements)} message nodes")

        for msg in msg_elements:
            try:
                raw_text = (msg.text or "").strip()
                classes = msg.get_attribute("class") or ""

                is_me = _detect_is_me(driver, msg, classes)
                timestamp = _extract_timestamp(msg, raw_text)
                cleaned_text = _strip_timestamp_from_text(raw_text, timestamp)
                msg_type = _classify_message(cleaned_text, msg)

                # If body was nothing but a placeholder, blank the text out
                final_text = cleaned_text
                if msg_type == "story_reaction":
                    fl = final_text.lower()
                    if any(p in fl for p in _UNSUPPORTED_PLACEHOLDERS):
                        final_text = ""

                media_in_msg = _extract_media_from_message(msg) if download_media else []

                # Skip pure noise (no text, no media, plain "text" type)
                if not final_text and msg_type == "text" and not media_in_msg:
                    continue

                messages.append({
                    "is_me":             is_me,
                    "sender":            "Me" if is_me else "Them",
                    "text":              final_text,
                    "raw_text":          raw_text,
                    "timestamp":         timestamp or "",
                    "message_type":      msg_type,
                    "is_story_reaction": msg_type == "story_reaction",
                    "media":             [
                        {"type": m["type"], "url": m["url"]}
                        for m in media_in_msg
                    ],
                })
                if media_in_msg:
                    msg_index = len(messages) - 1
                    for m in media_in_msg:
                        media_records.append({
                            "message_index": msg_index,
                            "type":          m["type"],
                            "url":           m["url"],
                        })
            except Exception:
                continue

    except Exception as e:
        print(f"   ⚠️  Chat extraction issue: {e}")

    counts: dict[str, int] = {}
    for m in messages:
        counts[m["message_type"]] = counts.get(m["message_type"], 0) + 1
    print(f"   ✅ Extracted {len(messages)} messages — {counts}")
    if media_records:
        print(f"   🎞️  {len(media_records)} media items found in this chat")
    return messages, media_records


# ─────────────────────────────────────────────────────────────────────────────
#  Errors → log
# ─────────────────────────────────────────────────────────────────────────────

def log_error(username: str, exc: Exception):
    try:
        with open(Paths.error_log, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat()}] {username}: {exc}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Main scrape loop
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_current_folder(driver, source: str, max_convos: int,
                            chat_scrolls: int, download_media: bool,
                            state: dict, completed: set, out_convos: list,
                            counters: dict) -> None:
    print(f"\n📁 Scraping folder: {source}")
    print("📜 Loading folder list...")
    load_inbox(driver)

    folder_total = count_loaded_conversations(driver)
    print(f"📊 {folder_total} conversations enumerated in {source}.")

    limit = max_convos if max_convos > 0 else 9999
    i = 0

    while i < limit:
        conv_items = driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='DivItemInfo'], div[role='listitem']")

        if i >= len(conv_items):
            print(f"\n   All {len(conv_items)} listed conversations processed.")
            break

        # Resolve username + grab avatar URL FROM THE LIST ITEM, before clicking
        username = f"conv_{i+1}"
        list_avatar_url = None
        try:
            raw = (conv_items[i].text or "").strip()
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if lines:
                username = lines[0]
            list_avatar_url = _get_list_item_avatar_url(conv_items[i])
        except Exception:
            pass

        tagged_key = f"{source}:{username}"
        if tagged_key in completed or username in completed:
            print(f"⏭️  [{i+1}] Skipping (already done) → {username} [{source}]")
            i += 1
            continue

        # Rate limit
        if counters["last_convo_at"]:
            elapsed = time.time() - counters["last_convo_at"]
            target = random.uniform(_MIN_SECONDS_PER_CONVO,
                                     _MAX_SECONDS_PER_CONVO)
            if elapsed < target:
                _take_break(target - elapsed)

        # Periodic long break
        if (counters["processed_this_run"] > 0
                and counters["processed_this_run"] >= counters["next_long_break_at"]):
            print(f"\n   🌙 Periodic long break (after "
                  f"{counters['processed_this_run']} convos)...")
            _take_break(random.uniform(_LONG_BREAK_SECS_MIN,
                                        _LONG_BREAK_SECS_MAX))
            counters["next_long_break_at"] = (
                counters["processed_this_run"]
                + random.randint(_LONG_BREAK_EVERY_MIN, _LONG_BREAK_EVERY_MAX)
            )

        try:
            print(f"\n📨 [{i+1}] Opening → {username} [{source}]")

            conv_items = driver.find_elements(
                By.CSS_SELECTOR,
                "div[class*='DivItemInfo'], div[role='listitem']")
            if i >= len(conv_items):
                print("   ⚠️  Item disappeared from DOM — stopping.")
                break

            conv_items[i].click()
            _think_pause(5, 9)

            # Defend against bleed-through: wait for header to actually update
            _wait_for_chat_username(driver, username, timeout=10)

            messages, media_records = extract_chat_history(
                driver, chat_scrolls, download_media, username,
            )

            # Avatar — prefer the URL captured from the list item
            avatar_url = list_avatar_url
            avatar_path = None
            if avatar_url:
                avatar_path = _download_avatar(avatar_url, username)
                if avatar_path:
                    print(f"   🖼️  Avatar → {avatar_path}")

            if download_media and media_records:
                downloaded = 0
                for rec in media_records:
                    local = _download_media_file(
                        rec["url"], Paths.media_dir,
                        f"{username}_{rec['type']}_{rec['message_index']}",
                    )
                    rec["local_path"] = local
                    msg_idx = rec["message_index"]
                    if 0 <= msg_idx < len(messages):
                        for media_item in messages[msg_idx]["media"]:
                            if media_item["url"] == rec["url"]:
                                media_item["local_path"] = local
                                break
                    if local:
                        downloaded += 1
                print(f"   📥 Downloaded {downloaded}/{len(media_records)} media files")

            type_counts: dict[str, int] = {}
            for m in messages:
                t = m.get("message_type", "text")
                type_counts[t] = type_counts.get(t, 0) + 1

            convo = {
                "username":            username,
                "source":              source,
                "is_request":          source == "requests",
                "avatar_url":          avatar_url,
                "avatar_path":         avatar_path,
                "messages":            messages,
                "message_count":       len(messages),
                "message_type_counts": type_counts,
                "media_count":         len(media_records),
                "scraped_at":          datetime.now().isoformat(),
            }
            out_convos.append(convo)
            completed.add(tagged_key)

            state["completed_usernames"] = sorted(completed)
            state["conversations"]      = out_convos
            save_state(state)
            print(f"   💾 Saved — {len(completed)} convos in state file")

            counters["last_convo_at"] = time.time()
            counters["processed_this_run"] += 1

            return_to_main_inbox(driver)
            if source == "requests":
                open_requests_tab(driver)
            load_inbox(driver)

            print_eta(counters.get("total"), len(completed))

        except KeyboardInterrupt:
            print("\n   🛑 Interrupted — state already saved, you can resume.")
            raise
        except Exception as e:
            print(f"   ❌ Error on [{i+1}] {username}: {e}")
            log_error(username, e)
            try:
                return_to_main_inbox(driver)
                if source == "requests":
                    open_requests_tab(driver)
                load_inbox(driver)
            except Exception:
                pass
            _take_break(random.uniform(15, 30))

        i += 1


def run_full_scraper(driver, max_convos: int, chat_scrolls: int,
                      download_media: bool, state: dict,
                      include_requests: bool = False) -> list:
    completed: set = set(state.get("completed_usernames", []))
    out_convos: list = list(state.get("conversations", []))

    return_to_main_inbox(driver)
    load_inbox(driver)
    total = count_loaded_conversations(driver)
    print(f"📊 {total} total conversations enumerated in main inbox.")
    print_eta(total, len(completed))

    counters = {
        "last_convo_at":      0.0,
        "processed_this_run": 0,
        "next_long_break_at": random.randint(_LONG_BREAK_EVERY_MIN,
                                              _LONG_BREAK_EVERY_MAX),
        "total":              total,
    }

    _scrape_current_folder(
        driver, "inbox", max_convos, chat_scrolls, download_media,
        state, completed, out_convos, counters,
    )

    if include_requests:
        return_to_main_inbox(driver)
        if open_requests_tab(driver):
            _scrape_current_folder(
                driver, "requests", max_convos, chat_scrolls, download_media,
                state, completed, out_convos, counters,
            )
        else:
            print("ℹ️  Skipping requests — tab not found.")

    return out_convos


# ─────────────────────────────────────────────────────────────────────────────
#  Output
# ─────────────────────────────────────────────────────────────────────────────

def build_output_path() -> str:
    base = datetime.now().strftime("%Y-%m-%d")
    path = f"{Paths.output_prefix}_{base}.json"
    if not os.path.exists(path):
        return path
    for n in range(2, 200):
        candidate = f"{Paths.output_prefix}_{base}_{n}.json"
        if not os.path.exists(candidate):
            return candidate
    return path


def save_to_json(conversations: list, username: str | None = None) -> str:
    owner_profile: dict = {}
    if username:
        owner_profile["username"] = username.lstrip("@")

    summary = {"inbox": 0, "requests": 0, "story_reactions": 0,
               "media_items": 0}
    for c in conversations:
        if c.get("source") == "requests":
            summary["requests"] += 1
        else:
            summary["inbox"] += 1
        for m in c.get("messages", []):
            if m.get("is_story_reaction"):
                summary["story_reactions"] += 1
            summary["media_items"] += len(m.get("media", []))

    output = {
        "owner_profile": owner_profile,
        "summary":       summary,
        "conversations": conversations,
    }
    filename = build_output_path()
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 ✅ Saved {len(conversations)} conversations → {filename}")
    print(f"     summary: {summary}")
    return filename


# ─────────────────────────────────────────────────────────────────────────────
#  Entry
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TikTok DM Parser v7.0 — per-account folder · resume · stealth")
    parser.add_argument("--sessionid",        required=True,
                        help="TikTok sessionid cookie value")
    parser.add_argument("--username",         default=None,
                        help="Your TikTok @handle (optional). Used for stronger "
                             "login verification, picks the output folder name "
                             "(dms_<username>/), and stored in the JSON. If "
                             "omitted, the script tries to auto-detect from nav.")
    parser.add_argument("--output-dir",       default=None,
                        help="Override output folder (default: dms_<username> "
                             "or dms_run_<timestamp>)")
    parser.add_argument("--headless",         action="store_true")
    parser.add_argument("--proxy",            default=None,
                        help="host:port  or  user:pass@host:port")
    parser.add_argument("--max-convos",       type=int, default=0,
                        help="Per-folder cap. 0 = all (default)")
    parser.add_argument("--chat-scrolls",     type=int, default=15)
    parser.add_argument("--include-requests", action="store_true",
                        help="Also scrape the Message Requests folder")
    parser.add_argument("--download-media",   action="store_true",
                        help="Download images / videos found in conversations "
                             "into <output_dir>/tiktok_media/")
    parser.add_argument("--reset",            action="store_true",
                        help="Wipe saved state and start over")
    args = parser.parse_args()

    print("🚀 TikTok DM Parser v7.0")

    initial_dir = args.output_dir or make_output_dir_name(args.username)
    Paths.init(initial_dir)
    print(f"📁 Output folder: {Paths.output_dir}")

    if args.reset:
        clear_state()
        print("🗑️  State cleared.")

    state = load_state()
    if state.get("completed_usernames"):
        print(f"♻️  Resuming — {len(state['completed_usernames'])} already done")
    if not state.get("started_at"):
        state["started_at"] = datetime.now().isoformat()
        save_state(state)

    driver = build_driver(args.headless, args.proxy)
    conversations: list = list(state.get("conversations", []))
    final_username = args.username

    try:
        if not login_with_retry(driver, args.sessionid, args.username):
            print("❌ Login failed after retries. State preserved — try again later.")
            return

        # Auto-detect username (still saved into the JSON even if --output-dir
        # was given, so the viewer can show "you")
        if not args.username:
            detected = detect_own_username(driver)
            if detected:
                print(f"🔍 Detected your @username from nav: @{detected}")
                final_username = detected
                if not args.output_dir:
                    new_dir = make_output_dir_name(detected)
                    if new_dir != Paths.output_dir:
                        print(f"📁 Using detected username for output folder: {new_dir}")
                        Paths.init(new_dir)
                        # Reload state from the new folder (likely empty).
                        # Old temp folder is left in place; user can delete.
                        state = load_state()
                        conversations = list(state.get("conversations", []))
                        if not state.get("started_at"):
                            state["started_at"] = datetime.now().isoformat()
                            save_state(state)
            else:
                print("ℹ️  Couldn't auto-detect username; output stays in current folder.")

        return_to_main_inbox(driver)

        conversations = run_full_scraper(
            driver, args.max_convos, args.chat_scrolls, args.download_media,
            state, include_requests=args.include_requests,
        )

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user. State preserved — rerun to resume.")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if conversations:
        save_to_json(conversations, username=final_username)
    else:
        print("ℹ️  Nothing to save this run.")


if __name__ == "__main__":
    main()
