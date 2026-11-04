"""用假账号、假用量把界面开出来截图，存到 docs/screenshots/。不读真账号、不发请求、不碰 Claude。

跑法：python scripts/demo_screenshots.py
"""
import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import claude_switcher as C  # noqa: E402
from PIL import ImageGrab  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="switcher_demo_"))
now = int(time.time())
H = 3600

# 假账号
PROFILES = {
    "主力号": {"note": "work@example.com"},
    "备用号A": {"note": "backup-a@example.com"},
    "备用号B": {"note": "backup-b@example.com"},
    "学习号": {"note": "study@example.com"},
}
USAGE = {
    "主力号": {"session_pct": 12, "session_reset_epoch": now + 4 * H + 1200, "weekly_pct": 41,
              "weekly_reset_epoch": now + 70 * H, "fetched_at": now - 30, "ok": True},
    "备用号A": {"session_pct": 100, "session_reset_epoch": now + 1 * H + 900, "weekly_pct": 63,
               "weekly_reset_epoch": now + 50 * H, "fetched_at": now - 300, "rate_limited": True},
    "备用号B": {"session_pct": 0, "session_reset_epoch": now - 60, "weekly_pct": 18,
               "weekly_reset_epoch": now + 120 * H, "fetched_at": now - 7200, "ok": True},
    "学习号": {"session_pct": 57, "session_reset_epoch": now + 2 * H + 1800, "weekly_pct": 92,
             "weekly_reset_epoch": now + 20 * H, "fetched_at": now - 600, "ok": True},
}
for name in PROFILES:
    (tmp / name).mkdir()
    (tmp / name / "config.json").write_text("{}", encoding="utf-8")
(tmp / "_current_profile").write_text("主力号", encoding="utf-8")
(tmp / "_profiles.json").write_text(json.dumps(PROFILES, ensure_ascii=False), encoding="utf-8")
(tmp / "_usage_cache.json").write_text(json.dumps(USAGE, ensure_ascii=False), encoding="utf-8")
(tmp / "_settings.json").write_text(json.dumps({"auto_continue": True, "auto_switch": True}), encoding="utf-8")
hm = time.strftime("%H:%M", time.localtime(now - 1500))
(tmp / "_auto_switch.json").write_text(json.dumps({
    "last_switch_at": now - 1500,
    "last_info": f"{hm} 「备用号A」额度用完（5小时 100%）→「主力号」（5小时 0%，周 41%）",
    "failures": {}}, ensure_ascii=False), encoding="utf-8")

# 指到假目录，关掉所有会碰真东西的部分
C.INSTANCE_DIR = tmp
C.CURRENT_FILE = tmp / "_current_profile"
C.PROFILES_META = tmp / "_profiles.json"
C.USAGE_CACHE_FILE = tmp / "_usage_cache.json"
C.ProfileManager.SETTINGS_FILE = tmp / "_settings.json"
C.UsageFetcher.get_account_info = staticmethod(
    lambda cfg=None: {"plan": "Max", "tier": "", "token": None, "expires": ""})
C.UsageFetcher.fetch_usage = staticmethod(lambda *a, **k: {})
C.WarmupScheduler.start = lambda self: None
C.AutoSwitcher.start = lambda self: None
C.App._setup_tray = lambda self: None
C.App._update_tray_menu = lambda self: None
C.App._start_singleton_listener = lambda self: None
C.App._check_environment = lambda self: None
C.App.on_refresh_usage = lambda self: None


def grab(app, name):
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    time.sleep(0.4)
    hwnd = ctypes.windll.user32.GetParent(app.winfo_id()) or app.winfo_id()
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(OUT / f"{name}.png")
    print("saved", OUT / f"{name}.png", img.size)


app = C.App(None)


def step1():
    app.geometry("560x900")
    app._refresh_profiles()
    app.set_status("4/4 账号已更新")
    app.after(1200, lambda: (grab(app, "main"), step2()))


def step2():
    app.set_status("已切换到「主力号」，已在「总控」发「继续」")
    app.auto_info_var.set("上次：" + app._auto_switcher.state.data["last_info"])
    app.after(600, lambda: (grab(app, "auto_switch"), step3()))


def step3():
    app._set_filter("available")
    app.set_status("就绪")
    app.after(800, lambda: (grab(app, "filter_available"), app.destroy()))


app.after(2500, step1)
app.mainloop()
