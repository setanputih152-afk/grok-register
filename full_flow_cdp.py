# Full Grok signup flow driven entirely over raw CDP with sub-second latency:
# temp mail -> email submit -> OTP poll -> code confirm -> profile fill ->
# turnstile checkbox click inside the 25s interaction window -> SSO capture.
import json
import subprocess
import sys
import time

import websocket

sys.path.insert(0, "/tmp/hoplite/workspace")
from email_register import get_oai_code  # noqa: E402

CDP_HTTP = "http://127.0.0.1:9333"
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
SSO_DIR = "/tmp/hoplite/workspace/sso"

ws = websocket.create_connection(
    [t for t in json.loads(subprocess.run(["curl", "-s", f"{CDP_HTTP}/json"], capture_output=True, text=True).stdout) if t["type"] == "page"][0]["webSocketDebuggerUrl"],
    timeout=20, suppress_origin=True,
)
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
        return None
    return res.get("value")

def click_js(selector_contains, exact=False):
    mode = "===" if exact else "includes"
    return ev(f"""(()=>{{
        const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim(){mode} '{selector_contains}');
        if(!b) return 'no-btn';
        b.click(); return 'ok';
    }})()""")

def fill(selector, value):
    sel = json.dumps(selector)
    val = json.dumps(value)
    return ev(f"""(()=>{{
        const i=document.querySelector({sel});
        if(!i) return 'no-input';
        const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        i.focus(); set.call(i,''); set.call(i,{val});
        i.dispatchEvent(new Event('input',{{bubbles:true}}));
        i.dispatchEvent(new Event('change',{{bubbles:true}}));
        return 'ok';
    }})()""")

def fill_wait(selector, value, timeout=15, poll=0.5):
    end = time.time() + timeout
    r = None
    while time.time() < end:
        r = fill(selector, value)
        if r == "ok":
            return "ok"
        time.sleep(poll)
    return r

def mouse_click(x, y):
    send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(x), "y": int(y)})
    send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})
    send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})

def wait_url_or_heading(h, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        h2 = ev("document.body.innerText.slice(0,400)")
        if h2 and h in h2:
            return True
        time.sleep(0.5)
    return False

# ---- step 1: fresh page ----
# capture turnstile render/error lifecycle (also empirically correlates with
# the widget actually mounting on x.ai)
send("Page.enable")
send("Page.addScriptToEvaluateOnNewDocument", {"source": open("/tmp/ts_hook.js").read()})
# swiftshader's "SwiftShader"/software renderer string is a bot signal for
# the challenge's WebGL adapter probe; report a real desktop GPU instead
send("Page.addScriptToEvaluateOnNewDocument", {"source": """
const REAL_GPU = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
for (const Ctor of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!Ctor) continue;
    const orig = Ctor.prototype.getParameter;
    Ctor.prototype.getParameter = function(p) {
        if (p === 37445) return REAL_GPU;
        if (p === 37446) return REAL_GPU;
        return orig.call(this, p);
    };
    const getExt = Ctor.prototype.getExtension;
    Ctor.prototype.getExtension = function(name) {
        const ext = getExt.call(this, name);
        if (ext && name === 'WEBGL_debug_renderer_info') {
            return {UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446};
        }
        return ext;
    };
}
"""})
print("[*] navigating")
send("Page.navigate", {"url": SIGNUP_URL})
time.sleep(5)
print("[email-btn]", click_js("Sign up with email", exact=True))
# OneTrust overlay can sit above the challenge iframe; dismiss it early
ev("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^(Allow All|Accept All|Dismiss)/.test(x.textContent.trim())); if(b) b.click();})()""")

# ---- step 2: temp mailbox ----
from email_register import _create_session, _create_account_on_base, _TOKEN_BASE
s, cffi = _create_session()
email, _pw, mail_token = _create_account_on_base(s, cffi, "https://api.duckmail.sbs")
_TOKEN_BASE[mail_token] = "https://api.duckmail.sbs"
print("[*] mailbox:", email)

if wait_url_or_heading("Sign up with your email", 20) is False:
    print("[warn] email form not detected, continuing anyway")

r = fill_wait("input[type='email']", email, 20)
print("[email-fill]", r)
time.sleep(0.5)
print("[email-submit]", click_js("Sign up", exact=True))

# ---- step 3: OTP ----
code = get_oai_code(mail_token, email, timeout=150)
print("[*] otp:", code)
if not code:
    raise SystemExit("no otp")

wait_url_or_heading("Verify your email", 30)
r = fill_wait("input", code, 20)
print("[code-fill]", r)
time.sleep(1)
# confirm button if present
ev("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('Confirm email')); if(b) b.click();})()")
time.sleep(4)

# ---- step 4: profile ----
wait_url_or_heading("Complete your sign up", 30)
print("[*] profile page")
print("[given]", fill_wait("input[name='givenName']", "Neo", 30))
print("[family]", fill_wait("input[name='familyName']", "Lin", 15))
print("[pass]", fill_wait("input[type='password']", "Tq9!wXr5#kPz2VbN", 15))
time.sleep(1)

# ---- step 5: turnstile interaction window ----
# The challenge iframe lives inside a closed shadow root (invisible to
# querySelectorAll) and waits ~25s for a checkbox click before erroring 600010
# and cycling. Sweep a grid of candidate points across the widget container
# every cycle; one lands on the checkbox.
token = ""
deadline = time.time() + 240
clicks = 0
submitted = 0
GRID = [(16, 36), (30, 36), (46, 36), (16, 20), (30, 20), (30, 52), (60, 36), (100, 36)]

def sweep_tick():
    global token
    rect = ev("""(()=>{const i=document.querySelector('input[name="cf-turnstile-response"]'); if(!i) return null; const d=i.parentElement.firstElementChild; if(!d) return null; const r=d.getBoundingClientRect(); if(r.width<10) return null; return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});})()""")
    if rect:
        b = json.loads(rect)
        for gx, gy in GRID:
            mouse_click(b["x"] + gx, b["y"] + gy)
            time.sleep(0.12)
        return True
    return False

deadline2 = time.time() + 240
sweep_round = 0
while time.time() < deadline2 and not token:
    token = ev("(()=>{const i=document.querySelector('input[name=\"cf-turnstile-response\"]'); return i?i.value:''})()") or ""
    if token:
        break
    if sweep_tick():
        sweep_round += 1
        clicks += len(GRID)
        print(f"[sweep {sweep_round}] {clicks} clicks, tok={token[:15]}", flush=True)
        time.sleep(2)
    else:
        # container missing/empty: nudge submit to re-trigger the widget
        if sweep_round % 6 == 5:
            click_js("Complete sign up", exact=False)
        time.sleep(1.5)

print("[*] turnstile token:", (token[:40] + "...") if token else "FAILED")
print("LOG:", ev("JSON.stringify(window.__tsLog||[])"))
if not token:
    raise SystemExit(1)

# ---- step 6: submit + SSO ----
print("[submit]", click_js("Complete sign up", exact=True))
sso = None
for _ in range(45):
    r = send("Storage.getCookies", {})
    for c in r.get("cookies", []):
        n = (c.get("name") or "").lower()
        if "sso" in n:
            sso = c["value"]
            print("[SSO]", c["name"], "=", c["value"][:60])
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
    print("[DONE] saved", out)
else:
    print("[*] no sso cookie; url:", ev("location.href"), "body:", ev("document.body.innerText.slice(0,200)"))
