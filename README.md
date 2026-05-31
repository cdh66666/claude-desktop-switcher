# Claude Desktop Switcher

[English](#english) · [中文](#中文)

> Windows 桌面工具：一键切换 Claude Desktop 多账号 + 实时用量监控
> Windows desktop tool to switch between multiple Claude Desktop accounts with live usage monitoring.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows-green.svg)
![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-yellow.svg)

---

## 中文

### 功能

- 🔀 **多账号一键切换** — 按 profile 交换 Claude Desktop 的会话文件，保留每个账号的登录状态
- 📊 **实时用量监控** — 解密本地 OAuth token 调用 `api.anthropic.com`，从响应头读取 5h / 7d 限额使用率
- ⏰ **重置时间** — 显示具体时间点（"今晚 21:00 重置"），不是抽象的倒计时
- 🟢 **状态指示灯** — 卡片左侧圆点根据用量自动变色（绿/橙/红）
- 🔍 **筛选** — 顶部一键切换"全部 / 可用 / 已耗尽"
- 🎨 **Claude.ai 风格** — 米白底 + 珊瑚橙强调色 + Win11 Fluent 标题栏 + 圆角窗口
- 🔒 **本地操作** — 所有切换都在本地完成，不会上传任何信息到第三方服务器
- 🪶 **单实例** — 重复启动会唤醒已有窗口，托盘常驻

### 截图

![界面截图](docs/screenshots/main.png)

### 安装

#### 方式 1：下载 exe（推荐）

从 [Releases](../../releases) 下载 `ClaudeDesktopSwitcher.exe`，双击即可运行。无需 Python。

可选：双击 `scripts\创建快捷方式.bat` 在开始菜单和桌面创建快捷方式。

#### 方式 2：从源码运行

需要 Python 3.10+：

```bash
git clone https://github.com/Picadoo/claude-desktop-switcher.git
cd claude-desktop-switcher
pip install -r requirements.txt
python claude_switcher.py
```

或双击 `scripts\安装依赖.bat` → `scripts\启动.bat`。

### 首次使用

1. 在 Claude Desktop 登录第 1 个账号
2. 启动 Switcher，点击「**＋ 保存当前登录为新 Profile**」，输入名称
3. 点「**🔑 登录其他账号**」 → Claude Desktop 自动重启并显示登录页
4. 登录第 2 个账号 → 重复步骤 2 保存
5. 之后切换：直接点卡片上的「切换」按钮

⚠️ **重要**：在 Claude Desktop 里点"退出登录"会让服务端吊销 token。请只通过 Switcher 切换账号，不要手动退出登录。

### 工作原理

Claude Desktop 把会话状态分散在 `%APPDATA%\Claude\` 下的多个文件里：
- `config.json`（含加密的 OAuth token）
- `Local Storage`、`Session Storage`、`Network`、`IndexedDB` 等

Switcher 把这些**会话相关**的文件按 profile 隔离保存到 `~/.claude-instances/<name>/`，切换时交换文件而不是改任何系统状态。

**用量数据**：通过 Windows DPAPI + AES-GCM 解密 `oauth:tokenCache` 拿到 OAuth token，调用 `api.anthropic.com/v1/messages`（消耗 ~1 token），从响应头读取：
- `anthropic-ratelimit-unified-5h-utilization` — 5 小时窗口百分比
- `anthropic-ratelimit-unified-5h-reset` — 5h 重置时间戳
- `anthropic-ratelimit-unified-7d-utilization` / `-reset` — 7 天窗口

刷新策略：
- 启动时刷新一次（无缓存的话）
- 切换账号时自动刷新（旧账号 + 新账号）
- 手动点刷新按钮（30 秒节流，避免误触）
- 之后靠本地时间戳显示重置时间，**不再调 API**

### 安全提示

- 备注里**不要存明文密码**！备注以明文 JSON 保存在 `~/.claude-instances/_profiles.json`
- 多个 Pro 账号建议用**不同支付方式**注册，避免 Anthropic 关联封号
- 别用本工具做 API 自动化轮询，可能触发滥用检测
- 本工具完全在本地运行，没有任何外部上报

### 限制

- 仅 Windows（用了 DPAPI 解密、Win32 API）
- 切换账号需要完全关闭 Claude Desktop
- 切换是文件级别的，不能"在账号 B 里继续账号 A 的对话"（对话存在 Anthropic 服务端，跟账号绑定）

### 致谢

灵感来源：[NeezerGu/claude-profile-switcher](https://github.com/NeezerGu/claude-profile-switcher)（PowerShell 版）

本项目用 Python 重写，添加了 GUI、用量监控、筛选、单实例等功能。

### License

MIT

---

## English

### Features

- 🔀 **One-click account switching** — swaps Claude Desktop session files per profile, keeps each account's login state intact
- 📊 **Live usage monitoring** — decrypts local OAuth token, calls `api.anthropic.com` to read 5h / 7d rate limits from response headers
- ⏰ **Reset times** — shows actual times like "tonight 21:00", not abstract countdowns
- 🟢 **Status indicator** — colored dots auto-update based on usage (green/orange/red)
- 🔍 **Filter** — toggle between "All / Available / Exhausted" at top
- 🎨 **Claude.ai styling** — cream background + coral accent + Win11 Fluent title bar + rounded window
- 🔒 **Fully local** — all operations happen on your machine, no external uploads
- 🪶 **Single instance** — re-launching focuses the existing window; lives in system tray

### Install

#### Option 1: Download exe (recommended)

Grab `ClaudeDesktopSwitcher.exe` from [Releases](../../releases), double-click to run. No Python required.

Optional: run `scripts\创建快捷方式.bat` to create Start Menu and Desktop shortcuts.

#### Option 2: Run from source

Requires Python 3.10+:

```bash
git clone https://github.com/Picadoo/claude-desktop-switcher.git
cd claude-desktop-switcher
pip install -r requirements.txt
python claude_switcher.py
```

### First-time setup

1. Log into Claude Desktop with account #1
2. Launch Switcher → click "**＋ 保存当前登录为新 Profile**" (Save current login as new Profile), name it
3. Click "**🔑 登录其他账号**" (Login another account) → Claude Desktop restarts to login screen
4. Log in with account #2 → repeat step 2
5. From now on: click "切换" (Switch) on any card to switch instantly

⚠️ **Don't manually log out inside Claude Desktop** — that revokes the token server-side and breaks the saved profile. Always switch via the Switcher.

### How it works

Session state in Claude Desktop lives across multiple files under `%APPDATA%\Claude\`:
- `config.json` (encrypted OAuth token)
- `Local Storage`, `Session Storage`, `Network`, `IndexedDB`, etc.

The Switcher isolates these **session-related** files into `~/.claude-instances/<name>/` and swaps them on switch — no system state changes.

**Usage data**: decrypts `oauth:tokenCache` via Windows DPAPI + AES-GCM to extract the OAuth bearer token, then calls `api.anthropic.com/v1/messages` (costs ~1 token) and reads from response headers:
- `anthropic-ratelimit-unified-5h-utilization` — 5-hour window usage %
- `anthropic-ratelimit-unified-5h-reset` — 5h reset epoch
- `anthropic-ratelimit-unified-7d-utilization` / `-reset` — 7-day window

Refresh strategy is conservative — single API call only on app start (if no cache), on profile switch (old + new account), or manual refresh button (30s throttle). Reset countdown is computed locally from the saved epoch, no further API calls needed.

### Security

- **Don't store plaintext passwords in notes!** Notes are saved as plain JSON in `~/.claude-instances/_profiles.json`
- Use **different payment methods** for multiple Pro accounts to avoid Anthropic correlating them
- Don't build API polling/automation on top of this — may trigger abuse detection
- Tool is 100% local, no telemetry or external reporting

### Limitations

- Windows only (uses DPAPI decryption + Win32 APIs)
- Switching requires fully closing Claude Desktop
- Switches at file level — cannot "continue account A's conversation in account B" (conversations live server-side, bound to the account)

### Acknowledgements

Inspired by [NeezerGu/claude-profile-switcher](https://github.com/NeezerGu/claude-profile-switcher) (PowerShell version).

This project is a Python rewrite adding GUI, usage monitoring, filtering, single-instance, and other quality-of-life improvements.

### License

MIT
