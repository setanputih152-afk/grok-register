# Decisive check: does the widget container host a (closed?) shadow root with an iframe?
import time
import sys
import json
sys.path.insert(0, ".")

import DrissionPage_example as main_mod
from DrissionPage_example import co

co.set_browser_path("/usr/bin/google-chrome")
co.set_proxy("http://127.0.0.1:1081")
from DrissionPage_example import Chromium
import tempfile
tmp = tempfile.mkdtemp(prefix="chrome_dbg_")
co.set_user_data_path(tmp)
browser = Chromium(co)
page = browser.latest_tab or browser.new_tab()
main_mod.page = page

try:
    from email_register import get_email_and_token
    page.get(main_mod.SIGNUP_URL)
    time.sleep(5)
    main_mod.click_email_signup_button()
    email, dev_token = main_mod.fill_email_and_submit()
    main_mod.fill_code_and_submit(email, dev_token)
    time.sleep(6)

    print("render:", page.run_js("""
const input = document.querySelector('input[name="cf-turnstile-response"]');
const container = input.parentElement.firstElementChild;
try {
    const wid = turnstile.render(container, { sitekey: '0x4AAAAAAAhr9JGVDZbrZOo0', theme: 'light', size: 'flexible', callback: t => { window.__tok = t; } });
    return 'rendered ' + wid;
} catch (e) { return 'err ' + e; }
"""))
    time.sleep(8)
    print(page.run_js("""
const input = document.querySelector('input[name="cf-turnstile-response"]');
const container = input.parentElement.firstElementChild;
const srOpen = container.shadowRoot;
let srInfo = 'closed-or-none';
if (srOpen) {
    srInfo = 'open, children: ' + srOpen.innerHTML.slice(0, 300);
}
return JSON.stringify({
    srInfo: srInfo,
    tok: window.__tok ? window.__tok.slice(0,30) : null,
});
"""))
finally:
    try:
        browser.quit()
    except Exception:
        pass
