# EzSolver-style flow replicated over raw CDP on the working browser:
# navigate to x.ai signup, inject a fixed-position Turnstile widget with the
# real sitekey, click its checkbox at the known (48,52) coords, harvest the
# token, push it into the real form, submit, capture SSO cookie.
import json
import random
import subprocess
import sys
import time

import websocket

sys.path.insert(0, "/tmp/hoplite/workspace")

SITEKEY = "0x4AAAAAAAhr9JGVDZbrZOo0"
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
SSO_DIR = "/tmp/hoplite/workspace/sso"

targets = json.loads(subprocess.run(["curl", "-s", "http://127.0.0.1:9333/json"], capture_output=True, text=True).stdout)
page_ws = next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"]
ws = websocket.create_connection(page_ws, timeout=25, suppress_origin=True)
_id = 0

def send(method, params=None):
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        d = json.loads(ws.recv())
        if d.get("id") == _id:
            return d.get("result", {})

def ev(expr, await_promise=False):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": await_promise})
    res = r.get("result", {})
    if res.get("subtype") == "error":
        return f"__ERR__{res.get('description','')[:120]}"
    return res.get("value")

def mouse(x, y, click=True):
    send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(x), "y": int(y)})
    if click:
        send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})
        send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})

def human_click(x, y):
    mouse(x - 80, y - 20, click=False)
    time.sleep(random.uniform(0.15, 0.3))
    mouse(x, y, click=False)
    time.sleep(random.uniform(0.08, 0.15))
    mouse(x, y)

# ---- load signup page ----
send("Page.enable")
print("[*] navigate:", send("Page.navigate", {"url": SIGNUP_URL}).get("frameId", "?") is not None)
time.sleep(6)
print("[email-btn]", ev("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Sign up with email'); if(!b) return 'no-btn'; b.click(); return 'ok'})()"))
time.sleep(2)

# ---- mailbox ----
from email_register import _create_session, _create_account_on_base, _TOKEN_BASE, get_oai_code
s, cffi = _create_session()
email, _pw, mail_token = _create_account_on_base(s, cffi, "https://api.duckmail.sbs")
_TOKEN_BASE[mail_token] = "https://api.duckmail.sbs"
print("[*] mailbox:", email)

r = ev(f"""(()=>{{const i=document.querySelector("input[type='email']"); if(!i) return 'no-input'; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; i.focus(); set.call(i,''); set.call(i,'{email}'); i.dispatchEvent(new Event('input',{{bubbles:true}})); i.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok'}})()""")
print("[email-fill]", r)
time.sleep(0.5)
print("[email-submit]", ev("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Sign up'); if(!b) return 'no-btn'; b.click(); return 'ok'})()"))

# ---- OTP ----
code = get_oai_code(mail_token, email, timeout=150)
print("[*] otp:", code)
if not code:
    raise SystemExit(1)
end = time.time() + 25
while time.time() < end:
    r = ev(f"""(()=>{{const i=document.querySelector("input"); if(!i) return null; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; i.focus(); set.call(i,''); set.call(i,'{code}'); i.dispatchEvent(new Event('input',{{bubbles:true}})); i.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok'}})()""")
    if r == "ok":
        break
    time.sleep(0.5)
print("[code-fill]", r)
time.sleep(4)
print("[*] page:", ev("document.body.innerText.slice(0,60).replace(/\\n/g,' | ')"))

# ---- profile ----
for name, val in (("input[name='givenName']", "Neo"), ("input[name='familyName']", "Lin"), ("input[type='password']", "Fx8!kQz2#Wr9TvLm")):
    end = time.time() + 25
    while time.time() < end:
        r = ev(f"""(()=>{{const i=document.querySelector("{name}"); if(!i) return null; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; i.focus(); set.call(i,''); set.call(i,'{val}'); i.dispatchEvent(new Event('input',{{bubbles:true}})); i.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok'}})()""")
        if r == "ok":
            break
        time.sleep(0.5)
    print(f"[{name}]", r)

# ---- inject own widget at fixed position ----
print("[inject]", ev(f"""(()=>{{
    if (document.getElementById('_ts_box')) return 'already';
    window._tsToken = null;
    const wrap = document.createElement('div');
    wrap.id = '_ts_box';
    wrap.style = 'position:fixed;top:20px;left:20px;z-index:2147483647;';
    document.body.appendChild(wrap);
    window._tsLoad = function () {{
        turnstile.render('#_ts_box', {{
            sitekey: '{SITEKEY}',
            callback: function(token) {{ window._tsToken = token; }}
        }});
    }};
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=_tsLoad&render=explicit';
    s.async = true;
    document.head.appendChild(s);
    return 'injected';
}})()"""))

def get_ts_token():
    return ev("""(()=>{ if (window._tsToken) return window._tsToken; const inp = document.querySelector('#_ts_box [name="cf-turnstile-response"]'); return (inp && inp.value) ? inp.value : null; })()""")

# give it time to auto-solve (invisible behavior)
for _ in range(10):
    time.sleep(1)
    if get_ts_token():
        break

# click loop at known fixed coords, human-like
deadline = time.time() + 120
clicks = 0
while time.time() < deadline and not get_ts_token():
    if clicks == 0 or clicks % 4 == 0:
        human_click(20 + 28 + random.uniform(-3, 3), 20 + 32 + random.uniform(-3, 3))
    else:
        mouse(20 + 28, 20 + 32)
    clicks += 1
    time.sleep(2)

token = get_ts_token()
print("[*] token:", (token[:40] + "...") if token else "FAILED")

if token:
    # push token into the real form and submit
    print("[sync]", ev(f"""(()=>{{const i=document.querySelector('input[name="cf-turnstile-response"]'); if(!i) return 'no-input'; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(i,'{token}'); i.dispatchEvent(new Event('input',{{bubbles:true}})); i.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok'}})()"""))
    print("[submit]", ev("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('Complete sign up')); if(b){b.click(); return 'ok'} return 'no-btn'})()"))
    sso = None
    for _ in range(45):
        r = send("Storage.getCookies", {})
        for c in r.get("cookies", []):
            if "sso" in (c.get("name") or "").lower():
                sso = c["value"]
                print("[SSO]", c["name"], "=", c["value"][:50])
                break
        if sso:
            break
        time.sleep(2)
    if sso:
        import datetime, os
        os.makedirs(SSO_DIR, exist_ok=True)
        out = os.path.join(SSO_DIR, f"sso_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(out, "a") as f:
            f.write(sso + "\n")
        print("[DONE]", out)
    else:
        print("[*] url:", ev("location.href"), "| body:", ev("document.body.innerText.slice(0,120)"))
