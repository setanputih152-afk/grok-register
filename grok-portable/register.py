# Grok SSO registration — headful, runs on a real desktop with home IP.
# Flow: temp mail (tokenless) -> email submit -> OTP -> profile -> Turnstile
# (human-like checkbox click) -> submit -> capture SSO cookie -> optional
# push to a grok2api pool.
from __future__ import annotations

import argparse
import datetime
import json
import random
import secrets
import sys
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

from email_register import get_email_and_token, get_oai_code

HERE = Path(__file__).parent
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
CONFIG = HERE / "config.json"


def conf() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {}


def click_email_signup(page, timeout_s=20):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for text in ("Sign up with email", "使用邮箱注册"):
            loc = page.locator(f"button:has-text('{text}')").first
            try:
                if loc.is_visible():
                    loc.click()
                    return True
            except Exception:
                continue
        time.sleep(0.5)
    raise RuntimeError("email signup button not found")


def fill_field(page, selector: str, value: str, timeout_s=25) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        loc = page.locator(selector).first
        try:
            if loc.is_visible():
                loc.click()
                loc.fill(value)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def submit_button(page, texts: list[str]):
    for text in texts:
        try:
            loc = page.locator(f"button:has-text('{text}')").first
            if loc.is_visible() and loc.is_enabled():
                loc.click()
                return True
        except Exception:
            continue
    return False


def solve_turnstile(page, timeout_s=90):
    """Wait for the widget token; click the checkbox with human-like movement."""
    deadline = time.time() + timeout_s
    clicked = 0
    while time.time() < deadline:
        token = page.evaluate("() => { try { return turnstile.getResponse() } catch(e) { return null } }")
        if token:
            return token
        for frame in page.frames:
            if "challenges.cloudflare.com" not in (frame.url or ""):
                continue
            try:
                cb = frame.locator("input[type='checkbox']").first
                if cb.count() > 0:
                    box = cb.bounding_box()
                    if box:
                        # human-like: approach from a distance, small jitter
                        tx = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
                        ty = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                        page.mouse.move(tx - random.randint(60, 120), ty - random.randint(15, 30))
                        page.wait_for_timeout(random.randint(120, 260))
                        page.mouse.move(tx, ty)
                        page.wait_for_timeout(random.randint(80, 160))
                        page.mouse.down()
                        page.wait_for_timeout(random.randint(40, 90))
                        page.mouse.up()
                        clicked += 1
                        print(f"    [turnstile] click #{clicked}")
            except Exception:
                pass
        time.sleep(2)
    return None


def run_round(context, page, output_path: Path) -> dict:
    page.goto(SIGNUP_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    # dismiss cookie notice if present
    for label in ("Accept All Cookies", "Allow All", "Reject All"):
        try:
            b = page.locator(f"button:has-text('{label}')").first
            if b.is_visible():
                b.click()
                break
        except Exception:
            continue

    click_email_signup(page)

    email, dev_token = get_email_and_token()
    if not email:
        raise RuntimeError("temp mailbox failed")
    print(f"[*] mailbox: {email}")

    if not fill_field(page, "input[type='email']", email):
        raise RuntimeError("email input not found")
    if not submit_button(page, ["Sign up", "继续", "Continue"]):
        raise RuntimeError("email submit not found")

    code = get_oai_code(dev_token, email, timeout=150)
    if not code:
        raise RuntimeError("OTP not received")
    print(f"[*] otp: {code}")

    # code page: single input or OTP boxes
    if not fill_field(page, "input", code, timeout_s=25):
        boxes = page.locator("input[maxlength='1']")
        for i in range(6):
            boxes.nth(i).click()
            boxes.nth(i).fill(code[i])
    time.sleep(2)
    submit_button(page, ["Confirm email", "确认邮箱", "Continue", "Verify"])

    # profile
    given, family = "Neo", "Lin"
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    for sel, val in (("input[name='givenName']", given),
                     ("input[name='familyName']", family),
                     ("input[type='password']", password)):
        if not fill_field(page, sel, val):
            raise RuntimeError(f"profile field missing: {sel}")

    print("[*] solving Turnstile...")
    token = solve_turnstile(page)
    if not token:
        print("[!] Turnstile needs a human click — click the checkbox in the browser window.")
        token = solve_turnstile(page, timeout_s=120)
        if not token:
            raise RuntimeError("turnstile not solved")

    if not submit_button(page, ["Complete sign up", "完成注册", "Create Account"]):
        raise RuntimeError("final submit not found")

    sso = None
    for _ in range(45):
        for c in context.cookies():
            if "sso" in (c.get("name") or "").lower():
                sso = c["value"]
                break
        if sso:
            break
        time.sleep(2)
    if not sso:
        raise RuntimeError("sso cookie not found")

    output_path.parent.mkdir(exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(sso + "\n")

    print(f"[+] registered: {email}")
    return {"email": email, "password": password, "sso": sso}


def push_to_api(tokens: list[str], cfg: dict):
    api = cfg.get("api") or {}
    endpoint, token = api.get("endpoint"), api.get("token")
    if not endpoint or not token:
        return
    try:
        import requests as rq
        r = rq.post(endpoint, json={"token": token, "tokens": tokens}, timeout=30)
        print(f"[*] pushed {len(tokens)} tokens to {endpoint}: {r.status_code}")
    except Exception as e:
        print(f"[!] push failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Grok registration (headful, real desktop)")
    parser.add_argument("--count", type=int, default=None, help="number of accounts (0 = infinite; default from config)")
    parser.add_argument("--output", default=str(HERE / "sso" / f"sso_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"))
    args = parser.parse_args()

    cfg = conf()
    count = args.count if args.count is not None else int((cfg.get("run") or {}).get("count", 10))

    Path(HERE / "sso").mkdir(exist_ok=True)
    Path(HERE / "logs").mkdir(exist_ok=True)

    collected: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,  # Turnstile needs a real, visible browser context
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page = context.new_page()

        i = 0
        while True:
            if count > 0 and i >= count:
                break
            i += 1
            print(f"[*] round {i}" + (f"/{count}" if count else ""))
            try:
                r = run_round(context, page, Path(args.output))
                collected.append(r["sso"])
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[!] round {i} failed: {e}")
            try:
                context.clear_cookies()
            except Exception:
                pass
            if count == 0 or i < count:
                time.sleep(random.randint(3, 8))
        browser.close()

    print(f"[*] done: {len(collected)} tokens")
    push_to_api(collected, cfg)


if __name__ == "__main__":
    main()
