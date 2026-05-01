"""
TikTok DM Parser v6.0
─────────────────────────────────────────────────────────────────────────────
What's new vs v5:
  • Profile scraper REMOVED
  • Login retry loop (handles flaky first-attempt logins)
  • Resume-from-crash: progress saved after every conversation
  • Human-like behavior: jittered delays, randomized scrolls, periodic breaks
  • Rate limit: 15–30 convos/hour (configurable), with extra long breaks
  • Inbox count detection → ETA shown before scraping starts and after each convo
  • Robust per-conversation error handling + error log file
  • Slow-connection safe: waits for chat to stabilize before extracting

Files written:
  tiktok_dms_state.json       ← resume state (delete or use --reset to start over)
  tiktok_dms_full_<date>.json ← final output (compatible with the viewer)
  tiktok_dms_errors.log       ← per-convo error tracebacks
  tiktok_avatars/             ← downloaded avatar images
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

_AVATAR_DIR    = "tiktok_avatars"
_STATE_FILE    = "tiktok_dms_state.json"
_OUTPUT_PREFIX = "tiktok_dms_full"
_ERROR_LOG     = "tiktok_dms_errors.log"

# ── Chromedriver: set if undetected-chromedriver's auto-download is flaky ───
# Download a matching build from:
#   https://googlechromelabs.github.io/chrome-for-testing/
# Then put the absolute path below. Leave empty to let uc auto-fetch.
_CHROMEDRIVER_PATH   = ""    # e.g. r"C:\drivers\chromedriver-win64\chromedriver.exe"
_CHROME_VERSION_MAIN = 147   # Pin to your installed Chrome major version (you said 147)

# Rate limit: 15–30 convos/hr  →  120–240 s per convo
_MIN_SECONDS_PER_CONVO = 120   # = 30/hr ceiling
_MAX_SECONDS_PER_CONVO = 240   # = 15/hr floor

# Periodic "long break" every N convos (random N each time) to look human
_LONG_BREAK_EVERY_MIN = 6
_LONG_BREAK_EVERY_MAX = 10
_LONG_BREAK_SECS_MIN  = 180    # 3 min
_LONG_BREAK_SECS_MAX  = 420    # 7 min

_LOGIN_MAX_ATTEMPTS = 5


# ─────────────────────────────────────────────────────────────────────────────
#  Human-like timing
# ─────────────────────────────────────────────────────────────────────────────

def _jitter(lo=0.8, hi=2.2):
    """Short human-like delay (clicks, small actions)."""
    time.sleep(random.uniform(lo, hi))


def _think_pause(lo=3.0, hi=7.0):
    """Longer pause between major actions (page nav, opening chats)."""
    time.sleep(random.uniform(lo, hi))


def _take_break(seconds: float):
    """Long break that prints a wake-up time so you can leave the screen."""
    if seconds <= 0:
        return
    eta = datetime.now() + timedelta(seconds=seconds)
    print(f"   ☕ Pausing {int(seconds)}s (resumes ~{eta:%H:%M:%S})...")
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(5.0, end - time.time()))


def _human_scroll(driver, container=None, direction="up", times=1):
    """Scroll with random pixel amounts and small pauses — feels human."""
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
#  Driver setup
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
#  Login (with retry)
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


def _verify_profile_url(driver, username: str) -> bool:
    """Optional extra verification — visit https://www.tiktok.com/@<username>
    and check we land on a real profile (not a login wall / 404)."""
    url = f"https://www.tiktok.com/@{username.lstrip('@')}"
    try:
        driver.get(url)
        _think_pause(4, 7)
        cur = (driver.current_url or "").lower()
        if "/login" in cur:
            return False
        # A real profile has these data-e2e attributes
        markers = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-e2e='user-title'], [data-e2e='user-subtitle'], "
            "[data-e2e='user-avatar']",
        )
        return len(markers) > 0
    except Exception:
        return False


def login_with_retry(driver, sessionid: str, username: str | None = None) -> bool:
    """Set cookie, navigate to /messages, verify. Retry several times.
    If `username` is given, also confirm /@username loads — more reliable
    than DOM heuristics."""
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
            _think_pause(7, 12)  # generous for slow connections

            ok = _looks_logged_in(driver)

            # Stronger check: if username was given, confirm profile page loads
            if ok and username:
                print(f"   🔎 Verifying /@{username}...")
                if _verify_profile_url(driver, username):
                    print("   ✅ Profile page loaded — login confirmed.")
                else:
                    print("   ⚠️  Profile page didn't load — treating as failed login.")
                    ok = False
                # Return to messages page either way
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
#  Inbox count + ETA
# ─────────────────────────────────────────────────────────────────────────────

def detect_inbox_count(driver) -> int | None:
    """
    Try to read the total conversation count visible on the inbox tab/badge.
    Returns None if we can't find it.
    """
    selectors = [
        "[data-e2e='message-tab-count']",
        "[data-e2e*='inbox'] [class*='Count']",
        "[class*='TabCount']",
        "[class*='Badge']",
        "[class*='Count']",
    ]
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                txt = (el.text or "").strip()
                m = re.search(r'(\d{1,5})', txt)
                if m:
                    n = int(m.group(1))
                    if 0 < n < 10000:
                        return n
        except Exception:
            continue

    # Fallback: scan body text for "Messages (N)" / "Inbox 123" patterns
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = re.search(
            r'(?:Messages?|Inbox|Chats?|DMs?)\s*\(?\s*(\d{1,5})\s*\)?',
            body, re.I,
        )
        if m:
            return int(m.group(1))
    except Exception:
        pass

    return None


def print_eta(total: int | None, completed: int):
    if total is None:
        return
    remaining = total - completed
    if remaining <= 0:
        print(f"   ⏱️  All {total} conversations done.")
        return
    avg = (_MIN_SECONDS_PER_CONVO + _MAX_SECONDS_PER_CONVO) / 2
    # Account for periodic long breaks (~5 min every ~8 convos)
    breaks_secs = (remaining / 8) * 300
    seconds = remaining * avg + breaks_secs
    eta = datetime.now() + timedelta(seconds=seconds)
    hrs = seconds / 3600
    print(f"   ⏱️  ETA: {completed}/{total} done · "
          f"~{hrs:.1f}h remaining (rate ~15-30/hr) → "
          f"finish ~{eta:%Y-%m-%d %H:%M}")


# ─────────────────────────────────────────────────────────────────────────────
#  Resume state
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {"completed_usernames": [], "conversations": [], "started_at": None}
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("completed_usernames", [])
        s.setdefault("conversations", [])
        s.setdefault("started_at", None)
        return s
    except Exception as e:
        print(f"⚠️  Could not load state ({e}) — starting fresh.")
        return {"completed_usernames": [], "conversations": [], "started_at": None}


def save_state(state: dict):
    """Atomic write so a crash mid-write can't corrupt the file."""
    tmp = _STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        print(f"⚠️  Could not save state: {e}")


def clear_state():
    if os.path.exists(_STATE_FILE):
        try:
            os.remove(_STATE_FILE)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Avatars
# ─────────────────────────────────────────────────────────────────────────────

def _download_avatar(url: str, filename: str) -> str | None:
    os.makedirs(_AVATAR_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", filename) + ".jpg"
    path = os.path.join(_AVATAR_DIR, safe)
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


def _get_chat_avatar_url(driver) -> str | None:
    header_selectors = [
        "[data-e2e='chat-header'] img",
        "[data-e2e='conversation-header'] img",
        "[class*='DivChatHeader'] img",
        "[class*='DivConversationHeader'] img",
        "[class*='DivHeader'] img[class*='Avatar']",
        "[class*='DivHeader'] img[class*='Img']",
    ]
    for sel in header_selectors:
        try:
            img = driver.find_element(By.CSS_SELECTOR, sel)
            src = img.get_attribute("src") or ""
            if src.startswith("http") and "tiktok" in src:
                return src
        except Exception:
            continue
    try:
        imgs = driver.find_elements(By.CSS_SELECTOR, "img[class*='ImgAvatar']")
        candidates = [
            img.get_attribute("src") for img in imgs
            if (img.get_attribute("src") or "").startswith("http")
            and "tiktok" in (img.get_attribute("src") or "")
        ]
        if candidates:
            return candidates[-1]
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Chat extraction (slow-connection safe)
# ─────────────────────────────────────────────────────────────────────────────

# Patterns commonly used by TikTok for "X reacted to your story" messages.
# We only treat a message as a story reaction if there's NO real text alongside.
_STORY_REACTION_PATTERNS = [
    re.compile(r'reacted (?:to )?(?:your |an? )?story', re.I),
    re.compile(r'replied to (?:your )?story', re.I),
    re.compile(r"^story\s*[:\-]", re.I),
    re.compile(r'^\W*(?:liked|loved|❤|😂|🔥|😮|😢|🙏|👏)\W*$'),  # bare emoji-only
]


def _classify_message(text: str, msg_element) -> str:
    """Return one of: 'text', 'story_reaction', 'shared_post', 'media', 'system'."""
    tl = (text or "").strip()

    # Story reaction patterns — text is usually short or template-like
    for pat in _STORY_REACTION_PATTERNS:
        if pat.search(tl):
            return "story_reaction"

    # DOM-based hints for media / shared content
    try:
        if msg_element.find_elements(
            By.CSS_SELECTOR,
            "video, img[class*='Img']:not([class*='Avatar']), "
            "[class*='SharedPost'], [class*='SharedVideo']",
        ):
            # Differentiate shared TikTok posts from plain media
            if msg_element.find_elements(
                By.CSS_SELECTOR,
                "[class*='Shared'], a[href*='/video/']",
            ):
                return "shared_post"
            return "media"
    except Exception:
        pass

    # System messages (joined / left / blocked etc.)
    if re.search(r'^(?:You|This account|User) (?:can|cannot|blocked|has)', tl, re.I):
        return "system"

    return "text" if tl else "media"


def _wait_for_chat_loaded(driver, timeout: int = 35) -> bool:
    """
    Wait until the chat container has settled — i.e. the message count
    stops changing for several consecutive checks. Crucial on slow networks
    so we don't grab a half-loaded chat.
    """
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


def extract_chat_history(driver, scroll_times: int = 15) -> list:
    messages: list = []
    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body")))

        if not _wait_for_chat_loaded(driver, timeout=40):
            print("   ⚠️  Chat did not stabilize in time — continuing anyway")

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
            _human_scroll(
                driver,
                container=chat_container,
                direction="up",
                times=1,
            )
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
                text = msg.text.strip()
                if not text:
                    continue
                classes = msg.get_attribute("class") or ""
                is_me = any(
                    k in classes.lower()
                    for k in ("right", "my", "self", "sender-me"))

                timestamp = ""
                time_elems = msg.find_elements(
                    By.CSS_SELECTOR, "span[class*='time'], small")
                if time_elems:
                    timestamp = time_elems[0].text.strip()

                msg_type = _classify_message(text, msg)

                messages.append({
                    "is_me":             is_me,
                    "sender":            "Me" if is_me else "Them",
                    "text":              text,
                    "timestamp":         timestamp or "—",
                    "message_type":      msg_type,
                    "is_story_reaction": msg_type == "story_reaction",
                })
            except Exception:
                continue

    except Exception as e:
        print(f"   ⚠️  Chat extraction issue: {e}")

    # Stats so it's visible in the log what's text vs reactions vs media
    counts: dict[str, int] = {}
    for m in messages:
        counts[m["message_type"]] = counts.get(m["message_type"], 0) + 1
    print(f"   ✅ Extracted {len(messages)} messages — {counts}")
    return messages


# ─────────────────────────────────────────────────────────────────────────────
#  Inbox loading
# ─────────────────────────────────────────────────────────────────────────────

def open_requests_tab(driver) -> bool:
    """Open the Message Requests folder. Returns True if we got there."""
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

    # Fallback: text-based search across clickable elements
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
    """Navigate back to the regular messages inbox."""
    driver.get("https://www.tiktok.com/messages")
    _think_pause(5, 9)


def load_inbox(driver, max_passes: int = 40):
    """Scroll the inbox sidebar so all conversations are in the DOM."""
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


# ─────────────────────────────────────────────────────────────────────────────
#  Errors → log file
# ─────────────────────────────────────────────────────────────────────────────

def log_error(username: str, exc: Exception):
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat()}] {username}: {exc}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Main scrape loop (with resume + rate limit + ETA)
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_current_folder(driver, source: str, max_convos: int,
                            chat_scrolls: int, state: dict,
                            completed: set, out_convos: list,
                            counters: dict) -> None:
    """
    Scrape conversations in the currently-open folder (inbox OR requests).
    Mutates `completed`, `out_convos`, `counters` in place.
    `counters` keys: last_convo_at, processed_this_run, next_long_break_at, total.
    """
    print(f"\n📁 Scraping folder: {source}")
    print("📜 Loading folder list...")
    load_inbox(driver)

    # Try to detect count (may differ between inbox & requests)
    folder_total = detect_inbox_count(driver)
    if folder_total is not None:
        print(f"📊 Folder reports {folder_total} conversations.")

    limit = max_convos if max_convos > 0 else 9999
    i = 0

    while i < limit:
        # Re-find every iteration to avoid stale references
        conv_items = driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='DivItemInfo'], div[role='listitem']")

        if i >= len(conv_items):
            print(f"\n   All {len(conv_items)} listed conversations processed.")
            break

        # Resolve username before clicking, so we can skip if already done
        username = f"conv_{i+1}"
        try:
            raw = (conv_items[i].text or "").strip()
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if lines:
                username = lines[0]
        except Exception:
            pass

        # Tag with folder so we can distinguish inbox vs requests later
        tagged_key = f"{source}:{username}"
        if tagged_key in completed or username in completed:
            print(f"⏭️  [{i+1}] Skipping (already done) → {username} [{source}]")
            i += 1
            continue

        # Rate limit: enforce minimum gap since previous convo
        if counters["last_convo_at"]:
            elapsed = time.time() - counters["last_convo_at"]
            target = random.uniform(_MIN_SECONDS_PER_CONVO,
                                     _MAX_SECONDS_PER_CONVO)
            if elapsed < target:
                _take_break(target - elapsed)

        # Periodic long break to look human
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

            # Re-find immediately before clicking to dodge stale references
            conv_items = driver.find_elements(
                By.CSS_SELECTOR,
                "div[class*='DivItemInfo'], div[role='listitem']")
            if i >= len(conv_items):
                print("   ⚠️  Item disappeared from DOM — stopping.")
                break

            conv_items[i].click()
            _think_pause(5, 9)

            messages = extract_chat_history(driver, chat_scrolls)

            avatar_url  = _get_chat_avatar_url(driver)
            avatar_path = None
            if avatar_url:
                avatar_path = _download_avatar(avatar_url, username)
                if avatar_path:
                    print(f"   🖼️  Avatar → {avatar_path}")

            # Per-convo summary by message_type
            type_counts: dict[str, int] = {}
            for m in messages:
                t = m.get("message_type", "text")
                type_counts[t] = type_counts.get(t, 0) + 1

            convo = {
                "username":           username,
                "source":             source,        # "inbox" | "requests"
                "is_request":         source == "requests",
                "avatar_url":         avatar_url,
                "avatar_path":        avatar_path,
                "messages":           messages,
                "message_count":      len(messages),
                "message_type_counts": type_counts,
                "scraped_at":         datetime.now().isoformat(),
            }
            out_convos.append(convo)
            completed.add(tagged_key)

            # Persist after every conversation — this is the resume point
            state["completed_usernames"] = sorted(completed)
            state["conversations"]      = out_convos
            save_state(state)
            print(f"   💾 Saved — {len(completed)} convos in state file")

            counters["last_convo_at"] = time.time()
            counters["processed_this_run"] += 1

            # Back to the SAME folder we're scraping (inbox or requests)
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
                      state: dict, include_requests: bool = False) -> list:
    """Scrape main inbox, then optionally the Message Requests folder."""
    completed: set = set(state.get("completed_usernames", []))
    out_convos: list = list(state.get("conversations", []))

    # Detect total count from the main inbox first (drives the ETA)
    return_to_main_inbox(driver)
    load_inbox(driver)
    total = detect_inbox_count(driver)
    if total is not None:
        print(f"📊 Inbox tab reports {total} total conversations.")
    else:
        print("📊 Couldn't detect total count — ETA disabled.")
    print_eta(total, len(completed))

    counters = {
        "last_convo_at":      0.0,
        "processed_this_run": 0,
        "next_long_break_at": random.randint(_LONG_BREAK_EVERY_MIN,
                                              _LONG_BREAK_EVERY_MAX),
        "total":              total,
    }

    # ── Main inbox ────────────────────────────────────────────────────────
    _scrape_current_folder(
        driver, "inbox", max_convos, chat_scrolls,
        state, completed, out_convos, counters,
    )

    # ── Message Requests (optional) ───────────────────────────────────────
    if include_requests:
        return_to_main_inbox(driver)
        if open_requests_tab(driver):
            _scrape_current_folder(
                driver, "requests", max_convos, chat_scrolls,
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
    path = f"{_OUTPUT_PREFIX}_{base}.json"
    if not os.path.exists(path):
        return path
    for n in range(2, 200):
        candidate = f"{_OUTPUT_PREFIX}_{base}_{n}.json"
        if not os.path.exists(candidate):
            return candidate
    return path


def save_to_json(conversations: list, username: str | None = None) -> str:
    """Viewer expects {owner_profile, conversations}. We only fill in username
    if the user passed one via --username (no auto profile scraping)."""
    owner_profile: dict = {}
    if username:
        owner_profile["username"] = username.lstrip("@")

    # Roll-up summary so it's easy to see at a glance
    summary = {"inbox": 0, "requests": 0, "story_reactions": 0}
    for c in conversations:
        if c.get("source") == "requests":
            summary["requests"] += 1
        else:
            summary["inbox"] += 1
        for m in c.get("messages", []):
            if m.get("is_story_reaction"):
                summary["story_reactions"] += 1

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
        description="TikTok DM Parser v6.1 — resume · rate-limited · stealth")
    parser.add_argument("--sessionid",        required=True,
                        help="TikTok sessionid cookie value")
    parser.add_argument("--username",         default=None,
                        help="Your TikTok @handle (optional, e.g. 'someone' or "
                             "'@someone'). Used to build "
                             "https://www.tiktok.com/@<username> for stronger "
                             "login verification, and stored in the output.")
    parser.add_argument("--headless",         action="store_true")
    parser.add_argument("--proxy",            default=None,
                        help="host:port  or  user:pass@host:port")
    parser.add_argument("--max-convos",       type=int, default=0,
                        help="Per-folder cap. 0 = all (default)")
    parser.add_argument("--chat-scrolls",     type=int, default=15)
    parser.add_argument("--include-requests", action="store_true",
                        help="Also scrape the Message Requests folder")
    parser.add_argument("--reset",            action="store_true",
                        help="Wipe saved state and start over")
    args = parser.parse_args()

    print("🚀 TikTok DM Parser v6.1")
    if args.username:
        print(f"👤 Username: @{args.username.lstrip('@')}")

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

    try:
        if not login_with_retry(driver, args.sessionid, args.username):
            print("❌ Login failed after retries. State preserved — try again later.")
            return

        conversations = run_full_scraper(
            driver, args.max_convos, args.chat_scrolls, state,
            include_requests=args.include_requests,
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
        save_to_json(conversations, username=args.username)
    else:
        print("ℹ️  Nothing to save this run.")


if __name__ == "__main__":
    main()
