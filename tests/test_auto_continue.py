"""自动“继续”的测试：用假界面跑真的 auto_continue.ps1，不碰真 Claude 窗口，不按真回车。

核心要求：深链接没生效（还停在别的对话）时，绝不打字、绝不回车。
跑法：python -m unittest tests.test_auto_continue
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "auto_continue.ps1"
sys.path.insert(0, str(ROOT))

SID = "local_11111111-2222-3333-4444-555555555555"
ZK = {"prompts": 1, "title": "总控 - Claude Code", "text": ""}
OTHER = {"prompts": 1, "title": "项目A - Claude Code", "text": ""}


def run_mock(mock: dict, *extra) -> dict:
    with tempfile.TemporaryDirectory() as d:
        mf, lf = Path(d) / "mock.json", Path(d) / "log.json"
        mf.write_text(json.dumps(mock, ensure_ascii=False), encoding="utf-8")
        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                "-Text", "继续", "-MockFile", str(mf), "-MockLog", str(lf), *extra]
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        log = json.loads(lf.read_text(encoding="utf-8-sig"))
        acts = log["actions"]
        log["actions"] = [acts] if isinstance(acts, str) else (acts or [])
        log["exit"] = r.returncode
        return log


def typed_or_entered(log) -> bool:
    return any(a.startswith("type:") or a == "enter" or a == "clear" for a in log["actions"])


EXISTING = ("-SessionId", SID, "-ExpectTitle", "总控")
NEW = ("-Folder", r"C:\work\demo")


class ExistingControlSession(unittest.TestCase):
    def test_deeplink_ignored_stays_on_other_chat_never_types(self):
        log = run_mock({"before": OTHER, "after": OTHER}, *EXISTING)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_deeplink_throws_never_types(self):
        log = run_mock({"before": OTHER, "after": ZK, "deeplinkThrows": True}, *EXISTING)
        self.assertEqual(log["exit"], 4)
        self.assertFalse(typed_or_entered(log), log)

    def test_split_view_two_prompts_never_types(self):
        log = run_mock({"before": OTHER, "after": dict(ZK, prompts=2)}, *EXISTING)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_no_window_title_never_types(self):
        log = run_mock({"before": OTHER, "after": dict(ZK, title="")}, *EXISTING)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_similar_title_not_accepted(self):
        log = run_mock({"before": OTHER, "after": dict(ZK, title="总控2 - Claude Code")}, *EXISTING)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_user_switches_away_after_focus_never_types(self):
        log = run_mock({"before": OTHER, "after": ZK, "afterFocus": OTHER}, *EXISTING)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_draft_in_control_left_alone(self):
        log = run_mock({"before": OTHER, "after": dict(ZK, text="我的草稿")}, *EXISTING)
        self.assertEqual(log["exit"], 3)
        self.assertFalse(typed_or_entered(log), log)

    def test_happy_path_types_then_enters(self):
        log = run_mock({"before": OTHER, "after": ZK}, *EXISTING)
        self.assertEqual(log["exit"], 0, log)
        self.assertEqual(log["actions"][-2:], ["type:继续", "enter"])

    def test_nosend_types_but_never_enters(self):
        log = run_mock({"before": OTHER, "after": ZK}, *EXISTING, "-NoSend")
        self.assertEqual(log["exit"], 0, log)
        self.assertIn("type:继续", log["actions"])
        self.assertNotIn("enter", log["actions"])

    def test_ime_garbles_text_no_enter(self):
        log = run_mock({"before": OTHER, "after": ZK, "typeResult": "jixu"}, *EXISTING)
        self.assertEqual(log["exit"], 3)
        self.assertNotIn("enter", log["actions"])


class NewControlSession(unittest.TestCase):
    def test_new_deeplink_ignored_never_types(self):
        log = run_mock({"before": OTHER, "after": OTHER}, *NEW)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_new_prefill_wrong_no_enter(self):
        log = run_mock({"before": OTHER, "after": dict(OTHER, text="继续吧")}, *NEW)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_old_chat_already_had_draft_继续_refuses(self):
        # 跳转前当前对话里就有“继续”：分不清新旧，不按回车
        log = run_mock({"before": dict(OTHER, text="继续"), "after": dict(OTHER, text="继续")}, *NEW)
        self.assertEqual(log["exit"], 5)
        self.assertFalse(typed_or_entered(log), log)

    def test_new_prefilled_enters_without_typing(self):
        log = run_mock({"before": OTHER, "after": {"prompts": 1, "title": "Claude Code", "text": "继续"}}, *NEW)
        self.assertEqual(log["exit"], 0, log)
        self.assertEqual(log["actions"][-1], "enter")
        self.assertFalse(any(a.startswith("type:") for a in log["actions"]))

    def test_new_nosend_never_enters(self):
        log = run_mock({"before": OTHER, "after": {"prompts": 1, "title": "Claude Code", "text": "继续"}},
                       *NEW, "-NoSend")
        self.assertEqual(log["exit"], 0, log)
        self.assertNotIn("enter", log["actions"])


class PythonSide(unittest.TestCase):
    S = {"text": "继续", "session_title": "总控", "folder": r"C:\work\demo"}

    def test_args_carry_expect_title(self):
        from claude_switcher import ProfileManager
        a = ProfileManager.auto_continue_args(SCRIPT, self.S, SID)
        self.assertIn("-ExpectTitle", a)
        self.assertEqual(a[a.index("-ExpectTitle") + 1], "总控")

    def test_code5_message_tells_user_to_go_manually(self):
        from claude_switcher import ProfileManager
        calls = []
        go = ProfileManager.interpret_auto_continue(self.S, SID, lambda a: calls.append(a) or (5, ""))
        ok, msg = go([])
        self.assertFalse(ok)
        self.assertIn("没发", msg)
        self.assertIn("「总控」", msg)
        self.assertEqual(len(calls), 2)   # 已有总控：重试一次

    def test_new_session_never_retried(self):
        from claude_switcher import ProfileManager
        calls = []
        go = ProfileManager.interpret_auto_continue(self.S, None, lambda a: calls.append(a) or (5, ""))
        ok, _ = go([])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
