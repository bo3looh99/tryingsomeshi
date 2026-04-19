"""
TikTok DM Parser v5.0
- Stealth: undetected-chromedriver (no bot fingerprint)
- Proxy: --proxy host:port or user:pass@host:port
- Profile data: pulled from TikTok's internal JSON APIs (not HTML scraping)
- Output: { owner_profile, conversations } JSON
"""

import argparse
import json
import os
import random
import re
import time
from datetime import datetime

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ─────────────────────────────────────────────────────────────────────────────
#  Stealth driver setup
# ─────────────────────────────────────────────────────────────────────────────

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def build_driver(headless: bool, proxy: str | None) -> uc.Chrome:
    options = uc.ChromeOptions()

    # Realistic window — always set even in headless so viewport fingerprint matches
    options.add_argument("--window-size=1440,900")
    options.add_argument(f"--user-agent={_UA}")
    options.add_argument("--lang=en-US,en;q=0.9")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    # Enable performance logging for CDP network capture
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if proxy:
        # Accepts: host:port  or  user:pass@host:port  or  http://...
        if not proxy.startswith("http"):
            proxy = f"http://{proxy}"
        options.add_argument(f"--proxy-server={proxy}")
        print(f"🔒 Proxy: {proxy}")

    driver = uc.Chrome(
        options=options,
        headless=headless,
        use_subprocess=True,
    )

    # Mask a few extra automation tells via JS
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = { runtime: {} };
        """
    })

    return driver


def _jitter(lo=0.8, hi=2.2):
    """Human-like random delay."""
    time.sleep(random.uniform(lo, hi))


# ─────────────────────────────────────────────────────────────────────────────
#  TikTok internal API helpers  (requests, no browser needed)
# ─────────────────────────────────────────────────────────────────────────────

_API_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}


def _api_session(sessionid: str, extra_cookies: dict | None = None) -> requests.Session:
    sess = requests.Session()
    sess.cookies.set("sessionid", sessionid, domain=".tiktok.com")
    if extra_cookies:
        for k, v in extra_cookies.items():
            sess.cookies.set(k, v, domain=".tiktok.com")
    return sess


def _wait_text(driver, css, timeout=10) -> str | None:
    """Wait for an element and return its text, or None on failure."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
        return el.text.strip() or None
    except Exception:
        return None


def _body_text(driver) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return ""


def scrape_owner_profile(driver, sessionid: str, debug: bool = False) -> dict:
    profile = {
        "username":        None,
        "nickname":        None,
        "region":          None,
        "store_region":    None,
        "video_count":     None,
        "followers_count": None,
        "following_count": None,
        "account_created": None,
        "app_store":       "unknown",
        "phone_masked":    None,
        "email_masked":    None,
        "avatar_url":      None,
        "scraped_at":      datetime.now().isoformat(),
    }

    # ── Step 1: get username from nav DOM ─────────────────────────────────
    print("   🔍 Detecting username from nav...")
    try:
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/@"]')
        for link in links:
            href = link.get_attribute("href") or ""
            if "/@" in href:
                slug = href.split("/@")[-1].split("?")[0].strip()
                if slug and slug not in ("following", "followers", "explore"):
                    profile["username"] = slug
                    break
    except Exception as e:
        print(f"   ⚠️  Nav detection failed: {e}")

    if not profile["username"]:
        print("   ⚠️  Could not detect username — skipping profile scrape")
        return profile

    username = profile["username"]
    print(f"   ✅ @{username}")

    # ── Step 2: profile page → nickname, avatar, followers, following, videos ─
    print(f"   🌐 Visiting /@{username}...")
    driver.get(f"https://www.tiktok.com/@{username}")
    _jitter(3, 5)

    profile["nickname"]        = _wait_text(driver, '[data-e2e="user-title"]')
    profile["followers_count"] = _wait_text(driver, '[data-e2e="followers-count"]')
    profile["following_count"] = _wait_text(driver, '[data-e2e="following-count"]')
    profile["video_count"]     = _wait_text(driver, '[data-e2e="user-post-count"]')

    # Avatar from the profile page img
    try:
        av_el = driver.find_element(
            By.CSS_SELECTOR, '[data-e2e="user-avatar"] img, img[class*="ImgAvatar"]'
        )
        profile["avatar_url"] = av_el.get_attribute("src") or None
    except Exception:
        pass

    print(f"   ✅ nickname={profile['nickname']}  "
          f"followers={profile['followers_count']}  videos={profile['video_count']}")

    # ── Step 3: settings page → phone, email, region, store ───────────────
    print("   🌐 Visiting /setting/...")
    driver.get("https://www.tiktok.com/setting/")
    _jitter(3, 5)

    body = _body_text(driver)

    if debug:
        with open("tiktok_debug_settings.txt", "w", encoding="utf-8") as f:
            f.write(body)
        print("   🐛 Settings body → tiktok_debug_settings.txt")

    # Phone — try data-e2e first, then pattern in body text
    profile["phone_masked"] = _wait_text(driver, '[data-e2e="setting-phone"]', timeout=4)
    if not profile["phone_masked"]:
        m = re.search(r'(\+[\d*\s]{6,})', body)
        if m:
            profile["phone_masked"] = m.group(1).strip()

    # Email
    profile["email_masked"] = _wait_text(driver, '[data-e2e="setting-email"]', timeout=4)
    if not profile["email_masked"]:
        m = re.search(r'([a-zA-Z*][\w*.]*@[\w.*]+\.[a-z]{2,})', body)
        if m:
            profile["email_masked"] = m.group(1)

    # Region
    profile["region"] = _wait_text(driver, '[data-e2e="setting-region"]', timeout=4)
    if not profile["region"]:
        m = re.search(r'Region\s*\n([^\n]{2,40})', body)
        if m:
            profile["region"] = m.group(1).strip()

    # Store region / app store platform
    bl = body.lower()
    if "app store" in bl or " ios" in bl:
        profile["app_store"] = "iOS App Store"
    elif "google play" in bl or "android" in bl:
        profile["app_store"] = "Google Play"

    # Store region code (e.g. "AE") — scan for a labeled region field
    m = re.search(r'[Ss]tore\s+[Rr]egion\s*[:\-]?\s*([A-Z]{2,3})', body)
    if m:
        profile["store_region"] = m.group(1)

    print(f"   ✅ phone={profile['phone_masked']}  email={profile['email_masked']}  "
          f"region={profile['region']}  store={profile['app_store']}")

    # ── Step 4: account-and-security → creation date ──────────────────────
    print("   🌐 Visiting /setting/account-and-security/...")
    driver.get("https://www.tiktok.com/setting/account-and-security/")
    _jitter(4, 6)

    sec_body = _body_text(driver)

    if debug:
        with open("tiktok_debug_security.txt", "w", encoding="utf-8") as f:
            f.write(sec_body)
        print("   🐛 Security body → tiktok_debug_security.txt")

    date_patterns = [
        r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|'
        r'Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b',
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{1,2}\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|'
        r'Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|'
        r'Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b',
    ]
    for pat in date_patterns:
        m = re.search(pat, sec_body, re.IGNORECASE)
        if m:
            profile["account_created"] = m.group(0).strip()
            break

    print(f"   ✅ account_created={profile['account_created']}")

    # ── Step 5: download owner avatar ─────────────────────────────────────
    if profile.get("avatar_url"):
        av_path = _download_avatar(profile["avatar_url"], f"owner_{username}")
        profile["avatar_path"] = av_path
        if av_path:
            print(f"   🖼️  Owner avatar → {av_path}")

    if debug:
        with open("tiktok_debug_profile.json", "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        print("   🐛 Profile dump → tiktok_debug_profile.json")

    return profile


# ─────────────────────────────────────────────────────────────────────────────
#  Avatar helpers
# ─────────────────────────────────────────────────────────────────────────────

_AVATAR_DIR = "tiktok_avatars"


def _download_avatar(url: str, filename: str) -> str | None:
    os.makedirs(_AVATAR_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", filename) + ".jpg"
    path = os.path.join(_AVATAR_DIR, safe)
    if os.path.exists(path):
        return path  # already downloaded
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception as e:
        print(f"   ⚠️  Avatar download failed: {e}")
    return None


def _get_chat_avatar_url(driver) -> str | None:
    """
    Find the chat PARTNER's avatar — looks inside the chat header only,
    so it never accidentally picks up the logged-in user's nav avatar.
    """
    # Most specific first: chat header / conversation header containers
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

    # Fallback: grab ALL avatar images, skip any that appear in the nav/sidebar
    try:
        imgs = driver.find_elements(By.CSS_SELECTOR, "img[class*='ImgAvatar']")
        # TikTok renders the nav avatar first — skip first result if >1 found
        candidates = [
            img.get_attribute("src") for img in imgs
            if (img.get_attribute("src") or "").startswith("http")
            and "tiktok" in (img.get_attribute("src") or "")
        ]
        # Return the last one — nav avatar is typically first in DOM order
        if candidates:
            return candidates[-1]
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Chat message extractor
# ─────────────────────────────────────────────────────────────────────────────

def extract_chat_history(driver, scroll_times: int = 15) -> list:
    messages = []
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        chat_container = driver.find_element(By.CSS_SELECTOR, "div[class*='DivMessage']")
        print("   ✅ Chat container found")

        print(f"   📜 Scrolling up {scroll_times} times...")
        for _ in range(scroll_times):
            driver.execute_script("arguments[0].scrollTop = 0;", chat_container)
            time.sleep(random.uniform(1.4, 2.2))

        msg_elements = driver.find_elements(By.CSS_SELECTOR, "div[data-e2e*='message']")
        print(f"   ✅ Found {len(msg_elements)} messages")

        for msg in msg_elements:
            try:
                text = msg.text.strip()
                if not text:
                    continue
                classes = msg.get_attribute("class") or ""
                is_me = any(k in classes.lower() for k in ["right", "my", "self", "sender-me"])

                timestamp = ""
                time_elems = msg.find_elements(By.CSS_SELECTOR, "span[class*='time'], small")
                if time_elems:
                    timestamp = time_elems[0].text.strip()

                messages.append({
                    "is_me":      is_me,
                    "sender":     "Me" if is_me else "Them",
                    "text":       text,
                    "timestamp":  timestamp or "—",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️  Chat issue: {e}")

    print(f"   ✅ Extracted {len(messages)} messages")
    return messages


# ─────────────────────────────────────────────────────────────────────────────
#  Conversation scraper loop
# ─────────────────────────────────────────────────────────────────────────────

def run_full_scraper(driver, max_convos: int, chat_scrolls: int) -> list:
    print("📜 Scrolling inbox to load conversations...")
    for _ in range(8):
        driver.execute_script("window.scrollBy(0, 1200);")
        _jitter(0.8, 1.4)

    all_convos = []
    limit = max_convos if max_convos > 0 else 9999

    i = 0
    while i < limit:
        # Re-find list every iteration — avoids stale element errors
        conv_items = driver.find_elements(
            By.CSS_SELECTOR, "div[class*='DivItemInfo'], div[role='listitem']"
        )
        if i >= len(conv_items):
            print(f"   All {len(conv_items)} conversations processed.")
            break

        try:
            item = conv_items[i]
            raw_text = item.text.strip()
            lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
            username = lines[0] if lines else f"conv_{i+1}"

            print(f"\n📨 [{i+1}] Opening → {username}")
            item.click()
            _jitter(5, 7)

            messages = extract_chat_history(driver, chat_scrolls)

            avatar_url  = _get_chat_avatar_url(driver)
            avatar_path = None
            if avatar_url:
                avatar_path = _download_avatar(avatar_url, username)
                if avatar_path:
                    print(f"   🖼️  Avatar → {avatar_path}")

            all_convos.append({
                "username":      username,
                "avatar_url":    avatar_url,
                "avatar_path":   avatar_path,
                "messages":      messages,
                "message_count": len(messages),
            })

            driver.get("https://www.tiktok.com/messages")
            _jitter(4, 6)

        except Exception as e:
            print(f"   ❌ Skipped conversation {i+1}: {e}")
            _jitter(1.5, 3)

        i += 1

    return all_convos


# ─────────────────────────────────────────────────────────────────────────────
#  JSON output
# ─────────────────────────────────────────────────────────────────────────────

def build_output_path() -> str:
    base = datetime.now().strftime("%Y-%m-%d")
    path = f"tiktok_dms_full_{base}.json"
    if not os.path.exists(path):
        return path
    for n in range(2, 200):
        candidate = f"tiktok_dms_full_{base}_{n}.json"
        if not os.path.exists(candidate):
            return candidate
    return path


def save_to_json(owner_profile: dict, conversations: list) -> str:
    output = {"owner_profile": owner_profile, "conversations": conversations}
    filename = build_output_path()
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 ✅ Saved {len(conversations)} conversations → {filename}")
    return filename


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TikTok DM Parser v5.0 — stealth · proxy · API-based profile"
    )
    parser.add_argument("--sessionid",    required=True,  help="TikTok sessionid cookie value")
    parser.add_argument("--headless",     action="store_true", help="Run Chrome headless (invisible)")
    parser.add_argument("--proxy",        default=None,   help="Proxy: host:port  or  user:pass@host:port")
    parser.add_argument("--max-convos",   type=int, default=0,  help="0 = all conversations")
    parser.add_argument("--chat-scrolls", type=int, default=15, help="Scroll passes per chat")
    parser.add_argument("--skip-profile", action="store_true",  help="Skip owner profile scrape")
    parser.add_argument("--debug",        action="store_true",  help="Dump API responses to debug files")
    args = parser.parse_args()

    print("🚀 TikTok DM Parser v5.0")
    driver = build_driver(args.headless, args.proxy)

    try:
        # ── Login ──────────────────────────────────────────────────────────
        print("\n🌐 Setting session cookie...")
        driver.get("https://www.tiktok.com")
        _jitter(2, 3)
        driver.add_cookie({
            "name":   "sessionid",
            "value":  args.sessionid,
            "domain": ".tiktok.com",
            "path":   "/",
        })
        driver.refresh()
        _jitter(4, 6)

        # ── Navigate to messages first (page source has embedded user JSON) ──
        print("📨 Going to Messages...")
        driver.get("https://www.tiktok.com/messages")
        _jitter(8, 12)

        # ── Owner profile — extracted from messages page source + APIs ────────
        owner_profile = {}
        if not args.skip_profile:
            print("\n👤 Scraping owner profile...")
            owner_profile = scrape_owner_profile(driver, args.sessionid, debug=args.debug)
            print("✅ Profile scrape complete.\n")
        else:
            print("⏭️  Skipping profile scrape (--skip-profile)\n")

        conversations = run_full_scraper(driver, args.max_convos, args.chat_scrolls)

    finally:
        driver.quit()

    if conversations or owner_profile:
        save_to_json(owner_profile, conversations)
    else:
        print("❌ No data found.")


if __name__ == "__main__":
    main()
