"""额度用完自动换号：挑号、什么时候切、防抖。纯逻辑，不碰界面和真 Claude，方便单元测试。"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULTS = {"auto_switch": True}  # 开关
CHECK_SEC = 3 * 60            # 每 3 分钟查一次当前号
COOLDOWN_SEC = 10 * 60        # 两次切换至少隔 10 分钟（手动切换也算），防止来回切
FAIL_SKIP_SEC = 2 * 3600      # 切过去失败/被登出的号，2 小时内不再选
FULL_PCT = 100                # 5 小时用量到 100% = 用完
WEEKLY_SKIP_PCT = 95          # 周额度 ≥95% 就当用完，不切过去（切过去没多久又满）
RECOVERED_PCT = 20            # 5 小时用量 ≤20%（或已过恢复时间）= 已恢复


def eff_session(usage: Optional[dict], now: float) -> Optional[int]:
    """5 小时用量，过了恢复时间就当 0；不知道返回 None。"""
    if not usage:
        return None
    reset = usage.get("session_reset_epoch") or 0
    if reset and reset <= now:
        return 0
    pct = usage.get("session_pct")
    if pct is None and usage.get("rate_limited"):
        return 100
    return pct


def eff_weekly(usage: Optional[dict], now: float) -> Optional[int]:
    if not usage:
        return None
    reset = usage.get("weekly_reset_epoch") or 0
    if reset and reset <= now:
        return 0
    return usage.get("weekly_pct")


def is_full(usage: Optional[dict], now: float) -> bool:
    """当前号额度用完了吗（5 小时到 100%，或已被限流且还没到恢复时间）。"""
    if not usage:
        return False
    reset = usage.get("session_reset_epoch") or 0
    if reset and reset <= now:
        return False
    if usage.get("rate_limited"):
        return True
    pct = usage.get("session_pct")
    return pct is not None and pct >= FULL_PCT


def pick_target(profiles, current, cache: dict, now: float, failures: dict):
    """挑一个号。返回 (名字或 None, 原因, 最早恢复时间或 None)。

    先挑已恢复的（周用量低的优先），再挑没用完的（恢复时间早的优先），再挑没有数据的。
    跳过：当前号、登录失效、周额度用完、2 小时内切换失败过的、5 小时已用完的。
    """
    tiers = []
    next_recover = None

    def later(epoch):
        nonlocal next_recover
        if epoch and (next_recover is None or epoch < next_recover):
            next_recover = epoch

    for name in profiles:
        if name == current:
            continue
        u = cache.get(name) or {}
        if u.get("token_invalid"):
            continue
        if now - failures.get(name, 0) < FAIL_SKIP_SEC:
            continue
        w = eff_weekly(u, now)
        if w is not None and w >= WEEKLY_SKIP_PCT:
            later(u.get("weekly_reset_epoch"))
            continue
        s = eff_session(u, now)
        if s is None:
            tiers.append((2, 0, name, "没有用量数据"))
        elif s <= RECOVERED_PCT:
            tiers.append((0, w or 0, name, f"5小时 {s}%，周 {w if w is not None else '?'}%"))
        elif not is_full(u, now):
            tiers.append((1, u.get("session_reset_epoch") or 0, name, f"5小时 {s}%"))
        else:
            later(u.get("session_reset_epoch"))
    if not tiers:
        return None, "没有能用的号", next_recover
    tiers.sort()
    _, _, name, why = tiers[0]
    return name, why, next_recover


def decide(cur_usage: Optional[dict], now: float, last_switch_at: float) -> str:
    """返回 ok / cooldown / switch。"""
    if not is_full(cur_usage, now):
        return "ok"
    if now - (last_switch_at or 0) < COOLDOWN_SEC:
        return "cooldown"
    return "switch"


def fmt_hm(epoch: Optional[float]) -> str:
    if not epoch:
        return "?"
    dt = datetime.fromtimestamp(epoch)
    if dt.date() == datetime.now().date():
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


HISTORY_KEEP = 200  # 换号记录只留最近 200 条


def brief(usage: Optional[dict], now: float) -> Optional[dict]:
    """把一份用量缩成一小条，写进换号记录。"""
    if not usage:
        return None
    b = {"s": eff_session(usage, now), "w": eff_weekly(usage, now)}
    if usage.get("fetched_at"):
        b["age_min"] = round((now - usage["fetched_at"]) / 60)
    for k in ("rate_limited", "token_invalid", "error"):
        if usage.get(k):
            b[k] = usage[k]
    return b


def review_flags(rec: dict) -> list:
    """一条换号记录里值得改进的地方（给复盘用）。"""
    flags = []
    if not rec.get("ok"):
        flags.append("切换失败")
    msg = rec.get("msg") or ""
    if "登出" in msg:
        flags.append("被登出")
    if rec.get("ok") and rec.get("auto_continue", True) and "已在" not in msg and "新开了对话" not in msg:
        flags.append("没自动发继续")
    if (rec.get("total_sec") or 0) > 60:
        flags.append(f"切换花了 {rec['total_sec']} 秒")
    before, after = rec.get("target_before") or {}, rec.get("target_after") or {}
    if before.get("s") is not None and after.get("s") is not None and after["s"] - before["s"] >= 30:
        flags.append(f"预计 {before['s']}% 实际 {after['s']}%")
    if after.get("rate_limited") or (after.get("s") or 0) >= 100:
        flags.append("换过去的号也是满的")
    return flags


def append_history(folder: Path, rec: dict):
    """追加一条换号记录到 _switch_history.jsonl（带复盘标记），只留最近 HISTORY_KEEP 条。"""
    rec = dict(rec)
    rec["flags"] = review_flags(rec)
    f = folder / "_switch_history.jsonl"
    try:
        lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        lines.append(json.dumps(rec, ensure_ascii=False))
        f.write_text("\n".join(lines[-HISTORY_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return rec


class State:
    """存在 _auto_switch.json：上次切换时间/原因、失败记录。日志写 _auto_switch.log。"""

    def __init__(self, folder: Path):
        self.file = folder / "_auto_switch.json"
        self.log_file = folder / "_auto_switch.log"
        self.data = {"last_switch_at": 0, "last_info": "", "failures": {}}
        try:
            self.data.update(json.loads(self.file.read_text(encoding="utf-8")))
        except Exception:
            pass

    def save(self):
        try:
            self.file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def log(self, text: str):
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        except OSError:
            pass
