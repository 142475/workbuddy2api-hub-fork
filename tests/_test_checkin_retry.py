"""签到补救重试：失败就地补试，签成/不可签就提前停。

2026-11-16 现场：09:00 那轮 4 个国内账号 2 个回「签到失败，请稍后重试」，
当天晚些时候同样的请求就成功——旧实现把这次失败直接放弃，100 积分随次日过期。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_scheduler


class _Acc:
    uid = "abcdef1234567890"

    def __init__(self, results, can_checkin=True):
        self._results = list(results)
        self._can = can_checkin
        self.calls = 0

    def can_checkin(self):
        return self._can

    def checkin(self, trigger=None):
        assert trigger == "scheduler", trigger
        self.calls += 1
        index = min(self.calls - 1, len(self._results) - 1)
        return self._results[index]


class CheckinRetryTests(unittest.TestCase):

    def _run(self, acc, **kwargs):
        sleeps = []
        kwargs.setdefault("rounds", 2)
        kwargs.setdefault("gap", 0)
        res = wb_scheduler.checkin_with_retry(
            acc, sleep=lambda gap: sleeps.append(gap), **kwargs)
        return res, sleeps

    def test_retries_until_success(self):
        acc = _Acc([{"ok": False}, {"ok": False}, {"ok": True}])
        res, sleeps = self._run(acc)
        self.assertTrue(res["ok"])
        self.assertEqual(acc.calls, 3)          # 首发 + 两次补试
        self.assertEqual(len(sleeps), 2)

    def test_no_retry_when_first_success(self):
        acc = _Acc([{"ok": True}])
        res, sleeps = self._run(acc)
        self.assertTrue(res["ok"])
        self.assertEqual(acc.calls, 1)
        self.assertEqual(sleeps, [])

    def test_stops_when_not_checkinable(self):
        # 上一轮补试期间被别的路径签上了：can_checkin 转 False，别再打上游
        acc = _Acc([{"ok": False}], can_checkin=False)
        res, sleeps = self._run(acc)
        self.assertFalse(res["ok"])
        self.assertEqual(acc.calls, 1)
        self.assertEqual(sleeps, [])

    def test_gives_up_after_rounds(self):
        acc = _Acc([{"ok": False}])
        res, sleeps = self._run(acc, rounds=2)
        self.assertFalse(res["ok"])
        self.assertEqual(acc.calls, 3)
        self.assertEqual(len(sleeps), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
