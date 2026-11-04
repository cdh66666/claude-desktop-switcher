"""
Claude Desktop Switcher
现代 GUI 界面，支持系统托盘常驻，一键切换多个 Claude Desktop 账号。
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import time
import ctypes
import ctypes.wintypes
import base64
import math
import re
import socket
import random
from pathlib import Path

import auto_switch
from typing import Optional
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray

# 设置 AppUserModelID，让 Windows 任务栏显示自定义图标而非 python.exe 图标
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClaudeDesktopSwitcher.App")

# === 配置 ===

def _detect_claude_dir() -> Path:
    """自动检测 Claude Desktop 的会话目录。

    支持两种安装：
      1. 官方 Squirrel 版：  %APPDATA%\\Claude
      2. Microsoft Store 版：%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude

    优先返回真实存在 config.json 的那个；都不存在时返回 Squirrel 默认路径
    （让后续逻辑给出"未找到登录状态"的友好提示）。
    """
    appdata = Path(os.environ["APPDATA"])
    local_appdata = Path(os.environ.get("LOCALAPPDATA", appdata.parent / "Local"))

    candidates = []

    # 1. Squirrel 版（最常见）
    squirrel = appdata / "Claude"
    candidates.append(squirrel)

    # 2. Microsoft Store 版：包名带哈希后缀，匹配所有 Claude_* 目录
    packages_dir = local_appdata / "Packages"
    if packages_dir.exists():
        try:
            for pkg in packages_dir.iterdir():
                if pkg.is_dir() and pkg.name.startswith("Claude_"):
                    store_path = pkg / "LocalCache" / "Roaming" / "Claude"
                    candidates.append(store_path)
        except OSError:
            pass

    # 优先返回有 config.json 的（说明真的登录过）
    for c in candidates:
        if (c / "config.json").exists():
            return c

    # 都没登录过 → 返回 Squirrel 默认路径
    return squirrel


CLAUDE_DIR = _detect_claude_dir()
INSTANCE_DIR = Path.home() / ".claude-instances"
CURRENT_FILE = INSTANCE_DIR / "_current_profile"
PROFILES_META = INSTANCE_DIR / "_profiles.json"
USAGE_CACHE_FILE = INSTANCE_DIR / "_usage_cache.json"


def _resource_dir() -> Path:
    """返回资源目录：开发时是脚本目录，PyInstaller 打包后是临时解压目录"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后运行：资源在 sys._MEIPASS
        return Path(sys._MEIPASS)
    return Path(__file__).parent


APP_DIR = _resource_dir()
# 图标在 assets/ 子目录下；PyInstaller 打包时通过 .spec datas 也要保持这个结构
ICON_PATH = APP_DIR / "assets" / "claude-icon.ico"

# 单实例检测：绑定本机回环端口，重复启动时唤醒已有窗口而不是再开一个进程
SINGLETON_PORT = 49231

# 5h 窗口预热用的 prompt 池（轻量、无害的日常问题，让请求看起来像真实用户）
WARMUP_PROMPTS = [
    "今天天气怎么样？",
    "推荐一首歌",
    "讲个冷笑话",
    "1+1 等于几",
    "你好",
    "Python 是什么",
    "推荐一本书",
    "宋朝有多少年",
    "今天周几",
    "如何泡茶",
    "番茄炒蛋怎么做",
    "什么是机器学习",
    "光速是多少",
    "推荐一部电影",
    "杭州有什么好玩的",
]

# 需要按 profile 交换的会话文件
SESSION_FILES = [
    "config.json", "Preferences", "DIPS", "DIPS-wal",
    "SharedStorage", "SharedStorage-wal", "ant-did"
]
SESSION_DIRS = [
    "Local Storage", "Session Storage", "Network", "IndexedDB", "WebStorage"
]

# === 主题 ===
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# 颜色常量 — Claude.ai 风格（暖色调，米白底）
COLORS = {
    # 主色调
    "bg_main": "#F5F4EE",        # 主背景：米白
    "bg_card": "#FFFFFF",        # 卡片背景：纯白
    "bg_card_active": "#FCEEE8", # 激活卡片：浅珊瑚
    "border_default": "#E8E6DF", # 卡片边框
    "border_active": "#D97757",  # 激活卡片边框：Claude 橙

    # 主色
    "accent": "#D97757",         # Claude 珊瑚橙
    "accent_hover": "#C25B3B",   # 主色悬停
    "accent_soft": "#FDF0EB",    # 主色浅底

    # 文字
    "text": "#191918",           # 主文字（深灰）
    "text_primary": "#191918",
    "text_dim": "#6F6E77",       # 次要文字
    "text_secondary": "#6F6E77",
    "text_tertiary": "#A8A7B0",  # 三级文字（提示）

    # 功能色
    "divider": "#ECEAE6",        # 分割线
    "track": "#F0EFED",          # 进度条轨道
    "green": "#3B8C5C",          # 安全
    "yellow": "#C49E3F",         # 注意
    "orange": "#D97757",         # 警告（用主色）
    "red": "#C25B3B",            # 危险
}


# === 图标 ===

def load_claude_icon(size: int = 48) -> Image.Image:
    """从 Claude 官方 ico 加载并缩放到指定尺寸"""
    try:
        img = Image.open(str(ICON_PATH)).convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception:
        # 兜底：纯色方块
        img = Image.new("RGBA", (size, size), (217, 119, 87, 255))
        return img


def make_caption_icon(kind: str, size: int = 32, color=(25, 25, 24, 255)) -> Image.Image:
    """生成 Win11 风格的窗口控制线条图标（最小化/最大化/还原/关闭）

    输出 size×size 的高分辨率图（后续会被 CTkImage 缩放到目标显示尺寸），
    抗锯齿质量好。线宽固定 1.4 像素，跟 Win11 Fluent 一致。
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 几何中心 + 内边距（图标占中心 ~56% 区域，跟 Win11 标题栏视觉一致）
    pad = int(size * 0.22)
    inner = size - 2 * pad
    lw = max(2, int(round(size * 0.06)))  # 线宽 ~6%，更明显

    if kind == "minimize":
        # 一条水平短线
        y = size // 2
        d.line([(pad, y), (size - pad, y)], fill=color, width=lw)

    elif kind == "maximize":
        # 矩形线框
        d.rectangle([pad, pad, size - pad - 1, size - pad - 1],
                    outline=color, width=lw)

    elif kind == "restore":
        # 双层矩形（前矩形 + 后矩形）
        offset = max(2, size // 12)
        # 后矩形：右上偏移
        d.rectangle([pad + offset, pad,
                     size - pad - 1, size - pad - 1 - offset],
                    outline=color, width=lw)
        # 前矩形：左下覆盖
        d.rectangle([pad, pad + offset,
                     size - pad - 1 - offset, size - pad - 1],
                    outline=color, width=lw)

    elif kind == "close":
        # 两条交叉斜线
        d.line([(pad, pad), (size - pad - 1, size - pad - 1)], fill=color, width=lw)
        d.line([(pad, size - pad - 1), (size - pad - 1, pad)], fill=color, width=lw)

    elif kind == "refresh":
        # Lucide RefreshCw 风格：两段对称的弧 + 两个三角箭头
        import math
        cx, cy = size / 2.0, size / 2.0
        # 圆环参数
        ring_pad = int(size * 0.22)
        ring_r = (size - 2 * ring_pad) / 2.0
        ring_lw = max(2, int(round(size * 0.085)))
        bbox = [ring_pad, ring_pad, size - ring_pad - 1, size - ring_pad - 1]

        # 上半弧：从 -45° 到 -160°（顺时针扫过顶部）
        # 在右上有箭头开口，左下也对称地有箭头开口
        d.arc(bbox, start=200, end=325, fill=color, width=ring_lw)
        d.arc(bbox, start=20, end=145, fill=color, width=ring_lw)

        # 上方箭头（开口在右上 -45° 位置，箭头指向右下）
        ah = max(3, int(round(size * 0.16)))  # 箭头大小
        a1 = math.radians(-35)
        ax = cx + ring_r * math.cos(a1)
        ay = cy + ring_r * math.sin(a1)
        d.polygon([
            (ax + ah * 1.0, ay - ah * 0.2),       # 右下
            (ax - ah * 0.4, ay + ah * 0.9),       # 下
            (ax - ah * 0.6, ay - ah * 0.7),       # 左上
        ], fill=color)

        # 下方箭头（对称在左下 135° 位置，箭头指向左上）
        a2 = math.radians(145)
        bx = cx + ring_r * math.cos(a2)
        by = cy + ring_r * math.sin(a2)
        d.polygon([
            (bx - ah * 1.0, by + ah * 0.2),
            (bx + ah * 0.4, by - ah * 0.9),
            (bx + ah * 0.6, by + ah * 0.7),
        ], fill=color)

    return img

# === Profile 管理逻辑 ===

class ProfileManager:
    """核心切换逻辑"""

    def __init__(self):
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def session_storage_error() -> Optional[str]:
        """拒绝对被重定向或包含链接的会话目录执行破坏性替换。"""
        try:
            root = CLAUDE_DIR.resolve(strict=True)
            for name in SESSION_FILES + SESSION_DIRS:
                path = CLAUDE_DIR / name
                if not path.exists():
                    continue
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    return 'Claude 会话目录包含链接，无法安全切换；原会话未改动'
                if not path.resolve(strict=True).is_relative_to(root):
                    return 'Claude 会话目录被 Windows 重定向，无法安全切换；原会话未改动'
                if path.is_dir():
                    for item in path.rglob('*'):
                        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                            return 'Claude 会话目录包含链接，无法安全切换；原会话未改动'
                        if not item.resolve(strict=True).is_relative_to(root):
                            return 'Claude 会话目录被 Windows 重定向，无法安全切换；原会话未改动'
        except (OSError, RuntimeError):
            return '无法确认 Claude 会话目录是否安全；原会话未改动'
        return None

    def get_current(self) -> Optional[str]:
        if CURRENT_FILE.exists():
            return CURRENT_FILE.read_text(encoding="utf-8").strip() or None
        return None

    def set_current(self, name: str):
        CURRENT_FILE.write_text(name, encoding="utf-8")

    def get_profiles(self) -> list[str]:
        if not INSTANCE_DIR.exists():
            return []
        profiles = []
        for d in INSTANCE_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                if (d / "config.json").exists():
                    profiles.append(d.name)
        return sorted(profiles)

    def get_meta(self) -> dict:
        if PROFILES_META.exists():
            try:
                return json.loads(PROFILES_META.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_meta(self, meta: dict):
        PROFILES_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_warmup(self, name: str) -> dict:
        """读取某个 profile 的预热配置；不存在时返回默认值"""
        meta = self.get_meta()
        warmup = meta.get(name, {}).get("warmup", {})
        return {
            "enabled": bool(warmup.get("enabled", False)),
            "schedule": list(warmup.get("schedule", [])),  # ["05:00", ...]
            "max_tokens": int(warmup.get("max_tokens", 10)),
            "last_run_date": warmup.get("last_run_date", ""),
        }

    def save_warmup(self, name: str, warmup: dict):
        """写入某个 profile 的预热配置"""
        meta = self.get_meta()
        entry = meta.setdefault(name, {})
        entry["warmup"] = {
            "enabled": bool(warmup.get("enabled", False)),
            "schedule": list(warmup.get("schedule", [])),
            "max_tokens": int(warmup.get("max_tokens", 10)),
            "last_run_date": warmup.get("last_run_date", ""),
        }
        self.save_meta(meta)

    def save_session(self, name: str):
        dest = INSTANCE_DIR / name
        dest.mkdir(parents=True, exist_ok=True)
        for f in SESSION_FILES:
            src = CLAUDE_DIR / f
            if src.exists():
                shutil.copy2(src, dest / f)
        for d in SESSION_DIRS:
            src = CLAUDE_DIR / d
            if src.exists():
                d_dest = dest / d
                if d_dest.exists():
                    shutil.rmtree(d_dest)
                shutil.copytree(src, d_dest)

    def load_session(self, name: str):
        src = INSTANCE_DIR / name
        for f in SESSION_FILES:
            f_src = src / f
            if f_src.exists():
                shutil.copy2(f_src, CLAUDE_DIR / f)
        for d in SESSION_DIRS:
            d_src = src / d
            if d_src.exists():
                d_dest = CLAUDE_DIR / d
                if d_dest.exists():
                    shutil.rmtree(d_dest)
                shutil.copytree(d_src, d_dest)

    # ---- Claude Desktop 进程识别 ----
    # 只认桌面版（Electron）进程：exe 位于 AnthropicClaude\app-* 或 WindowsApps\*Claude*。
    # 终端里单独跑的 Claude Code CLI（也叫 claude.exe，在 claude-code 目录或 npm 目录）不会被误杀。
    _last_exe: Optional[str] = None

    @staticmethod
    def _is_desktop_exe(path: str) -> bool:
        p = (path or "").lower().replace("/", "\\")
        if not p.endswith("\\claude.exe"):
            return False
        if "\\claude-code\\" in p:
            return False
        return "\\anthropicclaude\\" in p or ("\\windowsapps\\" in p and "claude" in p)

    @classmethod
    def _desktop_processes(cls) -> list[tuple[int, int, str]]:
        """返回 [(pid, ppid, exe_path)]，仅桌面版进程。用 Toolhelp + QueryFullProcessImageName，不依赖 WMI。"""
        k32 = ctypes.windll.kernel32

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD),
                        ("th32ProcessID", ctypes.wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", ctypes.wintypes.DWORD), ("cntThreads", ctypes.wintypes.DWORD),
                        ("th32ParentProcessID", ctypes.wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        k32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
        k32.OpenProcess.restype = ctypes.wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        out = []
        if not snap or snap == ctypes.wintypes.HANDLE(-1).value:
            return out
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(e)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == "claude.exe":
                    h = k32.OpenProcess(0x1000, False, e.th32ProcessID)  # QUERY_LIMITED_INFORMATION
                    if h:
                        buf = ctypes.create_unicode_buffer(1024)
                        n = ctypes.wintypes.DWORD(1024)
                        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                            if cls._is_desktop_exe(buf.value):
                                out.append((e.th32ProcessID, e.th32ParentProcessID, buf.value))
                        k32.CloseHandle(h)
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        return out

    @staticmethod
    def _send_end_session(pids) -> int:
        """给这些进程的所有顶层窗口（含隐藏窗口）发 WM_ENDSESSION，返回发了几个窗口。"""
        user32 = ctypes.windll.user32
        targets = set(pids)
        hwnds = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def cb(hwnd, _):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in targets:
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        WM_ENDSESSION, ENDSESSION_CLOSEAPP = 0x0016, 0x00000001
        result = ctypes.c_size_t()
        for h in hwnds:
            # SMTO_ABORTIFHUNG(2)，每个窗口最多等 3 秒
            user32.SendMessageTimeoutW(h, WM_ENDSESSION, 1, ENDSESSION_CLOSEAPP, 0x2, 3000,
                                       ctypes.byref(result))
        return len(hwnds)

    def is_claude_running(self) -> bool:
        return bool(self._desktop_processes())

    def stop_claude(self, timeout: float = 15.0) -> bool:
        """强制退出 Claude Desktop（含托盘、子进程），等到进程全部消失、文件锁释放后返回 True。"""
        procs = self._desktop_processes()
        if not procs:
            return True
        ProfileManager._last_exe = procs[0][2]
        pids = {p[0] for p in procs}
        # 主进程 = 父进程不是桌面版进程的那个；/T 连同子进程（含桌面里跑的 Claude Code）一起结束
        roots = [p[0] for p in procs if p[1] not in pids] or list(pids)
        flags = 0x08000000
        try:
            # 1) 正常退出：给主进程的窗口发“Windows 要关机了”(WM_ENDSESSION)。
            #    Claude 收到后会走自己的退出流程，把登录 cookie / 令牌写盘再退。
            #    直接强杀可能丢掉刚刷新、还没写盘的登录信息，下次切回来就得重新登录。
            self._send_end_session(roots)
            deadline = time.time() + 12
            while time.time() < deadline and self._desktop_processes():
                time.sleep(0.25)
            # 2) 还没退：普通 taskkill 再等一下
            if self._desktop_processes():
                for pid in roots:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True,
                                   creationflags=flags, timeout=10)
                deadline = time.time() + 2.5
                while time.time() < deadline and self._desktop_processes():
                    time.sleep(0.25)
            deadline = time.time() + timeout
            while time.time() < deadline:
                left = self._desktop_processes()
                if not left:
                    time.sleep(1.0)  # 等 LevelDB / SQLite 文件锁释放
                    return True
                for pid, _, _ in left:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                                   creationflags=flags, timeout=10)
                time.sleep(0.5)
        except Exception:
            pass
        return not self._desktop_processes()

    @classmethod
    def _find_claude_exe(cls) -> Optional[Path]:
        """找 Squirrel 启动器 claude.exe（它会自动跳到最新 app-* 版本）。"""
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        cands: list[Path] = []
        if cls._last_exe:  # 刚才关掉的那个：...\AnthropicClaude\app-x\claude.exe → 上一级启动器
            last = Path(cls._last_exe)
            cands += [last.parent.parent / "claude.exe", last]
        cands.append(local / "AnthropicClaude" / "claude.exe")
        # 被其他打包应用（如 Codex）虚拟化安装到 Packages\*\LocalCache 下的情况
        try:
            cands += sorted(local.glob("Packages/*/LocalCache/Local/AnthropicClaude/claude.exe"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            pass
        # 开始菜单快捷方式
        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
        for lnk in (start_menu / "Claude.lnk", start_menu / "Anthropic" / "Claude.lnk"):
            if lnk.exists():
                try:
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}').TargetPath"],
                        capture_output=True, text=True, creationflags=0x08000000, timeout=10)
                    t = r.stdout.strip()
                    if t:
                        cands.append(Path(t))
                except Exception:
                    pass
        for c in cands:
            if c.name.lower() == "claude.exe" and c.exists() and cls._is_desktop_exe(str(c)):
                return c
        return None

    def start_claude(self, wait: float = 20.0) -> bool:
        """启动 Claude Desktop，并等到进程真的起来才返回 True。"""
        if self._desktop_processes():
            return True
        launched = False
        exe = self._find_claude_exe()
        if exe:
            try:
                # 脱离换号器进程，换号器退出也不影响 Claude
                subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                                 creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
                launched = True
            except Exception:
                launched = False
        if not launched:
            # Microsoft Store 版：读清单里的真实 AppId
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "$p = Get-AppxPackage | Where-Object { $_.Name -match '^(Anthropic|Claude)' } | Select-Object -First 1; "
                     "if ($p) { $id = (Get-AppxPackageManifest $p).Package.Applications.Application | "
                     "Select-Object -First 1 -ExpandProperty Id; \"$($p.PackageFamilyName)!$id\" }"],
                    capture_output=True, text=True, creationflags=0x08000000, timeout=15)
                aumid = r.stdout.strip()
                if "!" in aumid:
                    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"],
                                     creationflags=0x08000000)
                    launched = True
            except Exception:
                pass
        if not launched:
            return False
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._desktop_processes():
                return True
            time.sleep(0.5)
        return False

    # ---- 切换后的检查：登录状态 / 设备标识 / 自动“继续” ----

    @staticmethod
    def _main_log() -> Optional[Path]:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        cands = [local / "Claude" / "logs" / "main.log"]
        try:
            cands += list(local.glob("Packages/*/LocalCache/Local/Claude/logs/main.log"))
        except OSError:
            pass
        cands = [c for c in cands if c.exists()]
        return max(cands, key=lambda p: p.stat().st_mtime) if cands else None

    @classmethod
    def log_offset(cls) -> tuple[Optional[Path], int]:
        log = cls._main_log()
        try:
            return log, (log.stat().st_size if log else 0)
        except OSError:
            return log, 0

    @staticmethod
    def wait_login_state(log: Optional[Path], offset: int, timeout: float = 40.0) -> str:
        """读 Claude 日志里启动之后的新内容：'ok' 已登录 / 'logged_out' 被登出 / 'unknown'。"""
        if not log:
            return "unknown"
        seen_ok_at = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(log, "rb") as fh:
                    fh.seek(offset)
                    text = fh.read().decode("utf-8", "replace")
            except OSError:
                text = ""
            if ("Navigated to /logout" in text or "loggedOut: false → true" in text
                    or ("User is logged out" in text and "account active and logged in" not in text)):
                return "logged_out"
            if seen_ok_at is None and "account active and logged in" in text:
                seen_ok_at = time.time()
            # 登录后服务器可能过几秒才拒绝旧会话（403 → 登出），多看 8 秒
            if seen_ok_at and time.time() - seen_ok_at > 8:
                return "ok"
            time.sleep(1)
        return "ok" if seen_ok_at else "unknown"

    @staticmethod
    def _file_hash(p: Path) -> Optional[str]:
        import hashlib
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            return None

    def shared_device_profiles(self, name: str) -> list[str]:
        """和 name 共用同一个设备标识(ant-did)的其他 profile。共用的账号会被服务器互相踢下线。"""
        mine = self._file_hash(INSTANCE_DIR / name / "ant-did")
        if not mine:
            return []
        return [p for p in self.get_profiles()
                if p != name and self._file_hash(INSTANCE_DIR / p / "ant-did") == mine]

    def reset_device_identity(self) -> bool:
        """关掉 Claude，删掉设备标识(ant-did)和 cookie，再打开：重新登录后就是一台“新设备”。"""
        if not self.stop_claude():
            return False
        for p in [CLAUDE_DIR / "ant-did",
                  CLAUDE_DIR / "Network" / "Cookies",
                  CLAUDE_DIR / "Network" / "Cookies-journal"]:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return self.start_claude()

    # 自动“继续”的设置（改 ~/.claude-instances/_settings.json；folder 留空 = 不限文件夹）
    SETTINGS_FILE = INSTANCE_DIR / "_settings.json"
    DEFAULT_SETTINGS = {"auto_continue": True, "folder": "",
                        "text": "继续", "session_title": "总控"}

    def get_settings(self) -> dict:
        s = dict(self.DEFAULT_SETTINGS)
        try:
            s.update(json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
        return s

    def save_settings(self, s: dict):
        self.SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def current_account_uuid() -> Optional[str]:
        try:
            cfg = json.loads((CLAUDE_DIR / "config.json").read_text(encoding="utf-8"))
            u = cfg.get("lastKnownAccountUuid")
            return u.lower() if isinstance(u, str) else None
        except Exception:
            return None

    def find_control_session(self, account: Optional[str], settings: dict) -> Optional[str]:
        """在当前账号的桌面版代码对话里找标题为“总控”、没归档、在指定文件夹的那个。"""
        if not account:
            return None
        base = CLAUDE_DIR / "claude-code-sessions" / account
        folder = settings.get("folder") or ""
        want_cwd = os.path.normcase(os.path.normpath(folder)) if folder else ""
        best = None
        try:
            files = list(base.glob("*/local_*.json"))
        except OSError:
            return None
        for f in files:
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if j.get("isArchived") or (j.get("title") or "").strip() != settings.get("session_title"):
                continue
            cwd = os.path.normcase(os.path.normpath(j.get("cwd") or ""))
            if want_cwd and cwd != want_cwd:
                continue
            key = j.get("lastActivityAt") or 0
            if best is None or key > best[0]:
                best = (key, j.get("sessionId"))
        sid = best[1] if best else None
        return sid if isinstance(sid, str) and re.fullmatch(r"local_[A-Za-z0-9-]{1,64}", sid) else None

    def run_auto_continue(self) -> tuple[bool, str]:
        s = self.get_settings()
        script = Path(__file__).with_name("auto_continue.ps1")
        if not script.exists():
            return False, "缺少 auto_continue.ps1"
        sid = self.find_control_session(self.current_account_uuid(), s)
        if not sid and not s.get("folder"):
            return False, f"没找到「{s['session_title']}」对话，没发「{s['text']}」"
        args = self.auto_continue_args(script, s, sid)
        return self.interpret_auto_continue(s, sid, self._run_ps)(args)

    @staticmethod
    def auto_continue_args(script: Path, s: dict, sid: Optional[str]) -> list:
        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-Text", s["text"], "-TimeoutSec", "90"]
        if sid:
            args += ["-SessionId", sid, "-ExpectTitle", s["session_title"]]
        else:
            args += ["-Folder", s["folder"]]
        return args

    @staticmethod
    def _run_ps(args) -> tuple[int, str]:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=0x08000000, timeout=180)
        return r.returncode, (r.stdout or "").strip()

    @staticmethod
    def interpret_auto_continue(s: dict, sid: Optional[str], runner):
        """返回一个函数：跑脚本并把退出码翻成给用户看的一句话。
        5 = 没确认是目标对话（脚本保证没打字没回车）。新开对话绝不重试，免得开出好几个。"""
        title, text = s["session_title"], s["text"]
        manual = f"请自己到「{title}」发「{text}」" if sid else f"请自己在 {s['folder']} 新开对话发「{text}」"

        def go(args):
            code = -1
            for attempt in range(2 if sid else 1):
                try:
                    code, _out = runner(args)
                except Exception as e:
                    return False, f"自动发「{text}」失败：{type(e).__name__}，{manual}"
                if code in (0, 2, 3):
                    break
                # 4/5：只有打开已有的总控才重试一次（脚本每次都会重新核对）
            if code == 0:
                if sid:
                    return True, f"已在「{title}」发「{text}」"
                return True, f"新开了对话并发「{text}」（它会自己改名「{title}」）"
            if code == 2:
                return False, f"没等到输入框，没发「{text}」"
            if code == 3:
                return False, f"输入框里的字对不上（可能有草稿），没发。{manual}"
            if code == 5:
                what = f"「{title}」" if sid else "新对话"
                return False, f"没确认打开的是{what}，没发。{manual}"
            return False, f"没能自动发「{text}」，{manual}"
        return go

    def after_start(self, target: str, log: Optional[Path], offset: int,
                    progress_cb=None) -> str:
        """Claude 启动后：确认登录状态；被登出时处理设备标识冲突；登录正常就自动发“继续”。"""
        if progress_cb:
            progress_cb("检查登录状态...")
        state = self.wait_login_state(log, offset)
        if state == "logged_out":
            shared = self.shared_device_profiles(target)
            if shared:
                if progress_cb:
                    progress_cb("换独立设备标识...")
                self.reset_device_identity()
                return (f"「{target}」被服务器登出了：它和「{'、'.join(shared)}」共用一个设备标识，会互相踢。"
                        f"已给它换了新标识，请在 Claude 里重新登录一次，以后不会再互踢")
            return f"「{target}」被服务器登出了，请在 Claude 里重新登录；下次切换会自动保存新登录"
        if state == "unknown":
            return f"已切换到「{target}」（没确认到登录状态，请看一眼 Claude）"
        msg = f"已切换到「{target}」"
        if self.get_settings().get("auto_continue"):
            if progress_cb:
                progress_cb("自动发「继续」...")
            ok, m = self.run_auto_continue()
            msg += "，" + m
        return msg

    def switch_profile(self, target: str, progress_cb=None,
                       on_old_usage_fetched=None) -> tuple[bool, str]:
        """切换并把每一步花的时间记到 self.last_switch_record（给换号复盘用）。"""
        t0 = time.time()
        steps = []

        def cb(text):
            steps.append([text.rstrip('.。'), round(time.time() - t0, 1)])
            if progress_cb:
                progress_cb(text)

        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "from": self.get_current(),
               "to": target, "auto_continue": bool(self.get_settings().get("auto_continue"))}
        ok, msg = False, ""
        try:
            ok, msg = self._switch_profile(target, cb, on_old_usage_fetched)
        except Exception as e:
            msg = f"出错：{type(e).__name__} {e}"
            raise
        finally:
            rec.update(ok=ok, msg=msg, steps=steps, total_sec=round(time.time() - t0, 1))
            self.last_switch_record = rec
        return ok, msg

    def _switch_profile(self, target: str, progress_cb=None,
                        on_old_usage_fetched=None) -> tuple[bool, str]:
        """
        切换到目标 profile。
        on_old_usage_fetched: 可选回调，参数 (old_profile_name, usage_dict)，
                              用于在切换时同步更新被切走账号的用量数据。
        """
        current = self.get_current()
        target_dir = INSTANCE_DIR / target
        if not (target_dir / "config.json").exists():
            return False, f"Profile '{target}' 不存在"
        if current == target:
            return False, f"已经在 '{target}' 上了"

        storage_error = self.session_storage_error()
        if storage_error:
            return False, storage_error

        # 切换前：先抓一次旧账号的用量（只调一次 API）
        # 这是"切换即刷新"的精妙之处——反正都要切了，顺便把被切走的账号状态更新一下
        if current and on_old_usage_fetched:
            old_token = None
            try:
                old_info = UsageFetcher.get_account_info()  # 此时 Claude 还在用旧账号
                old_token = old_info.get('token')
            except Exception:
                pass
            if old_token:
                if progress_cb:
                    progress_cb("更新当前账号用量...")
                old_usage = UsageFetcher.fetch_usage(old_token)
                try:
                    on_old_usage_fetched(current, old_usage)
                except Exception:
                    pass

        if progress_cb:
            progress_cb("正在关闭 Claude...")
        if not self.stop_claude():
            return False, "Claude 没能在 15 秒内退出，原会话未改动；请在任务管理器结束 Claude 后重试"
        if progress_cb:
            progress_cb("保存当前会话...")
        if current:
            self.save_session(current)
        if progress_cb:
            progress_cb("加载目标会话...")
        self.load_session(target)
        self.set_current(target)
        if progress_cb:
            progress_cb("启动 Claude...")
        log, offset = self.log_offset()
        if not self.start_claude():
            return False, f"已加载 '{target}'，但 Claude 未能启动；请手动启动并核对账号"
        return True, self.after_start(target, log, offset, progress_cb)

    def create_profile(self, name: str) -> tuple[bool, str]:
        if not (CLAUDE_DIR / "config.json").exists():
            return False, ("未找到 Claude Desktop 登录状态。\n"
                           "请先：1) 安装 Claude Desktop  "
                           "2) 启动并登录任意一个账号  "
                           "3) 看到主界面后再回来保存 Profile")
        if (INSTANCE_DIR / name / "config.json").exists():
            return False, f"Profile '{name}' 已存在，请换个名称"
        storage_error = self.session_storage_error()
        if storage_error:
            return False, storage_error
        if not self.stop_claude():
            return False, "Claude 没能退出，请在任务管理器结束 Claude 后重试"
        time.sleep(1)
        self.save_session(name)
        self.set_current(name)
        return True, f"已创建 Profile '{name}'"

    def delete_profile(self, name: str) -> tuple[bool, str]:
        target_dir = INSTANCE_DIR / name
        if not target_dir.exists():
            return False, f"Profile '{name}' 不存在"
        shutil.rmtree(target_dir)
        meta = self.get_meta()
        meta.pop(name, None)
        self.save_meta(meta)
        if self.get_current() == name:
            self.set_current("")
        return True, f"已删除 '{name}'"


# === 用量获取 ===

class UsageFetcher:
    """从 Claude Desktop 的加密 token 中获取账号信息和用量"""

    @staticmethod
    def _dpapi_decrypt(data: bytes) -> bytes:
        class BLOB(ctypes.Structure):
            _fields_ = [('cbData', ctypes.wintypes.DWORD),
                        ('pbData', ctypes.POINTER(ctypes.c_char))]
        blob_in = BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        blob_out = BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return b''
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return raw

    @staticmethod
    def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
        class AI(ctypes.Structure):
            _fields_ = [
                ('cbSize', ctypes.c_ulong), ('dwInfoVersion', ctypes.c_ulong),
                ('pbNonce', ctypes.c_void_p), ('cbNonce', ctypes.c_ulong),
                ('pbAuthData', ctypes.c_void_p), ('cbAuthData', ctypes.c_ulong),
                ('pbTag', ctypes.c_void_p), ('cbTag', ctypes.c_ulong),
                ('pbMacContext', ctypes.c_void_p), ('cbMacContext', ctypes.c_ulong),
                ('cbAAD', ctypes.c_ulong), ('cbData', ctypes.c_ulonglong),
                ('dwFlags', ctypes.c_ulong)]
        _b = ctypes.windll.bcrypt
        hAlg = ctypes.c_void_p()
        _b.BCryptOpenAlgorithmProvider(ctypes.byref(hAlg), ctypes.c_wchar_p('AES'), None, 0)
        mode = 'ChainingModeGCM\0'.encode('utf-16-le')
        mb = (ctypes.c_ubyte * len(mode))(*mode)
        _b.BCryptSetProperty(hAlg, ctypes.c_wchar_p('ChainingMode'), mb, len(mode), 0)
        hKey = ctypes.c_void_p()
        kb = (ctypes.c_ubyte * len(key))(*key)
        _b.BCryptGenerateSymmetricKey(hAlg, ctypes.byref(hKey), None, 0, kb, len(key), 0)
        ai = AI()
        ai.cbSize = ctypes.sizeof(ai); ai.dwInfoVersion = 1
        nb = (ctypes.c_ubyte * len(nonce))(*nonce)
        ai.pbNonce = ctypes.cast(nb, ctypes.c_void_p); ai.cbNonce = len(nonce)
        tb = (ctypes.c_ubyte * len(tag))(*tag)
        ai.pbTag = ctypes.cast(tb, ctypes.c_void_p); ai.cbTag = len(tag)
        cb = (ctypes.c_ubyte * len(ciphertext))(*ciphertext)
        pb = (ctypes.c_ubyte * len(ciphertext))()
        co = ctypes.c_ulong()
        st = _b.BCryptDecrypt(hKey, cb, len(ciphertext), ctypes.byref(ai),
                              None, 0, pb, len(ciphertext), ctypes.byref(co), 0)
        _b.BCryptDestroyKey(hKey)
        _b.BCryptCloseAlgorithmProvider(hAlg, 0)
        if st != 0:
            return b''
        return bytes(pb[:co.value])

    @classmethod
    def decrypt_oauth_cache(cls, config_path: Path = None,
                            cache_key: str = 'oauth:tokenCache') -> dict:
        """解密指定版本的 Claude Desktop OAuth 缓存；不改写凭据。"""
        try:
            local_state = CLAUDE_DIR / 'Local State'
            if not local_state.exists():
                return {}
            ls_data = json.loads(local_state.read_text(encoding='utf-8'))
            raw_key = base64.b64decode(ls_data['os_crypt']['encrypted_key'])
            if raw_key[:5] != b'DPAPI':
                return {}
            master_key = cls._dpapi_decrypt(raw_key[5:])
            if not master_key:
                return {}

            cfg_path = config_path or (CLAUDE_DIR / 'config.json')
            if not cfg_path.exists():
                return {}
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            enc_b64 = cfg.get(cache_key, '')
            if not enc_b64:
                return {}

            enc = base64.b64decode(enc_b64 + '==')
            if enc[:3] != b'v10':
                return {}
            nonce = enc[3:15]
            ct_tag = enc[15:]
            ciphertext, tag = ct_tag[:-16], ct_tag[-16:]
            plain = cls._aes_gcm_decrypt(master_key, nonce, ciphertext, tag)
            if not plain:
                return {}
            return json.loads(plain.decode('utf-8'))
        except Exception:
            return {}

    @classmethod
    def get_account_info(cls, config_path: Path = None) -> dict:
        """从新版或旧版 OAuth 缓存选择可用于推理的当前账号令牌。"""
        unavailable = {"plan": "未知", "token": None, "expires": None}
        cfg_path = config_path or (CLAUDE_DIR / 'config.json')
        try:
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            if not isinstance(cfg, dict):
                raise ValueError('invalid config')
        except (OSError, ValueError, TypeError):
            return {**unavailable, "error": "无法读取账号配置"}

        account_id = cfg.get('lastKnownAccountUuid')
        account_id = account_id.lower() if isinstance(account_id, str) else None
        cache_keys = ('oauth:tokenCacheV2', 'oauth:tokenCache')
        if not any(cfg.get(key) for key in cache_keys):
            return {**unavailable, "error": "账号配置中没有 OAuth 登录令牌"}

        candidates = []
        for priority, cache_key in enumerate(cache_keys):
            token_data = cls.decrypt_oauth_cache(config_path, cache_key)
            if not isinstance(token_data, dict):
                continue
            for key, entry in token_data.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                token = entry.get('token')
                if not isinstance(token, str) or not token:
                    continue
                scopes = set(re.findall(r'user:[A-Za-z0-9_-]+', key))
                if scopes and 'user:inference' not in scopes:
                    continue
                scoped_account = key[5:].split('|', 1)[0].lower() if key.startswith('acct:') and '|' in key else None
                if account_id and scoped_account and scoped_account != account_id:
                    continue
                org = re.search(r':([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):https?://', key, re.I)
                expires_ms = entry.get('expiresAt', 0)
                if isinstance(expires_ms, bool) or not isinstance(expires_ms, (int, float)):
                    continue
                try:
                    finite_expiry = math.isfinite(expires_ms)
                except OverflowError:
                    continue
                if not finite_expiry:
                    continue
                candidates.append((priority, scoped_account, org.group(1).lower() if org else None, expires_ms, entry))

        if not candidates:
            return {**unavailable, "error": "登录令牌无法读取，或不具备用量查询权限"}
        accounts = {item[1] for item in candidates if item[1]}
        organizations = {item[2] for item in candidates if item[2]}
        if (not account_id and len(accounts) > 1) or len(organizations) > 1:
            return {**unavailable, "error": "登录令牌对应多个账号或组织，无法安全确认用量归属"}
        valid = [item for item in candidates if item[3] > time.time() * 1000]
        if not valid:
            return {**unavailable, "error": "登录令牌已过期或没有有效到期时间"}
        # 优先 V2；同版本内选择有效期更长的令牌，不刷新或改写登录数据。
        entry = min(valid, key=lambda item: (item[0], -item[3]))[4]
        plan = entry.get('subscriptionType', 'unknown')
        tier = entry.get('rateLimitTier', '')
        plan = plan if isinstance(plan, str) else ''
        tier = tier if isinstance(tier, str) else ''
        token = entry.get('token', '')
        expires_ms = entry.get('expiresAt', 0)

        # 格式化 plan
        plan_display = plan.replace('_', ' ').title() if plan else '未知'

        # 过期时间
        expires_str = ""
        if expires_ms:
            try:
                dt = datetime.fromtimestamp(expires_ms / 1000)
                expires_str = dt.strftime("%Y-%m-%d %H:%M")
            except (OverflowError, OSError, ValueError):
                return {**unavailable, "error": "登录令牌的到期时间无效"}

        return {
            "plan": plan_display,
            "tier": tier,
            "token": token,
            "expires": expires_str,
        }

    @classmethod
    def fetch_usage(cls, token: str, max_tokens: int = 1, prompt: str = "a") -> dict:
        """通过 api.anthropic.com 获取用量数据 / 触发窗口预热

        发送一个最小请求，从响应头读 rate-limit 信息。
        max_tokens / prompt 用于自定义请求大小：
          - 默认 max_tokens=1, prompt="a" 仅用于探活（消耗 ~1 token）
          - 预热场景可以传 max_tokens=10~15 + 真实问题，行为更像真实用户
        返回字典：{
            'session_pct': 26,
            'session_reset_epoch': 1780225200,
            'weekly_pct': 2,
            'weekly_reset_epoch': 1780513200,
            'fetched_at': 1780207000,
        }
        """
        result = {}
        if not token:
            return result

        # 调 API（携带敏感 OAuth token，必须校验 TLS 证书）
        try:
            import requests

            r = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'Authorization': f'Bearer {token}',
                    'anthropic-version': '2023-06-01',
                    'anthropic-beta': 'oauth-2025-04-20',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'claude-haiku-4-5',
                    'max_tokens': max_tokens,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=15)

            if r.status_code == 200:
                h = r.headers
                result['fetched_at'] = int(time.time())
                # 5h 窗口 — 保存绝对时间戳，倒计时由前端实时计算
                util_5h = h.get('anthropic-ratelimit-unified-5h-utilization')
                reset_5h = h.get('anthropic-ratelimit-unified-5h-reset')
                if util_5h:
                    result['session_pct'] = int(round(float(util_5h) * 100))
                if reset_5h:
                    result['session_reset_epoch'] = int(reset_5h)
                # 7d 窗口
                util_7d = h.get('anthropic-ratelimit-unified-7d-utilization')
                reset_7d = h.get('anthropic-ratelimit-unified-7d-reset')
                if util_7d:
                    result['weekly_pct'] = int(round(float(util_7d) * 100))
                if reset_7d:
                    result['weekly_reset_epoch'] = int(reset_7d)
                if 'session_pct' in result or 'weekly_pct' in result:
                    result['ok'] = True
                else:
                    result['error'] = "服务未返回用量百分比"
            elif r.status_code == 429:
                # 额度耗尽 — 即使 429 响应，header 里也可能带 reset 信息
                result['fetched_at'] = int(time.time())
                result['rate_limited'] = True
                h = r.headers
                # 把已知信息尽量提取出来
                util_5h = h.get('anthropic-ratelimit-unified-5h-utilization')
                reset_5h = h.get('anthropic-ratelimit-unified-5h-reset')
                util_7d = h.get('anthropic-ratelimit-unified-7d-utilization')
                reset_7d = h.get('anthropic-ratelimit-unified-7d-reset')
                # 只展示响应里实际返回的百分比，不推断耗尽的窗口。
                if util_5h:
                    pct = float(util_5h) * 100
                    result['session_pct'] = int(round(pct))
                if reset_5h:
                    result['session_reset_epoch'] = int(reset_5h)
                if util_7d:
                    result['weekly_pct'] = int(round(float(util_7d) * 100))
                if reset_7d:
                    result['weekly_reset_epoch'] = int(reset_7d)
                # 解析 body 里的具体错误信息
                try:
                    body = r.json()
                    err_type = body.get('error', {}).get('type', '')
                    msg = body.get('error', {}).get('message', '')
                    result['error'] = f"额度已耗尽 ({err_type})"
                    result['error_msg'] = msg
                except Exception:
                    result['error'] = "额度已耗尽"
            elif r.status_code == 401:
                result['error'] = "Token 失效，需重新登录"
                result['token_invalid'] = True
            else:
                result['error'] = f"HTTP {r.status_code}"
        except Exception as e:
            result['error'] = type(e).__name__

        return result


# === 确认弹窗 ===

class ConfirmDialog(ctk.CTkToplevel):
    """自定义确认弹窗。当 cancel_text 为空时只显示确认按钮（变成纯提示弹窗）"""

    def __init__(self, parent, title: str, message: str,
                  confirm_text="确认", cancel_text="取消"):
        super().__init__(parent)
        self.result = False
        self.title(title)
        # 自适应高度：根据消息行数估算
        line_count = max(message.count("\n") + 1, 3)
        h = max(200, 90 + line_count * 22)
        w = 460
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=COLORS["bg_main"])

        # 居中
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")

        ctk.CTkLabel(self, text=message, font=("Microsoft YaHei UI", 12),
                     text_color=COLORS["text_primary"],
                     wraplength=w - 60, justify="left").pack(pady=(24, 16), padx=20)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(4, 16))

        if cancel_text:
            ctk.CTkButton(btn_frame, text=cancel_text, width=100, height=32,
                          fg_color=COLORS["bg_card"],
                          hover_color=COLORS["track"],
                          text_color=COLORS["text_secondary"],
                          border_width=1,
                          border_color=COLORS["border_default"],
                          command=self._cancel).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text=confirm_text, width=100, height=32,
                      fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"],
                      text_color="#FFFFFF",
                      command=self._confirm).pack(side="left", padx=10)

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _confirm(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()

    def get_result(self) -> bool:
        self.wait_window()
        return self.result


class EditNoteDialog(ctk.CTkToplevel):
    """编辑备注 + 预热配置弹窗"""

    # 返回结果约定：
    #   None        — 取消
    #   "__delete__" — 删除此 profile
    #   dict 形式    — {"note": str, "warmup": dict}

    def __init__(self, parent, name: str, current_note: str,
                 current_warmup: Optional[dict] = None,
                 on_test_warmup=None):
        super().__init__(parent)
        self.result = None
        self.title(f"编辑 - {name}")
        self.geometry("480x520")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=COLORS["bg_main"])
        self._on_test_warmup = on_test_warmup
        self._profile_name = name

        # 当前预热配置（深拷贝，避免直接改外部 dict）
        warmup = current_warmup or {}
        self._warmup_enabled = ctk.BooleanVar(value=bool(warmup.get("enabled")))
        self._warmup_max_tokens = ctk.StringVar(value=str(warmup.get("max_tokens", 10)))
        self._schedule_list = list(warmup.get("schedule", []))

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 480) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 520) // 2
        self.geometry(f"+{x}+{y}")

        # ==== 备注区 ====
        ctk.CTkLabel(self, text=f"「{name}」的备注（如邮箱/账号识别等，不要存密码）",
                     font=("Microsoft YaHei UI", 11),
                     text_color=COLORS["text_secondary"]).pack(pady=(20, 8), padx=20, anchor="w")

        self.entry = ctk.CTkEntry(self, width=440, height=36,
                                   font=("Microsoft YaHei UI", 12),
                                   fg_color=COLORS["bg_card"],
                                   border_color=COLORS["border_default"])
        self.entry.pack(padx=20)
        if current_note:
            self.entry.insert(0, current_note)
            self.entry.select_range(0, "end")

        # ==== 分割线 ====
        ctk.CTkFrame(self, height=1, fg_color=COLORS["divider"]).pack(
            fill="x", padx=20, pady=(18, 8))

        # ==== 预热配置区 ====
        warmup_header = ctk.CTkFrame(self, fg_color="transparent")
        warmup_header.pack(fill="x", padx=20, pady=(2, 6))

        ctk.CTkLabel(warmup_header, text="5h 窗口预热",
                     font=("Microsoft YaHei UI", 13, "bold"),
                     text_color=COLORS["text_primary"]).pack(side="left")

        # 启用开关
        ctk.CTkSwitch(warmup_header, text="启用",
                       variable=self._warmup_enabled,
                       font=("Microsoft YaHei UI", 11),
                       progress_color=COLORS["accent"],
                       text_color=COLORS["text_secondary"]).pack(side="right")

        ctk.CTkLabel(self,
                     text="到点自动发一条小请求激活 5h 窗口，让窗口结束时间对齐工作时段。",
                     font=("Microsoft YaHei UI", 10),
                     text_color=COLORS["text_tertiary"],
                     wraplength=440, justify="left").pack(padx=20, pady=(0, 8), anchor="w")

        # 时间列表
        time_row = ctk.CTkFrame(self, fg_color="transparent")
        time_row.pack(fill="x", padx=20, pady=(4, 4))
        ctk.CTkLabel(time_row, text="触发时间：",
                     font=("Microsoft YaHei UI", 11),
                     text_color=COLORS["text_secondary"], width=80, anchor="w").pack(side="left")

        self._time_chips_frame = ctk.CTkFrame(time_row, fg_color="transparent")
        self._time_chips_frame.pack(side="left", fill="x", expand=True)
        self._render_time_chips()

        # 添加时间输入
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(2, 4))
        ctk.CTkLabel(add_row, text="", width=80).pack(side="left")
        self._new_time_entry = ctk.CTkEntry(add_row, width=80, height=28,
                                              placeholder_text="HH:MM",
                                              font=("Microsoft YaHei UI", 11),
                                              fg_color=COLORS["bg_card"],
                                              border_color=COLORS["border_default"])
        self._new_time_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(add_row, text="+ 添加", width=70, height=28,
                      font=("Microsoft YaHei UI", 11),
                      fg_color=COLORS["accent_soft"],
                      hover_color="#F5DDD2",
                      text_color=COLORS["accent_hover"],
                      command=self._add_time).pack(side="left")

        # max_tokens 配置
        token_row = ctk.CTkFrame(self, fg_color="transparent")
        token_row.pack(fill="x", padx=20, pady=(8, 4))
        ctk.CTkLabel(token_row, text="单次消耗：",
                     font=("Microsoft YaHei UI", 11),
                     text_color=COLORS["text_secondary"], width=80, anchor="w").pack(side="left")
        ctk.CTkEntry(token_row, textvariable=self._warmup_max_tokens, width=60, height=28,
                      font=("Microsoft YaHei UI", 11),
                      fg_color=COLORS["bg_card"],
                      border_color=COLORS["border_default"]).pack(side="left")
        ctk.CTkLabel(token_row, text="tokens（建议 5-15，会随机 ±3）",
                     font=("Microsoft YaHei UI", 10),
                     text_color=COLORS["text_tertiary"]).pack(side="left", padx=(8, 0))

        # 立即测试按钮
        test_row = ctk.CTkFrame(self, fg_color="transparent")
        test_row.pack(fill="x", padx=20, pady=(6, 4))
        ctk.CTkLabel(test_row, text="", width=80).pack(side="left")
        ctk.CTkButton(test_row, text="立即测试一次", width=130, height=28,
                      font=("Microsoft YaHei UI", 11),
                      fg_color=COLORS["bg_card"],
                      hover_color=COLORS["track"],
                      text_color=COLORS["text_secondary"],
                      border_width=1,
                      border_color=COLORS["border_default"],
                      command=self._test_now).pack(side="left")
        last_run = warmup.get("last_run_date", "")
        if last_run:
            ctk.CTkLabel(test_row, text=f"上次：{last_run}",
                         font=("Microsoft YaHei UI", 10),
                         text_color=COLORS["text_tertiary"]).pack(side="left", padx=(10, 0))

        # ==== 底部按钮 ====
        ctk.CTkFrame(self, height=1, fg_color=COLORS["divider"]).pack(
            fill="x", padx=20, pady=(14, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=14)

        ctk.CTkButton(btn_frame, text="删除 Profile", width=100, height=32,
                      font=("Microsoft YaHei UI", 12),
                      fg_color=COLORS["bg_card"],
                      hover_color="#FBE5E1",
                      text_color=COLORS["red"],
                      border_width=1,
                      border_color=COLORS["border_default"],
                      command=self._delete).pack(side="left")

        right_group = ctk.CTkFrame(btn_frame, fg_color="transparent")
        right_group.pack(side="right")

        ctk.CTkButton(right_group, text="复制", width=60, height=32,
                      font=("Microsoft YaHei UI", 12),
                      fg_color=COLORS["accent_soft"],
                      hover_color="#F5DDD2",
                      text_color=COLORS["accent_hover"],
                      command=self._copy).pack(side="left", padx=4)
        ctk.CTkButton(right_group, text="取消", width=60, height=32,
                      font=("Microsoft YaHei UI", 12),
                      fg_color=COLORS["bg_card"],
                      hover_color=COLORS["track"],
                      text_color=COLORS["text_secondary"],
                      border_width=1,
                      border_color=COLORS["border_default"],
                      command=self._cancel).pack(side="left", padx=4)
        ctk.CTkButton(right_group, text="保存", width=60, height=32,
                      font=("Microsoft YaHei UI", 12, "bold"),
                      fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"],
                      text_color="#FFFFFF",
                      command=self._save).pack(side="left", padx=4)

        # 操作提示
        self.copy_hint = ctk.CTkLabel(self, text="", font=("Microsoft YaHei UI", 11),
                                       text_color=COLORS["green"])
        self.copy_hint.pack()

        self.entry.focus_set()
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _render_time_chips(self):
        """渲染当前 schedule 的时间标签（药丸样式 + 删除按钮）"""
        for w in self._time_chips_frame.winfo_children():
            w.destroy()
        if not self._schedule_list:
            ctk.CTkLabel(self._time_chips_frame, text="（未设置）",
                         font=("Microsoft YaHei UI", 11),
                         text_color=COLORS["text_tertiary"]).pack(side="left")
            return
        for hm in sorted(self._schedule_list):
            chip = ctk.CTkFrame(self._time_chips_frame, fg_color=COLORS["accent_soft"],
                                 corner_radius=10)
            chip.pack(side="left", padx=(0, 6), pady=2)
            ctk.CTkLabel(chip, text=hm,
                         font=("Microsoft YaHei UI", 11, "bold"),
                         text_color=COLORS["accent_hover"]).pack(side="left", padx=(8, 4), pady=2)
            ctk.CTkButton(chip, text="✕", width=18, height=18,
                          font=("Segoe UI", 10),
                          fg_color="transparent",
                          hover_color="#F5DDD2",
                          text_color=COLORS["accent_hover"],
                          corner_radius=9,
                          command=lambda t=hm: self._remove_time(t)).pack(side="left", padx=(0, 4))

    def _add_time(self):
        text = self._new_time_entry.get().strip()
        if not self._is_valid_hm(text):
            self.copy_hint.configure(text="时间格式必须是 HH:MM",
                                      text_color=COLORS["red"])
            self.after(2000, lambda: self.copy_hint.configure(text=""))
            return
        if text in self._schedule_list:
            self.copy_hint.configure(text="该时间已存在", text_color=COLORS["red"])
            self.after(2000, lambda: self.copy_hint.configure(text=""))
            return
        self._schedule_list.append(text)
        self._new_time_entry.delete(0, "end")
        self._render_time_chips()

    def _remove_time(self, hm: str):
        if hm in self._schedule_list:
            self._schedule_list.remove(hm)
            self._render_time_chips()

    @staticmethod
    def _is_valid_hm(text: str) -> bool:
        try:
            parts = text.split(":")
            if len(parts) != 2:
                return False
            h, m = int(parts[0]), int(parts[1])
            return 0 <= h <= 23 and 0 <= m <= 59
        except (ValueError, AttributeError):
            return False

    def _test_now(self):
        """立即测试一次预热（异步，不阻塞 UI）"""
        if not self._on_test_warmup:
            return
        try:
            max_tokens = max(5, int(self._warmup_max_tokens.get()))
        except ValueError:
            max_tokens = 10
        self.copy_hint.configure(text="正在发送测试请求...",
                                  text_color=COLORS["text_secondary"])
        self._on_test_warmup(self._profile_name, max_tokens, self._on_test_result)

    def _on_test_result(self, ok: bool, msg: str):
        self.copy_hint.configure(
            text=("✓ " if ok else "✗ ") + msg,
            text_color=COLORS["green"] if ok else COLORS["red"])
        self.after(4000, lambda: self.copy_hint.configure(text=""))

    def _copy(self):
        text = self.entry.get().strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.copy_hint.configure(text="已复制到剪贴板", text_color=COLORS["green"])
            self.after(2000, lambda: self.copy_hint.configure(text=""))
        else:
            self.copy_hint.configure(text="备注为空，无法复制", text_color=COLORS["red"])
            self.after(2000, lambda: self.copy_hint.configure(text=""))

    def _delete(self):
        self.result = "__delete__"
        self.destroy()

    def _save(self):
        try:
            mt = max(5, int(self._warmup_max_tokens.get()))
        except ValueError:
            mt = 10
        self.result = {
            "note": self.entry.get(),
            "warmup": {
                "enabled": bool(self._warmup_enabled.get()),
                "schedule": list(self._schedule_list),
                "max_tokens": mt,
            }
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def get_result(self):
        self.wait_window()
        return self.result


# === Profile 卡片 ===

class ProfileCard(ctk.CTkFrame):
    """单个 profile 卡片，使用进度条显示用量"""

    def __init__(self, master, name: str, note: str, is_active: bool,
                 account_info: dict, usage_info: dict,
                 on_switch, on_edit, on_delete, on_reload, on_refresh):
        bg = COLORS["bg_card_active"] if is_active else COLORS["bg_card"]
        border_color = COLORS["border_active"] if is_active else COLORS["border_default"]
        super().__init__(master, corner_radius=12, fg_color=bg, border_width=2,
                         border_color=border_color)
        self.name = name
        # 保存重置时间戳，供外部倒计时刷新使用
        self.session_reset_epoch = usage_info.get('session_reset_epoch')
        self.weekly_reset_epoch = usage_info.get('weekly_reset_epoch')

        self.grid_columnconfigure(1, weight=1)

        # ==== 顶部行：状态灯 + 名称 + 操作按钮 ====
        # 状态指示灯
        # 状态指示灯：根据额度状态反映健康度
        indicator_color = self._get_indicator_color(usage_info, is_active)
        self.indicator = ctk.CTkLabel(self, text="●", text_color=indicator_color,
                                       font=("", 16))
        self.indicator.grid(row=0, column=0, padx=(16, 8), pady=(14, 0), sticky="w")

        # 名称 + plan 标签
        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.grid(row=0, column=1, sticky="w", pady=(12, 0))

        ctk.CTkLabel(name_frame, text=name,
                     font=("Microsoft YaHei UI", 14, "bold"),
                     text_color=COLORS["text_primary"]).pack(side="left")

        plan = account_info.get("plan", "")
        if plan and plan != "未知":
            ctk.CTkLabel(name_frame, text=plan,
                         font=("Microsoft YaHei UI", 10, "bold"),
                         fg_color="#FFFFFF" if is_active else COLORS["accent_soft"],
                         text_color=COLORS["accent_hover"],
                         corner_radius=8).pack(side="left", padx=(8, 0), ipadx=7, ipady=1)

        # 额度状态标签（小药丸样式）
        if usage_info.get('rate_limited'):
            ctk.CTkLabel(name_frame, text="额度耗尽",
                         font=("Microsoft YaHei UI", 10, "bold"),
                         fg_color="#FBE5E1", text_color=COLORS["red"],
                         corner_radius=8).pack(side="left", padx=(6, 0), ipadx=7, ipady=1)
        elif usage_info.get('token_invalid'):
            ctk.CTkLabel(name_frame, text="登录失效",
                         font=("Microsoft YaHei UI", 10, "bold"),
                         fg_color="#FDEAD2", text_color="#B26A00",
                         corner_radius=8).pack(side="left", padx=(6, 0), ipadx=7, ipady=1)

        # 操作按钮区
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=(5, 12), pady=(8, 0), sticky="e")

        if not is_active:
            switch_btn = ctk.CTkButton(btn_frame, text="切换", width=60, height=30,
                                        font=("Microsoft YaHei UI", 12, "bold"),
                                        fg_color=COLORS["accent"],
                                        hover_color=COLORS["accent_hover"],
                                        text_color="#FFFFFF",
                                        command=lambda: on_switch(name))
            switch_btn.pack(side="left", padx=2)
        else:
            reload_btn = ctk.CTkButton(btn_frame, text="重载", width=60, height=30,
                                        font=("Microsoft YaHei UI", 12, "bold"),
                                        fg_color=COLORS["accent_soft"],
                                        hover_color="#F5DDD2",
                                        text_color=COLORS["accent_hover"],
                                        border_width=1,
                                        border_color=COLORS["border_default"],
                                        command=lambda: on_reload(name))
            reload_btn.pack(side="left", padx=2)

        # 卡片刷新按钮（图标）
        # 颜色取 text_secondary RGBA，跟标题栏图标一致
        refresh_color = (109, 110, 119, 255)
        if not hasattr(ProfileCard, '_refresh_icon_cache'):
            ProfileCard._refresh_icon_cache = ctk.CTkImage(
                light_image=make_caption_icon("refresh", 48, refresh_color),
                dark_image=make_caption_icon("refresh", 48, refresh_color),
                size=(14, 14))
        self.refresh_btn = ctk.CTkButton(btn_frame, text="", image=ProfileCard._refresh_icon_cache,
                                          width=32, height=30, corner_radius=8,
                                          fg_color=COLORS["bg_card"],
                                          hover_color=COLORS["track"],
                                          border_width=1,
                                          border_color=COLORS["border_default"],
                                          command=lambda: on_refresh(name))
        self.refresh_btn.pack(side="left", padx=2)

        edit_btn = ctk.CTkButton(btn_frame, text="编辑", width=50, height=30,
                                  font=("Microsoft YaHei UI", 11, "bold"),
                                  fg_color=COLORS["bg_card"],
                                  hover_color=COLORS["track"],
                                  text_color=COLORS["text_secondary"],
                                  border_width=1,
                                  border_color=COLORS["border_default"],
                                  command=lambda: on_edit(name))
        edit_btn.pack(side="left", padx=2)

        # ==== 第二行：备注 ====
        if note:
            note_label = ctk.CTkLabel(self, text=note,
                                       font=("Microsoft YaHei UI", 11),
                                       text_color=COLORS["text_secondary"], anchor="w")
            note_label.grid(row=1, column=1, columnspan=2, sticky="w",
                           padx=(0, 12), pady=(2, 4))

        # ==== 进度条区域 ====
        bars_frame = ctk.CTkFrame(self, fg_color="transparent")
        bars_frame.grid(row=2, column=0, columnspan=3, sticky="ew",
                       padx=(16, 12), pady=(6, 12))
        bars_frame.grid_columnconfigure(1, weight=1)

        # 5h 进度条
        if 'session_pct' in usage_info:
            self._build_progress_row(bars_frame, 0, "5h",
                                      usage_info.get('session_pct', 0),
                                      self.session_reset_epoch,
                                      label_attr='session_label',
                                      bar_attr='session_bar')
        else:
            ctk.CTkLabel(bars_frame, text="5h  -- 点击右上角刷新查看用量",
                         font=("Microsoft YaHei UI", 10),
                         text_color=COLORS["text_tertiary"]).grid(row=0, column=0, columnspan=3,
                                                     sticky="w", pady=2)

        # 7d 进度条
        if 'weekly_pct' in usage_info:
            self._build_progress_row(bars_frame, 1, "7d",
                                      usage_info.get('weekly_pct', 0),
                                      self.weekly_reset_epoch,
                                      label_attr='weekly_label',
                                      bar_attr='weekly_bar')

        # 数据更新时间
        info_bits = []
        fetched_at = usage_info.get('fetched_at')
        if fetched_at:
            info_bits.append(f"更新于 {datetime.fromtimestamp(fetched_at).strftime('%H:%M')}")
        if info_bits:
            ctk.CTkLabel(bars_frame, text="    ".join(info_bits),
                         font=("Microsoft YaHei UI", 10),
                         text_color=COLORS["text_tertiary"], anchor="w").grid(
                row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_progress_row(self, parent, row, label, pct, reset_epoch,
                            label_attr, bar_attr):
        """构建一行进度条（label + 进度条 + 百分比 + 倒计时）"""
        # 标签 (5h / 7d)
        ctk.CTkLabel(parent, text=label, width=24,
                     font=("Microsoft YaHei UI", 11, "bold"),
                     text_color=COLORS["text_primary"]).grid(
            row=row, column=0, sticky="w", pady=3)

        # 进度条
        try:
            pct_val = max(0.0, min(1.0, float(pct) / 100))
        except (ValueError, TypeError):
            pct_val = 0.0
        bar_color = self._get_bar_color(pct_val * 100)
        bar = ctk.CTkProgressBar(parent, height=10, corner_radius=5,
                                  progress_color=bar_color,
                                  fg_color=COLORS["track"])
        bar.set(pct_val)
        bar.grid(row=row, column=1, sticky="ew", padx=(10, 10), pady=4)
        setattr(self, bar_attr, bar)

        # 百分比 + 倒计时
        countdown = self._format_reset_time(reset_epoch) if reset_epoch else "--"
        text = f"{int(pct)}%   {countdown}"
        lbl = ctk.CTkLabel(parent, text=text, width=120,
                            font=("Microsoft YaHei UI", 11, "bold"),
                            text_color=COLORS["text_primary"], anchor="e")
        lbl.grid(row=row, column=2, sticky="e", pady=2)
        setattr(self, label_attr, lbl)

    @staticmethod
    def _get_bar_color(pct: float) -> str:
        """根据使用百分比返回进度条颜色（Claude 风格）"""
        if pct >= 90:
            return "#C25B3B"  # 深珊瑚红
        elif pct >= 70:
            return "#D97757"  # Claude 橙
        elif pct >= 40:
            return "#C49E3F"  # 暖黄
        return "#3B8C5C"      # 森林绿

    @staticmethod
    def _get_indicator_color(usage_info: dict, is_active: bool) -> str:
        """状态灯颜色：反映额度健康度"""
        # 已耗尽（429 / token 失效）
        if usage_info.get('rate_limited') or usage_info.get('token_invalid'):
            return "#C25B3B"  # 红 — 不可用
        # 取 5h / 7d 中较高的百分比作为健康度参考
        s = usage_info.get('session_pct', 0) or 0
        w = usage_info.get('weekly_pct', 0) or 0
        try:
            pct = max(int(s), int(w))
        except (TypeError, ValueError):
            pct = 0
        if pct >= 90:
            return "#C25B3B"  # 红
        if pct >= 70:
            return "#D97757"  # 橙
        # 没有用量数据 → 用默认状态色
        if not usage_info or ('session_pct' not in usage_info and 'weekly_pct' not in usage_info):
            return "#3B8C5C" if is_active else "#A8A7B0"  # 激活=绿，非激活=灰
        return "#3B8C5C"  # 绿 — 健康

    @staticmethod
    def _format_reset_time(epoch: int) -> str:
        """格式化重置时间为具体时间点：14:30 / 明天 09:00 / 6/3 14:30"""
        if not epoch:
            return "--"
        try:
            now = datetime.now()
            target = datetime.fromtimestamp(epoch)
            if target <= now:
                return "已恢复"
            today = now.date()
            target_date = target.date()
            delta_days = (target_date - today).days
            time_str = target.strftime("%H:%M")
            if delta_days == 0:
                return time_str
            if delta_days == 1:
                return f"明天 {time_str}"
            if delta_days == 2:
                return f"后天 {time_str}"
            # 7 天内显示星期，更远显示月日
            if delta_days < 7:
                weekday = "一二三四五六日"[target.weekday()]
                return f"周{weekday} {time_str}"
            return target.strftime("%-m/%-d %H:%M") if hasattr(target, 'strftime') else target.strftime("%m/%d %H:%M")
        except Exception:
            return "--"

    def update_countdown(self):
        """每秒调用，刷新倒计时数字（不需要重新调 API）"""
        if hasattr(self, 'session_label') and self.session_reset_epoch:
            try:
                bar = self.session_bar
                pct = int(bar.get() * 100)
                text = f"{pct}%   {self._format_reset_time(self.session_reset_epoch)}"
                self.session_label.configure(text=text)
            except Exception:
                pass
        if hasattr(self, 'weekly_label') and self.weekly_reset_epoch:
            try:
                bar = self.weekly_bar
                pct = int(bar.get() * 100)
                text = f"{pct}%   {self._format_reset_time(self.weekly_reset_epoch)}"
                self.weekly_label.configure(text=text)
            except Exception:
                pass

# === 预热调度器 ===

class WarmupScheduler(threading.Thread):
    """5h 窗口预热调度器：常驻后台线程，按 profile 配置的时间触发 1-2 token 请求

    设计要点：
      - 每 60s tick 一次，扫描所有启用预热的 profile
      - 当前时间 in profile.schedule 且当日未运行 → 触发
      - ±90s 抖动，避免准点（更像真实用户）
      - 单 profile 单日最多触发一次（无论 schedule 配几个时间）
      - max_tokens 在配置基础上随机 ±3，prompt 从池子里随机选
      - 任何错误都吞掉不让线程崩溃
    """

    def __init__(self, pm, on_status=None):
        super().__init__(daemon=True, name="WarmupScheduler")
        self.pm = pm
        self.on_status = on_status  # callable(profile_name, status_str)
        self._stop_event = threading.Event()
        self._jitter_until = {}  # {profile_name: epoch} — 抖动窗口的目标触发时间

    def stop(self):
        self._stop_event.set()

    def run(self):
        # 启动时不立刻跑，等 30s 让 UI 完全就绪
        if self._stop_event.wait(30):
            return
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                # 任何异常都不能让调度线程死掉
                pass
            # 每 60s 检查一次
            if self._stop_event.wait(60):
                break

    def _tick(self):
        """扫描所有 profile，触发到期的预热"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_hm = now.strftime("%H:%M")

        profiles = self.pm.get_profiles()
        for name in profiles:
            warmup = self.pm.get_warmup(name)
            if not warmup.get("enabled"):
                continue
            schedule = warmup.get("schedule", [])
            if not schedule:
                continue
            # 当日已经跑过 → 跳过
            if warmup.get("last_run_date") == today:
                continue

            # 检查是否到达任何一个 schedule 时间（精度到分钟）
            triggered = False
            for hm in schedule:
                if hm == current_hm:
                    triggered = True
                    break
                # 检查是否在抖动窗口内（之前到点了但还没到抖动目标时间）
                target = self._jitter_until.get((name, hm))
                if target and time.time() >= target:
                    triggered = True
                    # 清掉抖动记录
                    self._jitter_until.pop((name, hm), None)
                    break

            if triggered:
                # 加 0~90s 随机抖动；如果还没到抖动时间，先记下来等下一个 tick
                pending_target = self._jitter_until.get((name, hm))
                if not pending_target:
                    delay = random.randint(0, 90)
                    self._jitter_until[(name, hm)] = time.time() + delay
                    if delay > 0:
                        continue  # 这次 tick 不发，下次 tick 时间到了再发
                # 真正执行预热请求
                self._do_warmup(name, warmup, today)

    def _do_warmup(self, name: str, warmup: dict, today: str):
        """实际发送预热请求"""
        # 拿到 token：优先从 active 拿，否则从对应 profile config 拿
        try:
            current = self.pm.get_current()
            if name == current:
                ai = UsageFetcher.get_account_info()
            else:
                cfg = INSTANCE_DIR / name / "config.json"
                if not cfg.exists():
                    return
                ai = UsageFetcher.get_account_info(cfg)
            token = ai.get("token")
            if not token:
                return
        except Exception:
            return

        # 随机化参数（让请求更像自然用户）
        max_tokens = max(5, warmup.get("max_tokens", 10) + random.randint(-3, 3))
        prompt = random.choice(WARMUP_PROMPTS)

        try:
            result = UsageFetcher.fetch_usage(token, max_tokens=max_tokens, prompt=prompt)
            success = bool(result.get("ok"))
            if self.on_status:
                if success:
                    self.on_status(name, f"预热成功（{datetime.now().strftime('%H:%M')}）")
                else:
                    self.on_status(name, f"预热失败：{result.get('error', '未知')}")
        except Exception as e:
            if self.on_status:
                self.on_status(name, f"预热异常：{type(e).__name__}")

        # 无论成功失败都更新 last_run_date，避免反复重试
        warmup["last_run_date"] = today
        self.pm.save_warmup(name, warmup)


# === 额度用完自动换号 ===

class AutoSwitcher(threading.Thread):
    """后台每 3 分钟看一次当前号的额度，用完了就挑一个号切过去（挑号/防抖逻辑在 auto_switch.py）。"""

    def __init__(self, app):
        super().__init__(daemon=True, name="AutoSwitcher")
        self.app = app
        self.pm = app.pm
        self.state = auto_switch.State(INSTANCE_DIR)
        self._stop_event = threading.Event()
        self._next_probe = 0
        self._last_note = None

    def stop(self):
        self._stop_event.set()

    def cfg(self) -> dict:
        c = dict(auto_switch.DEFAULTS)
        c.update(self.pm.get_settings())
        return c

    def run(self):
        if self._stop_event.wait(45):
            return
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                self.state.log(f"出错：{type(e).__name__} {e}")
            if self._stop_event.wait(60):
                break

    def _note(self, text: str, log: bool = True):
        """界面上显示一句；变了才写日志。"""
        if text == self._last_note:
            return
        self._last_note = text
        if log and text:
            self.state.log(text)
        self.app.after(0, self.app._show_auto_info, text)

    def _probe(self, name: str, live: bool) -> Optional[dict]:
        ai = UsageFetcher.get_account_info() if live else \
            UsageFetcher.get_account_info(INSTANCE_DIR / name / "config.json")
        tok = ai.get("token")
        if not tok:
            return None
        u = UsageFetcher.fetch_usage(tok)
        self.app._cache_usage(name, u)
        return u if (u.get("ok") or u.get("rate_limited") or u.get("token_invalid")) else None

    def _tick(self):
        cfg = self.cfg()
        if not cfg.get("auto_switch") or self.app.is_switching:
            return
        current = self.pm.get_current()
        if not current or not self.pm.is_claude_running():
            return
        now = time.time()
        with self.app._cache_lock:
            cur = self.app._usage_cache.get(current)
        if now >= self._next_probe:
            old_pct = (cur or {}).get("session_pct")
            u = self._probe(current, live=True)
            if u and not u.get("token_invalid"):
                cur = u
                if u.get("session_pct") != old_pct:
                    self.app.after(0, self.app._refresh_profiles)
            self._next_probe = now + auto_switch.CHECK_SEC
        pct = auto_switch.eff_session(cur, now)
        last = max(self.state.data.get("last_switch_at", 0), self.app._last_manual_switch_at)
        d = auto_switch.decide(cur, now, last)
        if d == "ok":
            self._note("", log=False)
            return
        if d == "cooldown":
            self._note(f"「{current}」额度用完，刚换过号，过几分钟再换")
            return
        # 要换：先把数据旧的候选号刷新一下（只刷还在 5 小时窗口里的、或没数据的）
        profiles = self.pm.get_profiles()
        with self.app._cache_lock:
            snap = dict(self.app._usage_cache)
        for name in profiles:
            if name == current:
                continue
            u = snap.get(name)
            if u and u.get("token_invalid"):
                continue
            stale = u is None or ((u.get("session_reset_epoch") or 0) > now
                                  and now - (u.get("fetched_at") or 0) > 20 * 60)
            if stale:
                self._probe(name, live=False)
        with self.app._cache_lock:
            snap = dict(self.app._usage_cache)
        target, why, nxt = auto_switch.pick_target(
            profiles, current, snap, now, self.state.data.get("failures", {}))
        if not target:
            tail = f"，最早 {auto_switch.fmt_hm(nxt)} 有号恢复" if nxt else ""
            self._note(f"「{current}」额度用完，没有可换的号{tail}")
            return
        reason = f"「{current}」额度用完（5小时 {pct}%）→「{target}」（{why}）"
        self._last_note = None
        self.app.after(0, self.app._begin_auto_switch, target, reason)


# === 主窗口 ===

class App(ctk.CTk):

    def __init__(self, singleton_sock=None):
        super().__init__()
        self._singleton = singleton_sock
        self.title("Claude Desktop Switcher")
        self.minsize(500, 540)
        self.configure(fg_color=COLORS["bg_main"])

        if ICON_PATH.exists():
            self.iconbitmap(str(ICON_PATH))

        # 立即隐藏窗口，等所有初始化完成后再统一显示，避免"先小后大"
        self.withdraw()

        # 去边框
        self.overrideredirect(True)

        # 立即（同步）应用正确的几何，避免 overrideredirect 导致的尺寸丢失
        # 这样 _build_ui 拿到的是正确尺寸，卡片 grid 一次到位
        self._apply_initial_geometry()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.pm = ProfileManager()
        self.tray_icon = None
        self.is_switching = False
        self._last_usage_fetch = 0
        self._cache_lock = threading.Lock()
        self._usage_cache = self._load_usage_cache()
        self._drag_data = {"x": 0, "y": 0}

        self._build_ui()
        # 强制完成一次 idle 处理，让 ScrollableFrame 内部的 Canvas 先算出真实宽度
        # 不然首次画卡片时 Canvas 宽度还是 0，卡片会按最小尺寸渲染
        self.update_idletasks()
        self._refresh_profiles(fetch_usage=False)
        self._setup_tray()
        self._start_singleton_listener()
        self._tick_countdown()
        # 启动 5h 窗口预热调度器（守护线程，App 退出时自动结束）
        self._warmup_scheduler = WarmupScheduler(self.pm, on_status=self._on_warmup_status)
        self._warmup_scheduler.start()
        # 额度用完自动换号（守护线程）
        self._last_manual_switch_at = 0
        self._auto_switcher = AutoSwitcher(self)
        self._auto_switcher.start()
        self._show_auto_info("")
        self.bind("<Configure>", self._on_resize)
        # UI 全部就绪后再启用 taskbar 显示并 deiconify 窗口
        self.after(1, self._enable_taskbar)
        if not self._usage_cache:
            self.after(500, self.on_refresh_usage)
        # 启动后检查 Claude Desktop 环境：如果连 Claude 目录都没有，给明确提示
        self.after(800, self._check_environment)

    def _check_environment(self):
        """启动检查：Claude Desktop 是否已安装并登录过。"""
        # 没有任何 profile 且 Claude 配置目录也不存在 → 用户大概率没装/没登录 Claude Desktop
        if self.pm.get_profiles():
            return  # 已有 profile，跳过
        if (CLAUDE_DIR / "config.json").exists():
            return  # 当前已登录过 Claude Desktop
        # 弹一个友好提示
        try:
            ConfirmDialog(self, "首次使用提示",
                "未检测到 Claude Desktop 登录状态。\n\n"
                "使用本工具前请先完成以下三步：\n"
                "1. 在 https://claude.ai/download 下载并安装 Claude Desktop\n"
                "2. 启动 Claude Desktop\n"
                "3. 登录任意一个账号\n\n"
                "之后回到本工具点「＋ 保存当前登录为新 Profile」即可创建首个 Profile。",
                confirm_text="知道了",
                cancel_text="").get_result()
        except Exception:
            self.set_status("未检测到 Claude Desktop 登录状态，请先登录后再创建 Profile")

    def _on_resize(self, event):
        """窗口尺寸变化时重新应用圆角形状"""
        # 只对主窗口的 Configure 事件做响应（不响应子控件的）
        if event.widget is self:
            self._apply_round_corners()

    def _set_filter(self, mode: str):
        """切换筛选模式：all / available / exhausted"""
        if mode == self._filter_mode:
            return
        self._filter_mode = mode
        # 更新筛选按钮的视觉状态
        for k, btn in self._filter_buttons.items():
            if k == mode:
                btn.configure(fg_color=COLORS["accent"],
                              hover_color=COLORS["accent_hover"],
                              text_color="#FFFFFF",
                              border_width=0)
            else:
                btn.configure(fg_color=COLORS["bg_card"],
                              hover_color=COLORS["track"],
                              text_color=COLORS["text_secondary"],
                              border_width=1,
                              border_color=COLORS["border_default"])
        self._refresh_profiles()

    def _start_singleton_listener(self):
        """监听单实例端口：当用户再次启动程序时，把本窗口唤到前台而不是新开一个"""
        if not self._singleton:
            return

        def serve():
            while True:
                try:
                    conn, _ = self._singleton.accept()
                except OSError:
                    break
                try:
                    conn.recv(16)
                finally:
                    conn.close()
                self.after(0, self._show_window)

        threading.Thread(target=serve, daemon=True).start()

    def _apply_initial_geometry(self):
        """同步设置窗口初始尺寸 + 居中（在 _build_ui 之前调用）"""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 540, 720
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 40)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

    def _enable_taskbar(self):
        """让 overrideredirect 窗口在 Windows 任务栏显示 + 套圆角，最后显示窗口"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            # 设置 WS_EX_APPWINDOW 让任务栏显示
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

            self._hwnd = hwnd
            self._apply_round_corners()

            # 现在所有 UI 已经按正确尺寸渲染好，直接显示窗口
            self.deiconify()
        except Exception:
            # 即使 win32 调用失败，也要保证窗口能显示出来
            try:
                self.deiconify()
            except Exception:
                pass

    def _apply_round_corners(self):
        """用 GDI region 给窗口切出圆角形状（Win10/Win11 都生效）"""
        try:
            if getattr(self, '_is_maximized', False):
                # 最大化时不要圆角，让窗口完全填满工作区
                ctypes.windll.user32.SetWindowRgn(self._hwnd, 0, True)
                return
            w = self.winfo_width()
            h = self.winfo_height()
            if w <= 1 or h <= 1:
                return
            # 关键：声明返回值为 HANDLE，64 位 Windows 上不声明会被截断为 32 位 → 拿到非法 region 句柄 → 黑屏
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
            radius = 14
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
            ctypes.windll.user32.SetWindowRgn(self._hwnd, rgn, True)
        except Exception:
            pass

    def _tick_countdown(self):
        """定期刷新卡片显示（重置时间为绝对时间点，30 秒刷一次足够应对跨日变化）"""
        try:
            for name, (card, _) in getattr(self, '_cards', {}).items():
                card.update_countdown()
        except Exception:
            pass
        self.after(30000, self._tick_countdown)

    def _on_warmup_status(self, profile_name: str, status: str):
        """WarmupScheduler 回调：自动预热成功/失败时更新状态栏并刷新卡片"""
        # 切回主线程显示
        self.after(0, lambda: self.set_status(f"「{profile_name}」{status}"))
        # 预热成功后刷新一次卡片，显示新的 5h 用量
        if "成功" in status:
            self.after(0, lambda: self._refresh_profiles(fetch_usage=False))

    def _load_usage_cache(self) -> dict:
        """从磁盘加载用量缓存（只保留还有效的）"""
        if not USAGE_CACHE_FILE.exists():
            return {}
        try:
            data = json.loads(USAGE_CACHE_FILE.read_text(encoding='utf-8'))
            now = time.time()
            # 过滤掉两边窗口都已过期的条目
            valid = {}
            for name, usage in data.items():
                s_reset = usage.get('session_reset_epoch', 0)
                w_reset = usage.get('weekly_reset_epoch', 0)
                # 至少有一个窗口还没过期就保留
                if s_reset > now or w_reset > now:
                    valid[name] = usage
            return valid
        except Exception:
            return {}

    def _cache_usage(self, name: str, usage: dict):
        """线程安全地写入某账号的用量缓存（仅保存有效结果）"""
        if usage and (usage.get('ok') or usage.get('rate_limited')
                      or usage.get('token_invalid')):
            with self._cache_lock:
                self._usage_cache[name] = usage

    def _save_usage_cache(self):
        """把当前缓存写到磁盘"""
        try:
            with self._cache_lock:
                snapshot = dict(self._usage_cache)
            USAGE_CACHE_FILE.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding='utf-8')
        except Exception:
            pass

    def _build_ui(self):
        # ==== 自定义标题栏 ====
        title_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_main"], height=44,
                                  corner_radius=0)
        title_bar.pack(fill="x", padx=0, pady=0)
        title_bar.pack_propagate(False)

        # 应用图标 + 名称（左侧）
        title_left = ctk.CTkFrame(title_bar, fg_color="transparent")
        title_left.pack(side="left", padx=12, pady=4)

        # 图标（直接用 Claude 官方图标）
        try:
            icon_img = load_claude_icon(40)
            self._title_icon = ctk.CTkImage(light_image=icon_img,
                                              dark_image=icon_img, size=(20, 20))
            ctk.CTkLabel(title_left, image=self._title_icon, text="").pack(
                side="left", padx=(0, 8))
        except Exception:
            pass

        title_label = ctk.CTkLabel(title_left, text="Claude Desktop Switcher",
                                    font=("Microsoft YaHei UI", 12, "bold"),
                                    text_color=COLORS["text_primary"])
        title_label.pack(side="left")

        # 窗口控制按钮（右侧）
        title_right = ctk.CTkFrame(title_bar, fg_color="transparent")
        title_right.pack(side="right", padx=0, pady=0)

        # 准备线条图标（48px 源图，再缩放到 ~20px 显示，抗锯齿质量更好）
        icon_color = (25, 25, 24, 255)  # COLORS["text_primary"] 对应的 RGBA
        self._caption_icons = {
            "minimize": ctk.CTkImage(
                light_image=make_caption_icon("minimize", 48, icon_color),
                dark_image=make_caption_icon("minimize", 48, icon_color),
                size=(20, 20)),
            "maximize": ctk.CTkImage(
                light_image=make_caption_icon("maximize", 48, icon_color),
                dark_image=make_caption_icon("maximize", 48, icon_color),
                size=(18, 18)),
            "restore": ctk.CTkImage(
                light_image=make_caption_icon("restore", 48, icon_color),
                dark_image=make_caption_icon("restore", 48, icon_color),
                size=(18, 18)),
            "close": ctk.CTkImage(
                light_image=make_caption_icon("close", 48, icon_color),
                dark_image=make_caption_icon("close", 48, icon_color),
                size=(18, 18)),
        }

        # Win11 风格：每个按钮 52×40 大小，悬停才有底色，无圆角
        btn_kw = dict(width=52, height=40, fg_color="transparent",
                      corner_radius=0, text="")

        # 最小化
        min_btn = ctk.CTkButton(title_right, image=self._caption_icons["minimize"],
                                 hover_color=COLORS["track"],
                                 command=self._minimize, **btn_kw)
        min_btn.pack(side="left")

        # 最大化/还原
        self._is_maximized = False
        self.max_btn = ctk.CTkButton(title_right, image=self._caption_icons["maximize"],
                                       hover_color=COLORS["track"],
                                       command=self._toggle_maximize, **btn_kw)
        self.max_btn.pack(side="left")

        # 关闭（悬停时变红底白线）
        close_btn = ctk.CTkButton(title_right, image=self._caption_icons["close"],
                                    hover_color="#C42B1C",
                                    command=self.on_close, **btn_kw)
        close_btn.pack(side="left")

        # 标题栏可拖动（绑定到所有子控件，但按钮区域不绑）
        for widget in (title_bar, title_left, title_label):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<Double-Button-1>", lambda e: self._toggle_maximize())
        # 图标也可拖
        if hasattr(self, '_title_icon'):
            for child in title_left.winfo_children():
                child.bind("<Button-1>", self._start_drag)
                child.bind("<B1-Motion>", self._do_drag)
                child.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        # ==== 主体内容区 ====
        # 主标题区
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(12, 6))

        self.title_label = ctk.CTkLabel(header, text="账号管理",
                     font=("Microsoft YaHei UI", 20, "bold"),
                     text_color=COLORS["text_primary"])
        self.title_label.pack(side="left")

        # 刷新所有账号用量（图标按钮）
        refresh_icon_color = (109, 110, 119, 255)  # text_secondary
        self._refresh_icon = ctk.CTkImage(
            light_image=make_caption_icon("refresh", 48, refresh_icon_color),
            dark_image=make_caption_icon("refresh", 48, refresh_icon_color),
            size=(16, 16))
        self.refresh_btn = ctk.CTkButton(header, text="", image=self._refresh_icon,
                                          width=36, height=36, corner_radius=18,
                                          fg_color=COLORS["bg_card"],
                                          hover_color=COLORS["accent_soft"],
                                          border_width=1,
                                          border_color=COLORS["border_default"],
                                          command=self.on_refresh_usage)
        self.refresh_btn.pack(side="right", pady=(2, 0))

        # 分割线
        ctk.CTkFrame(self, height=1, fg_color=COLORS["divider"]).pack(
            fill="x", padx=24, pady=(8, 4))

        # 筛选 tab 栏
        self._filter_mode = "all"  # all / available / exhausted
        filter_bar = ctk.CTkFrame(self, fg_color="transparent")
        filter_bar.pack(fill="x", padx=24, pady=(8, 0))

        self._filter_buttons = {}
        for key, label in [("all", "全部"), ("available", "可用"), ("exhausted", "已耗尽")]:
            btn = ctk.CTkButton(filter_bar, text=label, width=72, height=28,
                                 font=("Microsoft YaHei UI", 11, "bold"),
                                 corner_radius=14,
                                 fg_color=COLORS["accent"] if key == "all" else COLORS["bg_card"],
                                 hover_color=COLORS["accent_hover"] if key == "all" else COLORS["track"],
                                 text_color="#FFFFFF" if key == "all" else COLORS["text_secondary"],
                                 border_width=0 if key == "all" else 1,
                                 border_color=COLORS["border_default"],
                                 command=lambda k=key: self._set_filter(k))
            btn.pack(side="left", padx=(0, 6))
            self._filter_buttons[key] = btn

        # Profile 列表容器
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                                    scrollbar_button_color=COLORS["track"],
                                                    scrollbar_button_hover_color=COLORS["text_tertiary"])
        self.scroll_frame.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        # 底部区域
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=24, pady=(4, 16))

        self.create_btn = ctk.CTkButton(bottom, text="＋  保存当前登录为新 Profile",
                                         font=("Microsoft YaHei UI", 13),
                                         height=40, corner_radius=8,
                                         fg_color=COLORS["accent"],
                                         hover_color=COLORS["accent_hover"],
                                         text_color="#FFFFFF",
                                         command=self.on_create)
        self.create_btn.pack(fill="x", pady=(4, 4))

        self.new_login_btn = ctk.CTkButton(bottom, text="登录其他账号",
                                            font=("Microsoft YaHei UI", 13),
                                            height=40, corner_radius=8,
                                            fg_color=COLORS["bg_card"],
                                            hover_color=COLORS["accent_soft"],
                                            text_color=COLORS["accent_hover"],
                                            border_width=1,
                                            border_color=COLORS["border_default"],
                                            command=self.on_new_login)
        self.new_login_btn.pack(fill="x", pady=(0, 4))

        # 切换后自动发“继续”
        s = self.pm.get_settings()
        self.auto_continue_var = ctk.BooleanVar(value=bool(s.get("auto_continue")))

        def _toggle_auto():
            cur = self.pm.get_settings()
            cur["auto_continue"] = bool(self.auto_continue_var.get())
            self.pm.save_settings(cur)

        ctk.CTkCheckBox(bottom, variable=self.auto_continue_var, command=_toggle_auto,
                        text=f"切换后在「{s['session_title']}」发「{s['text']}」",
                        font=("Microsoft YaHei UI", 12),
                        text_color=COLORS["text_secondary"],
                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                        checkbox_width=18, checkbox_height=18).pack(anchor="w", pady=(4, 0))

        # 额度用完自动换号
        a = dict(auto_switch.DEFAULTS)
        a.update(s)
        self.auto_switch_var = ctk.BooleanVar(value=bool(a.get("auto_switch")))

        def _toggle_switch():
            cur = self.pm.get_settings()
            cur["auto_switch"] = bool(self.auto_switch_var.get())
            self.pm.save_settings(cur)
            self._show_auto_info("")

        ctk.CTkCheckBox(bottom, variable=self.auto_switch_var, command=_toggle_switch,
                        text="额度用完自动换号",
                        font=("Microsoft YaHei UI", 12),
                        text_color=COLORS["text_secondary"],
                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                        checkbox_width=18, checkbox_height=18).pack(anchor="w", pady=(4, 0))
        self.auto_info_var = ctk.StringVar(value="")
        ctk.CTkLabel(bottom, textvariable=self.auto_info_var,
                     font=("Microsoft YaHei UI", 11), text_color=COLORS["text_tertiary"],
                     wraplength=440, justify="left").pack(anchor="w", padx=(26, 0))

        # 状态栏
        self.status_var = ctk.StringVar(value="就绪")
        self.status_label = ctk.CTkLabel(bottom, textvariable=self.status_var,
                                          font=("Microsoft YaHei UI", 11),
                                          text_color=COLORS["text_dim"])
        self.status_label.pack(pady=(4, 0))

    # === 窗口拖动 ===

    def _start_drag(self, event):
        self._drag_data["x"] = event.x_root - self.winfo_x()
        self._drag_data["y"] = event.y_root - self.winfo_y()

    def _do_drag(self, event):
        x = event.x_root - self._drag_data["x"]
        y = event.y_root - self._drag_data["y"]
        self.geometry(f"+{x}+{y}")

    def _minimize(self):
        """最小化到任务栏（无边框窗口走 Win32 ShowWindow，点任务栏可还原）"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            self.withdraw()  # 兜底：退到托盘

    def _toggle_maximize(self):
        """切换最大化/还原"""
        try:
            if self._is_maximized:
                # 还原
                self.geometry(self._prev_geom)
                self.max_btn.configure(image=self._caption_icons["maximize"])
                self._is_maximized = False
            else:
                # 记下当前位置和大小
                self._prev_geom = f"{self.winfo_width()}x{self.winfo_height()}+{self.winfo_x()}+{self.winfo_y()}"
                # 取工作区大小（去掉任务栏）
                from ctypes import wintypes
                rect = wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                self.geometry(f"{w}x{h}+{rect.left}+{rect.top}")
                self.max_btn.configure(image=self._caption_icons["restore"])
                self._is_maximized = True
        except Exception:
            pass

    def _refresh_profiles(self, fetch_usage: bool = False):
        """刷新 profile 列表
        fetch_usage: True=主动调 API 获取最新用量；False=只用缓存数据
        """
        # 每次刷新顺便持久化用量缓存（成本极低）
        self._save_usage_cache()

        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        profiles = self.pm.get_profiles()
        current = self.pm.get_current()
        meta = self.pm.get_meta()

        if hasattr(self, 'title_label'):
            self.title_label.configure(
                text=f"账号管理 · {len(profiles)}" if profiles else "账号管理")

        if not profiles:
            empty_label = ctk.CTkLabel(self.scroll_frame,
                                        text="还没有 Profile\n\n请先在 Claude Desktop 登录账号\n然后点击下方按钮保存",
                                        font=("Microsoft YaHei UI", 13),
                                        text_color=COLORS["text_dim"])
            empty_label.pack(pady=50)
            return

        self._cards = {}
        hidden_count = 0
        for name in profiles:
            is_active = (name == current)
            note = meta.get(name, {}).get("note", "")

            account_info = {}
            if is_active:
                account_info = UsageFetcher.get_account_info()
            else:
                profile_config = INSTANCE_DIR / name / "config.json"
                if profile_config.exists():
                    account_info = UsageFetcher.get_account_info(profile_config)

            # 用量数据：从缓存读
            usage_info = dict(self._usage_cache.get(name, {}))

            # 应用筛选（不直接 continue，记录用量缓存以保留状态）
            if not self._match_filter(usage_info):
                hidden_count += 1
                continue

            card = ProfileCard(self.scroll_frame, name, note, is_active,
                              account_info=account_info,
                              usage_info=usage_info,
                              on_switch=self.on_switch,
                              on_edit=self.on_edit,
                              on_delete=self.on_delete,
                              on_reload=self.on_reload,
                              on_refresh=self.on_refresh_one)
            card.pack(fill="x", pady=6)
            self._cards[name] = (card, account_info)

        # 若筛选后无结果，显示提示
        if not self._cards and hidden_count > 0:
            tip_label = ctk.CTkLabel(self.scroll_frame,
                                      text=f"当前筛选下没有账号\n\n（{hidden_count} 个被隐藏）",
                                      font=("Microsoft YaHei UI", 12),
                                      text_color=COLORS["text_tertiary"])
            tip_label.pack(pady=40)

    def _match_filter(self, usage_info: dict) -> bool:
        """判断当前 usage 是否符合筛选条件"""
        mode = getattr(self, '_filter_mode', 'all')
        if mode == 'all':
            return True
        is_exhausted = bool(usage_info.get('rate_limited') or usage_info.get('token_invalid'))
        if mode == 'exhausted':
            return is_exhausted
        if mode == 'available':
            return not is_exhausted
        return True

    def on_refresh_one(self, name: str):
        """刷新单个账号的用量（独立节流，避免反复点击）"""
        # 单账号节流：每个账号 15 秒一次
        per_card_throttle = getattr(self, '_per_card_last_fetch', {})
        now = time.time()
        last = per_card_throttle.get(name, 0)
        if now - last < 15:
            wait = int(15 - (now - last))
            self.set_status(f"「{name}」请等 {wait} 秒后再刷新")
            return
        per_card_throttle[name] = now
        self._per_card_last_fetch = per_card_throttle

        # 找出该账号的 token
        is_active = (name == self.pm.get_current())
        if is_active:
            ai = UsageFetcher.get_account_info()
        else:
            cfg = INSTANCE_DIR / name / "config.json"
            if not cfg.exists():
                self.set_status(f"「{name}」配置缺失")
                return
            ai = UsageFetcher.get_account_info(cfg)
        token = ai.get('token')
        if not token:
            self.set_status(f"「{name}」{ai.get('error', '没有可用的登录令牌')}")
            return

        # 禁用对应卡片的刷新按钮
        card_tuple = self._cards.get(name)
        if card_tuple:
            card, _ = card_tuple
            try:
                card.refresh_btn.configure(state="disabled")
            except Exception:
                pass

        self.set_status(f"正在刷新「{name}」...")

        def worker():
            usage = UsageFetcher.fetch_usage(token)
            if usage and (usage.get('ok') or usage.get('rate_limited')
                          or usage.get('token_invalid')):
                with self._cache_lock:
                    self._usage_cache[name] = usage

            def finish():
                ts = datetime.now().strftime("%H:%M")
                if usage.get('ok') or usage.get('rate_limited'):
                    self.set_status(f"「{name}」已更新（{ts}）")
                else:
                    self.set_status(f"「{name}」刷新失败：{usage.get('error', '未知错误')}")
                self._refresh_profiles(fetch_usage=False)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def on_refresh_usage(self):
        """手动刷新所有账号用量（带节流：30 秒内不重复调 API）"""
        now = time.time()
        elapsed = now - self._last_usage_fetch
        if elapsed < 30:
            wait = int(30 - elapsed)
            self.set_status(f"请等待 {wait} 秒后再刷新（避免频繁调用 API）")
            return

        profiles = self.pm.get_profiles()
        if not profiles:
            self.set_status("没有 Profile 可刷新")
            return

        self._last_usage_fetch = now
        count = len(profiles)
        self.set_status(f"正在获取 {count} 个账号的用量...")
        self.refresh_btn.configure(state="disabled")

        # 收集每个 profile 的 token
        targets = []
        missing = {}
        for name in profiles:
            is_active = (name == self.pm.get_current())
            if is_active:
                ai = UsageFetcher.get_account_info()
            else:
                cfg = INSTANCE_DIR / name / "config.json"
                if not cfg.exists():
                    missing[name] = {'error': '账号配置缺失'}
                    continue
                ai = UsageFetcher.get_account_info(cfg)
            token = ai.get('token')
            if token:
                targets.append((name, token))
            else:
                missing[name] = {'error': ai.get('error', '没有可用的登录令牌')}

        # 重新渲染卡片，显示"加载中"
        self._refresh_profiles(fetch_usage=False)

        if not targets:
            self._on_all_usage_done(missing)
            return

        # 后台并发获取所有有令牌的账号；缺少令牌的账号也要报告失败。
        threading.Thread(target=self._fetch_all_usage, args=(targets, missing), daemon=True).start()

    def _fetch_all_usage(self, targets: list, missing: dict = None):
        """并发获取所有账号的用量数据"""
        threads = []
        results = dict(missing or {})
        lock = threading.Lock()

        def worker(name, token):
            usage = UsageFetcher.fetch_usage(token)
            with lock:
                results[name] = usage

        for name, token in targets:
            t = threading.Thread(target=worker, args=(name, token), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=20)

        # 缓存所有结果，并在主线程刷新 UI
        for name, usage in results.items():
            self._cache_usage(name, usage)

        self.after(0, self._on_all_usage_done, results)

    def _on_all_usage_done(self, results: dict):
        """所有账号用量获取完成后刷新界面"""
        self.refresh_btn.configure(state="normal")
        # 成功 = ok（正常）或 rate_limited（额度耗尽，也算成功获取到状态）
        success_count = sum(1 for r in results.values()
                            if r.get('ok') or r.get('rate_limited'))
        # 真正失败 = 网络错误 / 401 token 失效 / 其他 HTTP 错误
        failed = [(name, r.get('error', '未知错误'))
                  for name, r in results.items()
                  if not (r.get('ok') or r.get('rate_limited'))]
        total = len(results)
        ts = datetime.now().strftime("%H:%M")
        if not failed:
            self.set_status(f"{success_count}/{total} 账号已更新（{ts}）")
        else:
            reasons = {reason for _, reason in failed}
            details = (next(iter(reasons)) if len(reasons) == 1
                       else "；".join(f"{name}: {reason}" for name, reason in failed))
            self.set_status(f"{success_count}/{total} 账号已更新；{details}")
        # 重新渲染卡片以显示新数据
        self._refresh_profiles(fetch_usage=False)

    def _switch_and_record(self, name: str, kind: str, reason: str = "") -> tuple[bool, str]:
        """后台线程里切换；切完查一次新号用量，再写一条换号记录（_switch_history.jsonl）供复盘。"""
        with self._cache_lock:
            before = dict(self._usage_cache.get(name) or {})
        success, msg = self.pm.switch_profile(name,
            progress_cb=lambda t: self.after(0, self.set_status, t),
            on_old_usage_fetched=self._cache_usage)
        after = None
        if success:
            try:
                new_token = UsageFetcher.get_account_info().get('token')
                if new_token:
                    after = UsageFetcher.fetch_usage(new_token)
                    self._cache_usage(name, after)
            except Exception:
                pass
        try:
            now = time.time()
            rec = dict(getattr(self.pm, "last_switch_record", None) or {"to": name, "ok": success, "msg": msg})
            rec.update(kind=kind, reason=reason, target_before=auto_switch.brief(before, now),
                       target_after=auto_switch.brief(after, now))
            auto_switch.append_history(INSTANCE_DIR, rec)
        except Exception:
            pass
        return success, msg

    def _show_auto_info(self, note: str):
        """自动换号那一行：等待中的提示优先，否则显示上次自动换号。"""
        if not hasattr(self, "auto_info_var"):
            return
        if not self.auto_switch_var.get():
            self.auto_info_var.set("")
            return
        sw = getattr(self, "_auto_switcher", None)
        last = sw.state.data.get("last_info", "") if sw else ""
        self.auto_info_var.set(note or (f"上次：{last}" if last else ""))

    def _begin_auto_switch(self, name: str, reason: str):
        """主线程：开始一次自动换号（走和托盘一样的切换流程，切完照旧发“继续”）。"""
        if self.is_switching or not self.auto_switch_var.get():
            return
        st = self._auto_switcher.state
        st.data["last_switch_at"] = time.time()
        st.data["last_info"] = f"{datetime.now():%H:%M} {reason}"
        st.save()
        st.log("开始自动换号：" + reason)
        self.is_switching = True
        self.create_btn.configure(state="disabled")
        self.new_login_btn.configure(state="disabled")
        self._show_auto_info("")
        self.set_status("自动换号：" + reason)

        def do_switch():
            success, msg = self._switch_and_record(name, "自动", reason)
            if not success or "登出" in msg:
                st.data.setdefault("failures", {})[name] = time.time()
                st.data["last_info"] += "（失败）"
                st.log(f"自动换号失败：{msg}")
            else:
                st.log(f"自动换号完成：{msg}")
            st.save()

            def finish():
                self.is_switching = False
                self.create_btn.configure(state="normal")
                self.new_login_btn.configure(state="normal")
                self.set_status(msg)
                self._show_auto_info("")
                self._refresh_profiles()
                self._update_tray_menu()
            self.after(0, finish)

        threading.Thread(target=do_switch, daemon=True).start()

    def set_status(self, text: str):
        self.status_var.set(text)
        self.update_idletasks()

    def on_switch(self, name: str):
        """切换 profile（带确认弹窗）"""
        if self.is_switching:
            return
        storage_error = self.pm.session_storage_error()
        if storage_error:
            self.set_status(storage_error)
            return
        # 确认弹窗
        dialog = ConfirmDialog(self, "切换确认",
                               f"确定要切换到 「{name}」 吗？\n\nClaude Desktop 会被强制关闭再重启，\n里面正在跑的对话会被中断（记录保留，之后发“继续”接上）。",
                               confirm_text="切换")
        if not dialog.get_result():
            return

        self.is_switching = True
        self._last_manual_switch_at = time.time()
        self.create_btn.configure(state="disabled")
        self.new_login_btn.configure(state="disabled")

        def do_switch():
            # 切换前自动获取旧账号用量，同时更新目标账号用量
            success, msg = self._switch_and_record(name, "按钮")

            def finish():
                self.is_switching = False
                self.create_btn.configure(state="normal")
                self.new_login_btn.configure(state="normal")
                self.set_status(msg)
                self._refresh_profiles()
                self._update_tray_menu()
            self.after(0, finish)

        threading.Thread(target=do_switch, daemon=True).start()

    def on_reload(self, name: str):
        """重新加载当前 profile 的已保存会话"""
        if self.is_switching:
            return
        storage_error = self.pm.session_storage_error()
        if storage_error:
            self.set_status(storage_error)
            return
        dialog = ConfirmDialog(self, "重载确认",
                               f"重新加载 「{name}」 的已保存登录状态？\n\nClaude Desktop 会被强制关闭再重启，正在跑的对话会被中断。",
                               confirm_text="重载")
        if not dialog.get_result():
            return

        self.is_switching = True
        self.create_btn.configure(state="disabled")

        def do_reload():
            if not self.pm.stop_claude():
                def blocked():
                    self.is_switching = False
                    self.create_btn.configure(state="normal")
                    self.set_status("Claude 没能退出，原会话未改动；请在任务管理器结束 Claude 后重试")
                self.after(0, blocked)
                return
            time.sleep(1)
            self.after(0, self.set_status, "重新加载会话...")
            self.pm.load_session(name)
            self.after(0, self.set_status, "启动 Claude...")
            started = self.pm.start_claude()
            def finish():
                self.is_switching = False
                self.create_btn.configure(state="normal")
                self.set_status(f"已重载 '{name}'" if started
                                else f"已重载 '{name}'，但 Claude 没启动起来，请从开始菜单手动打开")
                self._refresh_profiles()
            self.after(0, finish)

        threading.Thread(target=do_reload, daemon=True).start()

    def on_new_login(self):
        """清空会话文件，让 Claude 启动后显示登录页"""
        if self.is_switching:
            return
        storage_error = self.pm.session_storage_error()
        if storage_error:
            self.set_status(storage_error)
            return
        dialog = ConfirmDialog(self, "登录新账号",
                               "将保存当前会话并清空登录状态，\nClaude 会被强制关闭再重启，重启后请登录新账号。",
                               confirm_text="继续")
        if not dialog.get_result():
            return

        self.is_switching = True
        self.create_btn.configure(state="disabled")
        self.new_login_btn.configure(state="disabled")

        def do_new_login():
            current = self.pm.get_current()
            if not current or not (INSTANCE_DIR / current / "config.json").exists():
                def no_backup():
                    self.is_switching = False
                    self.create_btn.configure(state="normal")
                    self.new_login_btn.configure(state="normal")
                    self.set_status("请先保存当前登录为 Profile，再登录其他账号")
                self.after(0, no_backup)
                return
            if not self.pm.stop_claude():
                def blocked():
                    self.is_switching = False
                    self.create_btn.configure(state="normal")
                    self.new_login_btn.configure(state="normal")
                    self.set_status("Claude 没能退出，原会话未改动；请在任务管理器结束 Claude 后重试")
                self.after(0, blocked)
                return
            time.sleep(1)
            if current:
                self.after(0, self.set_status, "保存当前会话...")
                self.pm.save_session(current)
            self.after(0, self.set_status, "清空会话...")
            for f in SESSION_FILES:
                p = CLAUDE_DIR / f
                if p.exists():
                    p.unlink()
            for d in SESSION_DIRS:
                p = CLAUDE_DIR / d
                if p.exists():
                    shutil.rmtree(p)
            self.pm.set_current("")
            self.after(0, self.set_status, "启动 Claude...")
            started = self.pm.start_claude()
            def finish():
                self.is_switching = False
                self.create_btn.configure(state="normal")
                self.new_login_btn.configure(state="normal")
                self.set_status("请登录新账号，然后点「保存当前登录为新 Profile」" if started
                                else "Claude 没启动起来，请从开始菜单手动打开并登录新账号")
                self._refresh_profiles()
            self.after(0, finish)

        threading.Thread(target=do_new_login, daemon=True).start()

    def on_create(self):
        """创建新 profile"""
        dialog = ctk.CTkInputDialog(text="输入 Profile 名称（如：个人号、工作号A）：",
                                     title="创建 Profile")
        name = dialog.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        if any(c in name for c in r'\/:*?"<>|'):
            self.set_status("名称不能包含特殊字符")
            return

        note_dialog = ctk.CTkInputDialog(text="备注（可选，如邮箱；不要填写密码或令牌）：",
                                          title="Profile 备注")
        note = note_dialog.get_input() or ""

        self.set_status("正在创建...")
        self.create_btn.configure(state="disabled")

        def do_create():
            success, msg = self.pm.create_profile(name)
            if success and note.strip():
                meta = self.pm.get_meta()
                meta[name] = {"note": note.strip()}
                self.pm.save_meta(meta)
            def finish():
                self.create_btn.configure(state="normal")
                self.set_status(msg)
                self._refresh_profiles()
                self._update_tray_menu()
            self.after(0, finish)

        threading.Thread(target=do_create, daemon=True).start()

    def on_edit(self, name: str):
        """编辑 profile：备注 + 预热配置 + 删除"""
        meta = self.pm.get_meta()
        current_note = meta.get(name, {}).get("note", "")
        current_warmup = self.pm.get_warmup(name)
        dialog = EditNoteDialog(self, name, current_note,
                                 current_warmup=current_warmup,
                                 on_test_warmup=self._test_warmup)
        result = dialog.get_result()
        if result is None:
            return
        if result == "__delete__":
            self.on_delete(name)
            return
        # result 是 dict: {"note": ..., "warmup": ...}
        new_note = result.get("note", "").strip()
        new_warmup = result.get("warmup", {})
        # 保留 last_run_date（用户改配置不影响今天是否已跑过的状态）
        new_warmup["last_run_date"] = current_warmup.get("last_run_date", "")

        meta.setdefault(name, {})["note"] = new_note
        self.pm.save_meta(meta)
        self.pm.save_warmup(name, new_warmup)
        self._refresh_profiles()
        self.set_status(f"已更新「{name}」")

    def _test_warmup(self, name: str, max_tokens: int, callback):
        """从编辑弹窗触发的"立即测试"按钮 — 后台线程发请求，回调到主线程"""
        def worker():
            try:
                is_active = (name == self.pm.get_current())
                if is_active:
                    ai = UsageFetcher.get_account_info()
                else:
                    cfg = INSTANCE_DIR / name / "config.json"
                    if not cfg.exists():
                        self.after(0, callback, False, "Profile 配置文件缺失")
                        return
                    ai = UsageFetcher.get_account_info(cfg)
                token = ai.get("token")
                if not token:
                    self.after(0, callback, False, "没有可用的 token")
                    return
                prompt = random.choice(WARMUP_PROMPTS)
                result = UsageFetcher.fetch_usage(token, max_tokens=max_tokens, prompt=prompt)
                if result.get("ok"):
                    pct = result.get("session_pct", 0)
                    self.after(0, callback, True,
                                f"测试成功，5h 已用 {pct}%（窗口已激活）")
                else:
                    self.after(0, callback, False,
                                f"失败：{result.get('error', '未知错误')}")
            except Exception as e:
                self.after(0, callback, False, f"异常：{type(e).__name__}")

        threading.Thread(target=worker, daemon=True).start()

    def on_delete(self, name: str):
        """删除 profile"""
        dialog = ConfirmDialog(self, "删除确认",
                               f"确定要删除 「{name}」 吗？\n此操作不可恢复。",
                               confirm_text="删除")
        if dialog.get_result():
            success, msg = self.pm.delete_profile(name)
            self.set_status(msg)
            self._refresh_profiles()
            self._update_tray_menu()

    def on_close(self):
        self.withdraw()

    def on_quit(self):
        if self.tray_icon:
            self.tray_icon.stop()
        if self._singleton:
            try:
                self._singleton.close()
            except Exception:
                pass
        self.destroy()
        sys.exit(0)

    # === 系统托盘 ===

    def _create_tray_image(self):
        return load_claude_icon(64)

    def _build_tray_menu(self):
        profiles = self.pm.get_profiles()
        current = self.pm.get_current()
        items = []
        for p in profiles:
            prefix = "✓ " if p == current else "   "
            items.append(pystray.MenuItem(
                f"{prefix}{p}",
                lambda _, name=p: self._tray_switch(name),
                enabled=(p != current)))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("打开主窗口", lambda _: self.after(0, self._show_window)))
        items.append(pystray.MenuItem("退出", lambda _: self.after(0, self.on_quit)))
        return pystray.Menu(*items)

    def _setup_tray(self):
        image = self._create_tray_image()
        menu = self._build_tray_menu()
        self.tray_icon = pystray.Icon("claude-switcher", image, "Claude Switcher", menu)
        self.tray_icon.on_activate = lambda icon: self.after(0, self._show_window)
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()

    def _update_tray_menu(self):
        if self.tray_icon:
            self.tray_icon.menu = self._build_tray_menu()
            self.tray_icon.update_menu()

    def _show_window(self):
        # 从任务栏最小化恢复（无边框窗口需要 Win32 SW_RESTORE 才能正确还原）
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_switch(self, name: str):
        """从托盘切换（托盘切换不弹确认，因为是主动右键选择的）"""
        self.after(0, lambda: self._do_tray_switch(name))

    def _do_tray_switch(self, name: str):
        if self.is_switching:
            return
        self.is_switching = True
        self._last_manual_switch_at = time.time()

        def do_switch():
            success, msg = self._switch_and_record(name, "托盘")

            def finish():
                self.is_switching = False
                self.set_status(msg)
                self._refresh_profiles()
                self._update_tray_menu()
            self.after(0, finish)

        threading.Thread(target=do_switch, daemon=True).start()


# === 入口 ===

def _bind_singleton() -> Optional[socket.socket]:
    """绑定单实例锁端口；若已被占用返回 None"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLETON_PORT))
        s.listen(8)
        return s
    except OSError:
        s.close()
        return None


def _wake_existing_instance() -> bool:
    """通知已运行的实例把窗口显示到前台；成功返回 True"""
    try:
        with socket.create_connection(("127.0.0.1", SINGLETON_PORT), timeout=1) as s:
            s.sendall(b"show")
        return True
    except OSError:
        return False


def main():
    singleton = _bind_singleton()
    if singleton is None:
        # 端口被占用：大概率已有实例在运行 —— 唤醒它并退出，避免重复打开
        if _wake_existing_instance():
            return
        # 没唤醒成功（端口被无关程序占用），则放弃单实例保护，照常启动
    app = App(singleton)
    app.mainloop()


if __name__ == "__main__":
    main()
