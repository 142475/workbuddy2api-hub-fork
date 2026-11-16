#!/bin/bash
# 每日任务失败汇总告警（22:00，两个实例各查各自真实在跑的任务）
#
#   8788 国内版 → 「Buddy 加油站签到」= 账号字段 lastCheckin
#   8789 国际版 → 「今日活跃」      = 账号字段 lastDailyChat
#
# 口径：只查 enabled=true 的账号；对应字段不是今天就算「今天没成功」。
# 有失败才发一封汇总邮件；全部成功则静默退出（不发）。
#
# 用法（凭据走环境变量，不要把口令写进仓库）：
#   . /etc/wb-checkin-alert.env                  # 里面有 export WB_ALERT_PUSH_URL / WB_ALERT_PUSH_PASSWORD
#                                                # 必须是 export：本脚本是独立进程，source 的普通赋值传不进来
#   ./scripts/checkin_alert.sh                   # 真发
#   DRY_RUN=1 ./scripts/checkin_alert.sh         # 只打印标题和正文，不发送
#   FORCE_DAY=2026-11-17 DRY_RUN=1 ./...         # 假装今天是另一天，看「失败」分支长什么样
#
# crontab（root）：
#   0 22 * * * . /etc/wb-checkin-alert.env && /root/sofaware/wb-checkin-alert/checkin_alert.sh >> /var/log/wb-checkin-alert.log 2>&1

set -u

PUSH_URL="${WB_ALERT_PUSH_URL:-}"
PUSH_PASSWORD="${WB_ALERT_PUSH_PASSWORD:-}"
if [ -z "$PUSH_URL" ] || [ -z "$PUSH_PASSWORD" ]; then
    echo "缺少 WB_ALERT_PUSH_URL / WB_ALERT_PUSH_PASSWORD，请先 source 凭据文件" >&2
    exit 2
fi

# cron 的 locale 是 POSIX/C，python 的 stdout 会退化成 ASCII，打印中文直接 UnicodeEncodeError
PYTHONIOENCODING=utf-8 python3 - "$PUSH_URL" "$PUSH_PASSWORD" <<'PY'
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

PUSH_URL, PUSH_PASSWORD = sys.argv[1], sys.argv[2]

# (显示名, 数据根目录, 流水里的任务名, 账号里的时间字段, 任务中文名)
INSTANCES = (
    ("8788 国内版", "/docker/wb-proxy", "checkin", "lastCheckin", "签到"),
    ("8789 国际版", "/docker/wb-proxy2", "daily_chat", "lastDailyChat", "今日活跃"),
)
SKIP_FILES = {"model.json", "settings.json", "model_windows.json", "active_realm.json"}
TAIL_BYTES = 400 * 1024
TAIL_LINES = 2000


def today_stamp():
    """固定按东八区算「今天」：账号里的时间戳就是东八区本地时间。"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def tail_lines(path, limit=TAIL_LINES):
    """只读文件尾部——历史文件会一直增长，不整份读。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            chunk = fh.read()
    except OSError:
        return []
    return chunk.decode("utf-8", "replace").splitlines()[-limit:]


def last_task_error(usage_dir, uid, day, task):
    """今天这个账号最后一次该任务的错误文案；今天已成功过或没尝试过都返回空串。"""
    path = os.path.join(usage_dir, "activity_history.jsonl")
    for line in reversed(tail_lines(path)):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("ts", ""))[:10] != day:
            continue
        if row.get("task") != task or str(row.get("uid", "")) != uid:
            continue
        return "" if row.get("ok") else str(row.get("message") or "")[:160]
    return ""


def collect(day):
    """返回今天没完成任务的启用账号列表。"""
    failed = []
    for label, base, task, field, task_label in INSTANCES:
        for path in sorted(glob.glob(os.path.join(base, "accounts", "*.json"))):
            if os.path.basename(path) in SKIP_FILES:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    account = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(account, dict) or "uid" not in account:
                continue
            if not account.get("enabled"):
                continue
            last = str(account.get(field) or "")
            if last[:10] == day:
                continue
            uid = str(account.get("uid"))
            failed.append({
                "instance": label,
                "task_label": task_label,
                "nickname": str(account.get("nickname") or "").strip()[:20] or "(无名)",
                "uid": uid,
                "last": last or "从未成功",
                "error": last_task_error(os.path.join(base, "usage"), uid, day, task),
            })
    return failed


def build_message(day, failed):
    grouped = {}
    for item in failed:
        grouped.setdefault(item["instance"], []).append(item)
    parts = ["今天（%s）22:00，还有 %d 个启用中的账号没完成每日任务。" % (day, len(failed))]
    for label, _base, _task, _field, _task_label in INSTANCES:
        items = grouped.get(label)
        if not items:
            continue
        parts.append("")
        parts.append("【%s】" % label)
        for item in items:
            parts.append("· %s（%s）%s" % (item["nickname"], item["uid"][:8], item["task_label"]))
            parts.append("  上次成功：%s" % item["last"])
            parts.append("  今天最后错误：%s" % (item["error"] or "今天没有尝试记录"))
    parts.append("")
    parts.append("国内版签到跑 09:00 / 21:00；国际版今日活跃整点补跑到 23:00。")
    return "\n".join(parts)


def push(title, message):
    params = urllib.parse.urlencode({
        "password": PUSH_PASSWORD,
        "App": "监控告警",
        "title": title,
        "message": message,
    })
    with urllib.request.urlopen("%s?%s" % (PUSH_URL, params), timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")[:300]


def main():
    # FORCE_DAY 只给演练用：填一个不是今天的日期就能看到「失败」分支的邮件长什么样
    day = os.environ.get("FORCE_DAY") or today_stamp()
    failed = collect(day)
    if not failed:
        print("[%s] 每日任务告警：所有启用中的账号今天都成功，不发邮件"
              % time.strftime("%Y-%m-%d %H:%M:%S"))
        return 0
    message = build_message(day, failed)
    title = "每日任务失败 %d 个账号（%s 22:00）" % (len(failed), day)
    print("[%s] 每日任务告警：%d 个账号失败" % (time.strftime("%Y-%m-%d %H:%M:%S"), len(failed)))
    if os.environ.get("DRY_RUN") == "1":
        print("--- 演练模式，不发送 ---")
        print("标题：" + title)
        print(message)
        return 0
    try:
        status, body = push(title, message)
        print("  发送结果：HTTP %s %s" % (status, body))
    except Exception as exc:  # 告警发不出去也要留日志，别静默
        print("  发送失败：%s: %s" % (type(exc).__name__, exc))
        print(message)
        return 1
    return 0


sys.exit(main())
PY
