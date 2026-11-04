"""自动换号的纯逻辑测试：挑号、用完才换、防抖。不碰真 Claude、不发请求。
跑法：python -m unittest tests.test_auto_switch
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auto_switch as A  # noqa: E402

NOW = 1_800_000_000
H = 3600


def u(s=None, s_reset=None, w=None, w_reset=None, **kw):
    d = {"fetched_at": NOW - 60}
    if s is not None:
        d["session_pct"] = s
    d["session_reset_epoch"] = s_reset if s_reset is not None else NOW + 2 * H
    if w is not None:
        d["weekly_pct"] = w
    d["weekly_reset_epoch"] = w_reset if w_reset is not None else NOW + 72 * H
    d.update(kw)
    return d


class PickTarget(unittest.TestCase):
    def pick(self, cache, current="1", failures=None):
        return A.pick_target(sorted(set(cache) | {current}), current, cache, NOW, failures or {})

    def test_recovered_beats_partial(self):
        cache = {"1": u(100), "2": u(50, w=10), "3": u(5, w=40)}
        self.assertEqual(self.pick(cache)[0], "3")

    def test_reset_passed_counts_as_recovered(self):
        cache = {"1": u(100), "2": u(100, s_reset=NOW - 10, w=30, rate_limited=True), "3": u(40)}
        self.assertEqual(self.pick(cache)[0], "2")

    def test_recovered_prefers_lower_weekly(self):
        cache = {"1": u(100), "2": u(0, w=60), "3": u(10, w=20)}
        self.assertEqual(self.pick(cache)[0], "3")

    def test_not_full_prefers_earliest_reset(self):
        cache = {"1": u(100), "2": u(60, s_reset=NOW + 3 * H), "3": u(90, s_reset=NOW + 1 * H)}
        self.assertEqual(self.pick(cache)[0], "3")

    def test_skips_full_and_weekly_full(self):
        cache = {"1": u(100), "2": u(0, w=100), "3": u(100, rate_limited=True), "4": u(95)}
        self.assertEqual(self.pick(cache)[0], "4")

    def test_weekly_reset_passed_not_skipped(self):
        cache = {"1": u(100), "2": u(0, w=100, w_reset=NOW - 5)}
        self.assertEqual(self.pick(cache)[0], "2")

    def test_skips_invalid_login_and_recent_failure(self):
        cache = {"1": u(100), "2": u(0, token_invalid=True), "3": u(0), "4": u(60)}
        self.assertEqual(self.pick(cache, failures={"3": NOW - 600})[0], "4")

    def test_old_failure_forgotten(self):
        cache = {"1": u(100), "3": u(0)}
        self.assertEqual(self.pick(cache, failures={"3": NOW - 3 * H})[0], "3")

    def test_unknown_after_known(self):
        self.assertEqual(A.pick_target(["1", "2", "3"], "1", {"1": u(100), "3": u(70)}, NOW, {})[0], "3")
        self.assertEqual(A.pick_target(["1", "2", "3"], "1", {"1": u(100), "3": u(100)}, NOW, {})[0], "2")

    def test_none_usable_reports_earliest_recover(self):
        cache = {"1": u(100), "2": u(100, s_reset=NOW + 2 * H, rate_limited=True),
                 "3": u(100, s_reset=NOW + 1 * H)}
        name, _, nxt = self.pick(cache)
        self.assertIsNone(name)
        self.assertEqual(nxt, NOW + 1 * H)

    def test_never_picks_current(self):
        self.assertIsNone(self.pick({"1": u(0)})[0])


class Decide(unittest.TestCase):
    def d(self, usage, last=0):
        return A.decide(usage, NOW, last)

    def test_not_full_no_switch(self):
        self.assertEqual(self.d(u(99)), "ok")
        self.assertEqual(self.d(None), "ok")

    def test_full_switches(self):
        self.assertEqual(self.d(u(100)), "switch")

    def test_rate_limited_counts_as_full(self):
        self.assertEqual(self.d(u(None, rate_limited=True)), "switch")
        self.assertEqual(self.d(u(60, w=100, rate_limited=True)), "switch")

    def test_reset_passed_is_ok(self):
        self.assertEqual(self.d(u(100, s_reset=NOW - 1, rate_limited=True)), "ok")

    def test_cooldown(self):
        self.assertEqual(self.d(u(100), last=NOW - 120), "cooldown")
        self.assertEqual(self.d(u(100), last=NOW - A.COOLDOWN_SEC - 1), "switch")


class StateFile(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = A.State(Path(d))
            s.data["last_switch_at"] = 123
            s.data["failures"]["2"] = 5
            s.save()
            s.log("测试")
            s2 = A.State(Path(d))
            self.assertEqual(s2.data["last_switch_at"], 123)
            self.assertEqual(s2.data["failures"], {"2": 5})
            self.assertIn("测试", (Path(d) / "_auto_switch.log").read_text(encoding="utf-8"))


class History(unittest.TestCase):
    GOOD = {"ok": True, "msg": "已切换到「2」，已在「总控」发「继续」", "total_sec": 25,
            "auto_continue": True, "target_before": {"s": 0}, "target_after": {"s": 3}}

    def test_good_switch_no_flags(self):
        self.assertEqual(A.review_flags(self.GOOD), [])

    def test_flags(self):
        bad = dict(self.GOOD, msg="已切换到「2」，没等到输入框，没发「继续」", total_sec=90,
                   target_after={"s": 100, "rate_limited": True})
        f = A.review_flags(bad)
        self.assertIn("没自动发继续", f)
        self.assertIn("切换花了 90 秒", f)
        self.assertIn("换过去的号也是满的", f)
        self.assertIn("预计 0% 实际 100%", f)
        self.assertIn("切换失败", A.review_flags({"ok": False, "msg": "Claude 没能退出"}))
        self.assertIn("被登出", A.review_flags({"ok": True, "msg": "「2」被服务器登出了"}))
        self.assertEqual(A.review_flags(dict(self.GOOD, auto_continue=False, msg="已切换到「2」")), [])

    def test_brief(self):
        b = A.brief(u(100, s_reset=NOW - 1, w=40, rate_limited=True), NOW)
        self.assertEqual((b["s"], b["w"], b["age_min"]), (0, 40, 1))
        self.assertIsNone(A.brief(None, NOW))

    def test_append_keeps_last(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            for i in range(A.HISTORY_KEEP + 5):
                A.append_history(Path(d), {"i": i, "ok": True, "auto_continue": False})
            lines = (Path(d) / "_switch_history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), A.HISTORY_KEEP)
            self.assertEqual(json.loads(lines[-1])["i"], A.HISTORY_KEEP + 4)

    def test_switch_profile_records_steps(self):
        import claude_switcher as C
        pm = C.ProfileManager.__new__(C.ProfileManager)
        pm.get_current = lambda: "1"
        pm.get_settings = lambda: {"auto_continue": True}

        def inner(target, cb, _old):
            cb("正在关闭 Claude...")
            cb("启动 Claude...")
            return True, "已切换到「2」，已在「总控」发「继续」"
        pm._switch_profile = inner
        seen = []
        ok, _ = pm.switch_profile("2", progress_cb=seen.append)
        r = pm.last_switch_record
        self.assertTrue(ok)
        self.assertEqual(seen, ["正在关闭 Claude...", "启动 Claude..."])
        self.assertEqual([s[0] for s in r["steps"]], ["正在关闭 Claude", "启动 Claude"])
        self.assertEqual((r["from"], r["to"], r["ok"]), ("1", "2", True))


if __name__ == "__main__":
    unittest.main()
