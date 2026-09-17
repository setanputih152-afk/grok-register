# Grok Account Registration — Portable Package

Register Grok (x.ai) accounts otomatis: temp mail tokenless -> email -> OTP ->
profil -> Turnstile -> SSO token. Gratis, tanpa API key.

## Kenapa harus di PC sendiri

Turnstile menolak terbit token di environment headless (VPS/server tanpa GPU):
fingerprint browser + GPU asli + IP rumah adalah syarat skor kepercayaannya.
Semua langkah lain sudah teruji; jalankan ini di PC/laptop rumah dengan
Chrome ter-install dan IP rumah (bukan VPN/VPS), dan challenge-nya
auto-pass seperti yang terlihat pada mode demo.

## Isi

- `register.py`      — flow utama (patchright/Playwright, klik Turnstile human-like)
- `email_register.py` — temp mail tokenless (duckmail / mail.gw / mail.tm, rotasi otomatis)
- `solver.py` / `service.py` — EzSolver (MIT) sebagai fallback solver mandiri
- `config.example.json` — template config
- `requirements.txt` — dependencies

## Setup (sekali)

```bash
# 1. Python 3.10+
pip install -r requirements.txt
playwright install chromium

# 2. Config
cp config.example.json config.json
#   - run.count: jumlah akun (0 = loop terus)
#   - biarkan duckmail_api_base default; tidak perlu bearer token

# 3. Jalankan
python register.py --count 5
```

SSO token terkumpul di `sso/sso_<timestamp>.txt` (satu per baris).

## Catatan Turnstile

- Jangan jalankan dengan `--headless`; biarkan jendela Chrome muncul (headful).
- GPU harus aktif (bukan VM tanpa GPU). Kalau di server: pakai `--headed` + Xvfb
  tetap bisa, tapi tingkat sukses turun.
- Kalau challenge minta klik manual, `register.py` otomatis klik checkbox-nya
  dengan gerakan mouse human-like; kalau tetap minta manusia, cukup klik
  sekali di jendela yang muncul — flow lanjut sendiri.
- IP: pakai WiFi rumah. VPN/datacenter/IP bersama = verification failed.
- Fallback mandiri kalau widget di halaman utama bandel:
  `python solver.py 0x4AAAAAAAhr9JGVDZbrZOo0 https://accounts.x.ai/sign-up`
  (token disalin otomatis oleh register.py via `service.py`, port 8191).
