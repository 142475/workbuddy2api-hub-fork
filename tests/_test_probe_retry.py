"""面板「测试」撞上链路抖动时要重试一次，两次都抖就说清是链路问题。

线上现象：出口配了代理（德国/美国/香港）的号点「测试」偶尔报
`URLError: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in
violation of protocol (_ssl.c:1018)>`，同一秒手工连一次就正常 —— 上游/CDN 边缘
建连时掐掉 TLS，与账号无关。真实流量那侧（`open_upstream`）本来就换号重试，
只有诊断入口 `probe_account` 是单发，于是把一次抖动原样报成了账号失败。

这个套件钉住三件事：
  1. 链路层失败（TLS 被掐 / 连接重置 / 读超时）算抖动，业务答复与 DNS 失败不算；
  2. 抖动给一次原地重试，第二次成功就是成功；
  3. 两次都抖时标 network，并且说清是出口链路。

    python tests/_test_probe_retry.py
"""

import io
import os
import socket
import ssl
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_dirs import isolated_data_dirs    # noqa: E402

_TMP = isolated_data_dirs("wb-probe-retry-")

import wb_accounts as A        # noqa: E402
import wb_proxy as P           # noqa: E402


def ssl_eof():
    return urllib.error.URLError(
        ssl.SSLEOFError(
            8,
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of "
            "protocol (_ssl.c:1018)",
        ))


class FakeResponse(object):
    """probe_account 只把它当「可迭代的 SSE 行来源 + 上下文管理器」。"""

    def __init__(self, lines):
        self._lines = lines
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._lines)


def ok_response(text="OK"):
    chunk = 'data: {"choices":[{"delta":{"content":"%s"},"index":0}]}\n' % text
    return FakeResponse([chunk.encode("utf-8"), b"data: [DONE]\n"])


def make_account(**overrides):
    data = {
        "uid": "37fad6d4-ef05-4344-a022-5c31e3075e57",
        "accessToken": "x.y.z",
        "nickname": "反倒是那么",
        "enabled": True,
        "realm": "intl",
        "proxy": "http://jf:pw@129.146.81.33:3128",
    }
    data.update(overrides)
    return A.Account(data)


class TransportGlitchTests(unittest.TestCase):
    def test_a_killed_tls_handshake_is_a_glitch(self):
        self.assertTrue(P._transport_glitch(ssl_eof()))

    def test_reset_and_read_timeout_are_glitches(self):
        self.assertTrue(P._transport_glitch(
            urllib.error.URLError(ConnectionResetError("reset by peer"))))
        self.assertTrue(P._transport_glitch(
            urllib.error.URLError(socket.timeout("timed out"))))
        self.assertTrue(P._transport_glitch(
            urllib.error.URLError(ssl.SSLError(1, "[SSL] record layer failure"))))

    def test_a_business_answer_is_not_a_glitch(self):
        error = urllib.error.HTTPError(
            "https://x", 400, "Bad Request", {}, io.BytesIO(b"{}"))
        self.assertFalse(P._transport_glitch(error))

    def test_dns_failure_keeps_its_own_branch(self):
        # 解析失败是本机网络/代理地址错，重试没有意义，文案也该是另一句。
        self.assertFalse(P._transport_glitch(
            urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))))

    def test_unrelated_errors_are_not_glitches(self):
        self.assertFalse(P._transport_glitch(ValueError("boom")))
        self.assertFalse(P._transport_glitch(urllib.error.URLError(
            OSError("Proxy Authentication Required"))))


class ProbeRetryTests(unittest.TestCase):
    def test_a_single_glitch_is_retried_and_the_test_passes(self):
        calls = []

        def fake_urlopen(req, timeout=30, proxy=""):
            calls.append(proxy)
            if len(calls) == 1:
                raise ssl_eof()
            return ok_response()

        with mock.patch.object(A, "urlopen", fake_urlopen):
            row = P.probe_account(make_account(), model="deepseek-v4.1-flash")
        self.assertTrue(row["ok"], row.get("error"))
        self.assertEqual(len(calls), 2, "链路抖动应当原地再试一次")

    def test_two_glitches_report_the_egress_not_the_account(self):
        def fake_urlopen(req, timeout=30, proxy=""):
            raise ssl_eof()

        with mock.patch.object(A, "urlopen", fake_urlopen):
            row = P.probe_account(make_account(), model="deepseek-v4.1-flash")
        self.assertFalse(row["ok"])
        self.assertTrue(row["network"], "链路问题不能算成账号问题")
        self.assertIn("出口链路", row["error"])

    def test_a_business_error_is_not_retried(self):
        calls = []

        def fake_urlopen(req, timeout=30, proxy=""):
            calls.append(1)
            raise urllib.error.HTTPError(
                "https://x", 400, "Bad Request", {},
                io.BytesIO(b'{"error":"bad request"}'))

        with mock.patch.object(A, "urlopen", fake_urlopen):
            row = P.probe_account(make_account(), model="deepseek-v4.1-flash")
        self.assertFalse(row["ok"])
        self.assertEqual(len(calls), 1, "上游已经明确表态，重试没有意义")
        self.assertFalse(row["network"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
