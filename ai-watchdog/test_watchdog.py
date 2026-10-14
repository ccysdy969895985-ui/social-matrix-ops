#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-watchdog 判定逻辑回归测试。

只用假数据跑 analyze()，不碰真实数据库、不碰任何任务。
重点是钉死曾经踩过的坑：PENDING_REVIEW 被误判成失败、补跑成功还被反复报错。

    python3 test_watchdog.py
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog as w  # noqa: E402

NOW = datetime(2026, 10, 8, 22, 0, 0, tzinfo=w.TZ)
CFG = dict(w.DEFAULT_CONFIG)

PASS, FAIL = [], []


def ms(dt):
    return int(dt.timestamp() * 1000) if dt else None


def task(tid, name, *, stype="recurring", status="ACTIVE", rrule="FREQ=DAILY;BYHOUR=12;BYMINUTE=0",
         next_run=None, last_run=None, scheduled_at=None):
    return {
        "id": tid, "name": name, "status": status, "schedule_type": stype,
        "next_run_at": ms(next_run), "last_run_at": ms(last_run),
        "rrule": rrule, "scheduled_at": scheduled_at,
        "valid_from": None, "valid_until": None,
    }


def run(success, at, status="PENDING_REVIEW", failure_code=None, reason_code=None):
    return {"status": status, "success": success, "failure_code": failure_code,
            "reason_code": reason_code, "created_at": ms(at), "updated_at": ms(at)}


def case(title, want_kinds, tasks, runs, rruns, successes, expects, rt=None):
    """expects: {task_id: [期望出现的 kinds]}；successes 的值必须是 datetime。"""
    summary, issues, rows, dormant, resolved, historical = w.analyze(
        NOW, tasks, runs, rruns, rt or {}, CFG, successes, successes)
    got = {i["automation_id"]: sorted(i["kinds"]) for i in issues}
    ok = True
    for tid, want in expects.items():
        have = got.get(tid, [])
        if sorted(want) != have:
            ok = False
            print("   ✗ %s：期望 %s，实际 %s" % (tid, sorted(want) or "无异常", have or "无异常"))
    if ok:
        PASS.append(title)
        print("✅ %s" % title)
    else:
        FAIL.append(title)
        print("❌ %s" % title)


print("=" * 64)
print("场景 1：PENDING_REVIEW + succ=0（跑完等人工确认）—— 这是历史误报的元凶")
t = task("a1", "小红书赚钱案例·12:30", next_run=NOW + timedelta(hours=20),
         last_run=NOW - timedelta(minutes=30))
case("跑完等确认不算失败", [], [t],
     {"a1": run(0, NOW - timedelta(minutes=30))}, {"a1": 1}, {}, {"a1": []})

print("\n场景 2：真停摆（到点 2 小时没跑）")
t = task("a2", "视频号自动发布", next_run=NOW - timedelta(hours=2), last_run=NOW - timedelta(hours=26))
case("到点没跑 → STALLED", [], [t], {}, {"a2": 1}, {}, {"a2": ["STALLED"]})

print("\n场景 3：调度器抖动（到点只晚了 20 分钟，宽限 60 分钟内）")
t = task("a3", "公众号案例", next_run=NOW - timedelta(minutes=20), last_run=NOW - timedelta(hours=24))
case("正常抖动不误报", [], [t], {}, {"a3": 1}, {}, {"a3": []})

print("\n场景 4：网络类失败且没有补跑")
t = task("a4", "小红书推送", next_run=NOW + timedelta(hours=20), last_run=NOW - timedelta(minutes=10))
case("网络失败 → FAILED", [], [t], {"a4": run(0, NOW - timedelta(minutes=10))},
     {"a4": 1}, {}, {"a4": ["FAILED"]},
     rt={"a4": {"running": 0, "running_started_at": None, "last_run_at": ms(NOW - timedelta(minutes=10)),
                "last_error": "Run timed out"}})

print("\n场景 5：网络类失败，但同家补跑已成功")
orig = task("a5", "小红书补发·20:30（AI音乐频道案例）",
            stype="once", scheduled_at="2026-10-07T20:30:00+08:00")
comp = task("a5b", "补跑｜小红书补发·20:30（AI音乐频道案例）｜10-08",
            stype="once", scheduled_at="2026-10-08T22:48:00+08:00")
case("补跑成功则平账，不再报错", [], [orig, comp],
     {"a5": run(0, datetime(2026, 10, 7, 20, 37, tzinfo=w.TZ)),
      "a5b": run(1, datetime(2026, 10, 8, 22, 48, tzinfo=w.TZ), status="ACCEPTED")},
     {}, {"a5": datetime(2026, 10, 7, 20, 37, tzinfo=w.TZ),
          "a5b": datetime(2026, 10, 8, 22, 48, tzinfo=w.TZ)},
     {"a5": [], "a5b": []},
     rt={"a5": {"running": 0, "running_started_at": None, "last_run_at": None,
                "last_error": "Network error: Stream timeout"}})

print("\n场景 6：单次任务逾期，且没有任何补跑")
t = task("a6", "小红书补发·20:30（旧案例）", stype="once", scheduled_at="2026-10-08T20:30:00+08:00")
case("单次任务没跑成 → OVERDUE_ONCE", [], [t],
     {"a6": run(0, datetime(2026, 10, 8, 20, 37, tzinfo=w.TZ))}, {"a6": 1}, {},
     {"a6": ["OVERDUE_ONCE"]})

print("\n场景 7：单次任务逾期一天以上（疑似废弃）")
t = task("a7", "远古单次任务", stype="once", scheduled_at="2026-10-04T20:30:00+08:00")
summary, issues, *_ = w.analyze(NOW, [t], {}, {}, {}, CFG, {}, {})
it = [i for i in issues if i["automation_id"] == "a7"]
if it and it[0]["severity"] == "medium" and it[0]["stale"]:
    PASS.append("逾期超 3 天降级 + 标记废弃")
    print("✅ 逾期超 3 天降级 + 标记废弃")
else:
    FAIL.append("逾期超 3 天降级 + 标记废弃")
    print("❌ 逾期超 3 天降级 + 标记废弃（实际 %s）" % (it[0] if it else "无"))

print("\n场景 8：卡死（运行超过 90 分钟没结束）")
t = task("a8", "某长任务", next_run=NOW + timedelta(hours=20), last_run=NOW - timedelta(hours=3))
case("跑太久 → STUCK", [], [t], {}, {"a8": 1},
     {}, {"a8": ["STUCK"]}) if False else None
summary, issues, *_ = w.analyze(
    NOW, [t], {}, {},
    {"a8": {"running": 1, "running_started_at": ms(NOW - timedelta(hours=3)),
            "last_error": None, "last_run_at": ms(NOW - timedelta(hours=3))}},
    CFG, {}, {})
got = [i["kind"] for i in issues if i["automation_id"] == "a8"]
if got == ["STUCK"]:
    PASS.append("跑太久 → STUCK")
    print("✅ 跑太久 → STUCK")
else:
    FAIL.append("跑太久 → STUCK")
    print("❌ 跑太久 → STUCK（实际 %s）" % (got or "无异常"))

print("\n场景 9：调度器没排期 + 停跑 4 天（废弃嫌疑）")
t = task("a9", "老任务", next_run=None, last_run=NOW - timedelta(days=4))
summary, issues, *_ = w.analyze(NOW, [t], {}, {}, {}, CFG, {}, {})
it = [i for i in issues if i["automation_id"] == "a9"]
if it and it[0]["kind"] == "STALLED" and it[0]["stale"]:
    PASS.append("未排期 + 长期停跑 → STALLED(废弃)")
    print("✅ 未排期 + 长期停跑 → STALLED(废弃)")
else:
    FAIL.append("未排期 + 长期停跑 → STALLED(废弃)")
    print("❌ 未排期 + 长期停跑 → STALLED(废弃)（实际 %s）" % (it[0]["kind"] if it else "无"))

print("\n场景 10：半年前的陈年失败，不该天天翻旧账")
t = task("a10", "很老的任务", next_run=NOW + timedelta(hours=20), last_run=NOW - timedelta(days=180))
summary, issues, rows, dormant, resolved, historical = w.analyze(
    NOW, [t], {"a10": run(0, NOW - timedelta(days=180))}, {}, 
    {"a10": {"running": 0, "running_started_at": None,
             "last_run_at": ms(NOW - timedelta(days=180)),
             "last_error": "Run timed out"}},
    CFG, {}, {})
has_failed_issue = any(i["kind"] == "FAILED" for i in issues)
has_failed_hist = any(h["kind"] == "FAILED" for h in historical)
if not has_failed_issue and has_failed_hist:
    PASS.append("陈旧失败降级为历史遗留")
    print("✅ 陈旧失败降级为历史遗留（不打扰）")
else:
    FAIL.append("陈旧失败降级为历史遗留")
    print("❌ 陈旧失败降级为历史遗留（issue=%s historical=%s）"
          % ([i["kind"] for i in issues], [h["kind"] for h in historical]))

print("\n场景 11：暂停的任务不参与判定")
t = task("a11", "已暂停", status="PAUSED", next_run=NOW - timedelta(days=5), last_run=None)
summary, issues, *_ = w.analyze(NOW, [t], {}, {}, {}, CFG, {}, {})
if not issues:
    PASS.append("暂停任务不报警")
    print("✅ 暂停任务不报警")
else:
    FAIL.append("暂停任务不报警")
    print("❌ 暂停任务不报警（实际 %s）" % [i["kind"] for i in issues])

print("\n场景 12：名称归一化（各种补跑命名都要认得出）")
checks = {
    "补跑｜小红书赚钱案例·12:30准时发（10:45起跑）｜10-08": "小红书赚钱案例·12:30准时发（10:45起跑）",
    "补跑|每日新闻|10-8": "每日新闻",
    "补跑｜任务A｜2026-10-08": "任务A",
    "普通任务名": "普通任务名",
}
bad = {k: w.name_group(k) for k, v in checks.items() if w.name_group(k) != v}
if not bad:
    PASS.append("补跑名称归一化")
    print("✅ 补跑名称归一化")
else:
    FAIL.append("补跑名称归一化")
    print("❌ 补跑名称归一化：%s" % bad)

print("\n" + "=" * 64)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：%s" % FAIL)
    sys.exit(1)
print("全部通过 🎉")
