# Grok 账号自动注册工具

基于 DrissionPage 的 Grok (x.ai) 账号自动注册脚本。临时邮箱免 token（duckmail / mail.gw / mail.tm 自动轮换），通过 Chrome 扩展 patch `MouseEvent.screenX/screenY` 绕过 Cloudflare Turnstile，注册完成后可自动推送 SSO token 到 grok2api 号池。

## 环境要求

- Python 3.10+
- Chrome / Chromium（推荐 playwright 版，见下方安装）
- 无需任何 API key 或 bearer token

## 安装（从零开始）

```bash
git clone https://github.com/setanputih152-afk/grok-register.git
cd grok-register

# 虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate

# 依赖
pip install -r requirements.txt

# Chromium（推荐 playwright 版，避开 snap 版 AppArmor 限制）
pip install playwright
python3 -m playwright install chromium
python3 -m playwright install-deps chromium   # 需要 sudo 时按提示执行

# 配置（tokenless 邮箱模式下无需改动任何字段）
cp config.example.json config.json
```

## 运行

```bash
python DrissionPage_example.py --count 3
```

- Chrome 窗口弹出后全自动：临时邮箱 → 填邮箱 → 收 OTP → 填资料
- 出现 Turnstile 复选框时：脚本自动点击；若提示手动，用鼠标点击复选框（窗口 ~180 秒）
- SSO token 输出到 `sso/sso_<时间戳>.txt`，每行一个

## 配置（config.json）

| 字段 | 说明 |
|------|------|
| `run.count` | 注册轮数，`0` 为无限循环（可用 `--count` 覆盖） |
| `duckmail_api_base` | 临时邮箱 API 地址（默认 duckmail，失败自动换 mail.gw / mail.tm） |
| `duckmail_bearer` | **已不需要**，保留字段仅为兼容 |
| `proxy` | 临时邮箱 API 请求代理（可选） |
| `browser_proxy` | 浏览器代理（可选，datacenter IP 被拒时填住宅/手机代理） |
| `api.endpoint` / `api.token` | grok2api 管理接口（可选，用于推送 token） |
| `api.append` | `true` 合并线上已有 token |

## 无头服务器注意

- 脚本自动启用 Xvfb 虚拟显示器
- 不要用 snap 版 chromium（AppArmor 限制），用 playwright 版
- datacenter IP 可能被 Cloudflare 拒绝 —— 需要住宅/手机代理时填 `browser_proxy`

## 输出文件

```
sso/sso_<时间戳>.txt   ← SSO token（每行一个）
logs/run_<时间戳>.log  ← 每轮注册日志
```

## 文件结构

```
├── DrissionPage_example.py   # 主脚本
├── email_register.py         # 临时邮箱（免 token，三提供商轮换）
├── turnstilePatch/           # Chrome 扩展（Turnstile 点击补丁）
├── config.example.json       # 配置模板
└── requirements.txt
```
