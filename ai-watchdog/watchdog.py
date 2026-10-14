#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-watchdog —— 用 AI 监督 AI
==============================
监督 WorkBuddy 里那些"设了就该自动跑"的任务，发现它们停摆 / 失败 / 卡死，
并把证据整理成 AI 能直接消费的诊断包。

设计原则
--------
* **只读**：以 mode=ro 打开数据库，绝不写入、绝不修改任何任务。
* **零依赖**：只用 Python 内置库，不装任何东西。
* **可被 AI 消费**：--json 输出结构化诊断包，交给上层 AI 判断与补跑。

用法
----
    python3 watchdog.py scan              # 体检报告（人看）
    python3 watchdog.py scan --json       # 诊断包（AI 看）
    python3 watchdog.py scan --alert      # 有异常时响铃 + 语音播报
    python3 watchdog.py scan --wake       # 发现有停摆时唤起 WorkBuddy（触发补跑）
    python3 watchdog.py brief             # 一行概览

退出码：0=健康  1=有异常  2=运行出错（方便被 cron / 自动化判断）
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 基础配置

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".workbuddy", "ai-watchdog")
DB_PATH = os.path.join(HOME, ".workbuddy", "workbuddy.db")
STATE_DIR = os.path.join(BASE, "state")
REPORT_DIR = os.path.join(BASE, "reports")
CONFIG_PATH = os.path.join(BASE, "config.json")
APP_NAME = "WorkBuddy"
TZ = timezone(timedelta(hours=8))

DEFAULT_CONFIG = {
    # 到点后多久没被触发，就判定为「停摆」。调度器实际触发常晚 7~50 分钟，
    # 所以宽限必须给足，否则每次正常抖动都会被误报成「掉链子」。
    # 巡检点设在任务时点后 50 分钟，正好略超这个宽限，一次就能确认结果。
    "grace_minutes": 45,
    # 单次运行超过多久还没结束，就判定为「卡死」
    "stuck_minutes": 90,
    # 静默判定：距上次运行超过「周期 × 该倍数」算异常
    "silent_factor": 1.8,
    # 失败只在这么多小时内算「活异常」上报；更早的降级为历史遗留，不打扰用户
    "failed_fresh_hours": 6,
    # 停摆超过这么多天，提示可能是废弃任务
    "stale_days": 3,
    # 监工自身距上次巡检超过这么多小时，提示「监工也停摆了」
    "supervisor_gap_hours": 3,
    # 同一批异常在这么多小时内只响铃一次（避免反复骚扰）
    "alert_cooldown_hours": 6,
    # 报告保留天数
    "report_retention_days": 14,
}

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
KIND_LABEL = {
    "STALLED": "停摆（到点没触发）",
    "STUCK": "卡死（跑太久没结束）",
    "FAILED": "执行失败",
    "OVERDUE_ONCE": "错过了单次任务",
    "SILENT": "长期静默",
}

# 明确的失败 / 成功 / 中性运行状态。
# 踩过的坑：本机 automation_runs.status 绝大多数是 PENDING_REVIEW
# （跑完等人工确认，既可能 succ=0 也可能 succ=1），早期版本把它一律当成
# 「失败」，导致明明发布成功的任务被反复误报。现在只有明确失败态、
# 或 runtime_state.last_error 有货，才算真失败。
STATUS_FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED",
                 "TIMEOUT", "EXPIRED", "ABORTED", "REJECTED"}
STATUS_OK = {"ACCEPTED", "COMPLETED", "DONE", "SUCCESS", "FINISHED"}
STATUS_NEUTRAL = {"PENDING_REVIEW", "PENDING", "ARCHIVED", "RUNNING", "IN_PROGRESS"}

# 「补跑｜原名｜10-08」这类补偿任务的名称归一化规则（日期支持 10-08 / 2026-10-08）
SUFFIX_RE = re.compile(r"[｜|]\s*(?:\d{4}[-/.])?\d{1,2}[-/.]\d{1,2}\s*$")
PREFIX_RE = re.compile(r"^补跑\s*[｜|]\s*")


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        if isinstance(user_cfg, dict):
            cfg.update({k: v for k, v in user_cfg.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------- 工具函数

def connect():
    """只读打开数据库，避免任何写锁风险。"""
    if not os.path.exists(DB_PATH):
        raise RuntimeError("找不到数据库：%s" % DB_PATH)
    return sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=5)


def ms2dt(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, TZ)
    except Exception:
        return None


def parse_iso(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def fmt(dt):
    return dt.strftime("%m-%d %H:%M") if dt else "—"


def human_delta(seconds):
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%d 秒" % seconds
    if seconds < 3600:
        return "%d 分钟" % (seconds // 60)
    if seconds < 86400:
        return "%.1f 小时" % (seconds / 3600.0)
    return "%.1f 天" % (seconds / 86400.0)


def period_hours(rrule):
    """从调度规则粗略估算「一轮的间隔」，用于静默判定。估不出返回 None。"""
    if not rrule:
        return None
    m = re.search(r"FREQ=(\w+)", rrule)
    if not m:
        return None
    freq = m.group(1).upper()
    if freq == "DAILY":
        hours = re.search(r"BYHOUR=([\d,\s]+)", rrule)
        n = len([x for x in hours.group(1).split(",") if x.strip()]) if hours else 1
        return 24.0 / max(n, 1)
    if freq == "WEEKLY":
        days = re.search(r"BYDAY=([A-Z,]+)", rrule)
        n = len(days.group(1).split(",")) if days else 1
        return 24.0 * 7 / max(n, 1)
    if freq == "HOURLY":
        iv = re.search(r"INTERVAL=(\d+)", rrule)
        return float(iv.group(1)) if iv else 1.0
    if freq == "MONTHLY":
        return 24.0 * 30
    return None


def name_group(name):
    """把「补跑｜XX｜10-08」归一化回「XX」，便于把补跑任务和原任务认成一家。

    这样原任务的历史失败，只要组内后来有成功的补跑，就不会再被反复报出来。
    """
    n = (name or "").strip()
    n = PREFIX_RE.sub("", n)
    n = SUFFIX_RE.sub("", n)
    return n.strip()


def is_network_error(err):
    """网络类文案识别，用于「网络抖动可直接补跑」的判断。"""
    if not err:
        return False
    e = err.lower()
    return any(k in e for k in (
        "timeout", "timed out", "enotfound", "502", "503", "504",
        "request cancelled", "connection", "network", "网络", "econnreset",
    ))


# ---------------------------------------------------------------- 数据采集

def collect(cur):
    now = datetime.now(TZ)

    tasks = []
    cur.execute("""
        SELECT id, name, status, schedule_type, next_run_at, last_run_at,
               rrule, scheduled_at, valid_from, valid_until
        FROM automations
        WHERE deleted_at IS NULL
    """)
    for row in cur.fetchall():
        tasks.append({
            "id": row[0], "name": row[1], "status": row[2],
            "schedule_type": row[3], "next_run_at": row[4], "last_run_at": row[5],
            "rrule": row[6] or "", "scheduled_at": row[7],
            "valid_from": row[8], "valid_until": row[9],
        })
    tasks = [t for t in tasks if t["status"].upper() in ("ACTIVE", "PAUSED")]

    # 每个任务最近一次运行（同一毫秒多条时取 rowid 最大的那条，避免随机取到旧记录）
    latest_runs = {}
    cur.execute("""
        SELECT r.automation_id, r.status, r.result_success, r.failure_code,
               r.reason_code, r.created_at, r.updated_at
        FROM automation_runs r
        JOIN (SELECT automation_id, MAX(created_at) mx
              FROM automation_runs GROUP BY automation_id) t
          ON t.automation_id = r.automation_id AND t.mx = r.created_at
        ORDER BY r.automation_id, r.rowid DESC
    """)
    for row in cur.fetchall():
        latest_runs.setdefault(row[0], {
            "status": row[1], "success": row[2], "failure_code": row[3],
            "reason_code": row[4], "created_at": row[5], "updated_at": row[6],
        })

    # 每个任务最近一次「成功」运行的时间（用于失败豁免 / 补跑覆盖判断）
    last_success = {}
    cur.execute("""
        SELECT automation_id, MAX(created_at) FROM automation_runs
        WHERE result_success = 1 GROUP BY automation_id
    """)
    for row in cur.fetchall():
        last_success[row[0]] = ms2dt(row[1])

    # 每个任务最近一次有运行记录的时间（含未标记成功的）
    last_attempt = {}
    cur.execute("""
        SELECT automation_id, MAX(created_at) FROM automation_runs GROUP BY automation_id
    """)
    for row in cur.fetchall():
        last_attempt[row[0]] = ms2dt(row[1])

    # 24 小时运行次数
    runs_24h = {}
    since = int((now - timedelta(hours=24)).timestamp() * 1000)
    cur.execute("""
        SELECT automation_id, COUNT(*) FROM automation_runs
        WHERE created_at >= ? GROUP BY automation_id
    """, (since,))
    for row in cur.fetchall():
        runs_24h[row[0]] = row[1]

    # 运行时状态（含卡死信息与最近错误）
    runtime = {}
    cur.execute("""
        SELECT automation_id, last_run_at, last_error, running, running_started_at
        FROM automation_runtime_state
    """)
    for row in cur.fetchall():
        runtime[row[0]] = {
            "last_run_at": row[1], "last_error": row[2],
            "running": row[3], "running_started_at": row[4],
        }

    return now, tasks, latest_runs, runs_24h, runtime, last_success, last_attempt


# ---------------------------------------------------------------- 异常判定

def analyze(now, tasks, latest_runs, runs_24h, runtime, cfg,
            last_success=None, last_attempt=None):
    issues = []
    rows = []
    dormant = []    # 仍是启用状态、但有效期已过的「僵尸任务」
    resolved = []   # 曾有异常、但已被更晚的成功运行（含补跑）覆盖的
    historical = []  # 超出巡检窗口的隔夜旧账，只报备不打扰

    last_success = last_success or {}
    last_attempt = last_attempt or {}

    grace = timedelta(minutes=cfg["grace_minutes"])
    stuck = timedelta(minutes=cfg["stuck_minutes"])
    failed_fresh = timedelta(hours=cfg["failed_fresh_hours"])

    # 把「补跑｜XX｜10-08」和原任务 XX 认成同一家：
    # 只要这一家里有更晚的成功运行，就说明链条已经接上了，不再重复报警。
    fam = {}
    by_id = {}
    for t in tasks:
        by_id[t["id"]] = t
        fam.setdefault(name_group(t["name"]), []).append(t["id"])

    def fam_last_success(tid):
        best = last_success.get(tid)
        for other in fam.get(name_group(by_id[tid]["name"]), []):
            v = last_success.get(other)
            if v and (best is None or v > best):
                best = v
        return best

    for t in tasks:
        tid = t["id"]
        run = latest_runs.get(tid)
        rt = runtime.get(tid, {})
        # 「最后运行时间」取三处来源里最晚的一个：
        # automations.last_run_at 经常滞后（调度器只在部分时机回写该列），
        # 真正的凭据是 automation_runs / automation_runtime_state。
        # 早期只读该列，结果把「其实刚跑过」的任务误报成「长期静默（SILENT）」。
        last = ms2dt(t["last_run_at"])
        for cand in (last_attempt.get(tid), ms2dt(rt.get("last_run_at"))):
            if cand and (last is None or cand > last):
                last = cand
        nxt = ms2dt(t["next_run_at"])
        active = t["status"].upper() == "ACTIVE"
        found = []
        recover = None

        nxt_h, last_h, sched = fmt(nxt), fmt(last), (t["rrule"] or ("once @ " + str(t["scheduled_at"])))

        # 有效期之外的任务（已失效 / 尚未开始）不参与判定
        v_until = parse_iso(t["valid_until"])
        v_from = parse_iso(t["valid_from"])
        if (v_until and v_until < now) or (v_from and v_from > now) or not active:
            if active and v_until and v_until < now:
                dormant.append({"id": tid, "name": t["name"],
                                "until": fmt(v_until), "last_run": last_h})
            rows.append({
                "id": tid, "name": t["name"], "status": t["status"], "schedule": sched,
                "last_run": last_h, "next_run": nxt_h, "runs_24h": runs_24h.get(tid, 0),
                "last_result": ("成功" if (run and run.get("success")) else ("失败" if run else "无记录")),
                "issue_kinds": [],
            })
            continue

        # 1) 卡死：单次运行迟迟不结束
        if rt.get("running"):
            started = ms2dt(rt.get("running_started_at"))
            if started and (now - started) > stuck:
                found.append({
                    "kind": "STUCK", "severity": "high",
                    "detail": "运行中已持续 %s，疑似卡死" % human_delta((now - started).total_seconds()),
                    "suggest": "结束该次运行后重启任务；必要时新建一次性补跑任务",
                })

        # 2) 停摆：该跑没跑。宽限期已放宽到 60 分钟，正常抖动不误报。
        if t["schedule_type"] == "recurring" and not rt.get("running"):
            if nxt is None:
                # 没有任何下次运行时间 = 调度器根本没接住这个任务
                if last is not None:
                    gap = now - last
                    days = gap.days
                    stale = days >= cfg["stale_days"]
                    found.append({
                        "kind": "STALLED", "severity": "high",
                        "detail": "调度器未排期（无下次运行时间），已停跑 %s" % human_delta(gap.total_seconds()),
                        "suggest": ("疑似废弃的旧任务：确认是否还需要；不需要就在自动化列表里停用，需要就立即补跑"
                                    if stale else
                                    "创建一次性补跑任务（scheduledAt=现在），并核查调度器是否被中断"),
                        "stale": stale,
                    })
            elif (now - nxt) > grace:
                found.append({
                    "kind": "STALLED", "severity": "high",
                    "expected_at": nxt.isoformat(),
                    "detail": "应在 %s 触发，已超期 %s 仍未运行" % (fmt(nxt), human_delta((now - nxt).total_seconds())),
                    "suggest": "创建一次性补跑任务（scheduledAt=现在），并核查调度器是否被中断",
                })

        # 3) 错过的一次性任务
        #    注意：这里不做「窗口压制」——该跑没跑的单次任务，多久都要一直报，
        #    直到它真的成功（自己跑成、或同家补跑跑成）或被人停用为止。
        if t["schedule_type"] == "once":
            due = parse_iso(t["scheduled_at"])
            if due and due < now:
                fs = fam_last_success(tid)
                if run and run.get("success"):
                    pass                                   # 自己跑成了
                elif fs and fs >= due:
                    # 原任务没成，但同一家的补跑任务已经跑成 → 已接上
                    recover = {"reason": "已被更晚的成功运行覆盖（含补跑）",
                               "at": fmt(fs)}
                else:
                    overdue = now - due
                    long_overdue = overdue >= timedelta(days=cfg["stale_days"])
                    found.append({
                        "kind": "OVERDUE_ONCE",
                        "severity": "high" if not long_overdue else "medium",
                        "expected_at": due.isoformat(),
                        "detail": "单次任务定于 %s，已过期 %s 且未见成功记录"
                                  % (fmt(due), human_delta(overdue.total_seconds())),
                        "suggest": ("已逾期超过 %d 天，大概率是废弃任务：确认是否还需要，不需要就停用"
                                    % cfg["stale_days"] if long_overdue else
                                    "确认是否已手动完成；若否，立即补跑"),
                        "stale": long_overdue,
                    })

        # 4) 执行失败：只有「明确失败态」或 runtime 里有错误才算，
        #    PENDING_REVIEW 这类「跑完等确认」不再被误判成失败。
        err = (rt.get("last_error") or "").strip()
        status = (run or {}).get("status") or ""
        hard_fail = bool(
            (run and (run.get("failure_code") or run.get("reason_code")))
            or status.upper() in STATUS_FAILED
            or (run and run.get("success") == 0 and status.upper() not in STATUS_OK
                and status.upper() not in STATUS_NEUTRAL and status.upper() != "")
        )
        if hard_fail or err:
            ran_at = ms2dt((run or {}).get("created_at")) or last
            fs = fam_last_success(tid)
            if fs and ran_at and fs > ran_at:
                recover = {"reason": "此后已有成功运行（含补跑），无需再管",
                           "at": fmt(fs)}
            else:
                fresh = bool(ran_at and (now - ran_at) <= failed_fresh)
                if fresh:
                    found.append({
                        "kind": "FAILED",
                        "severity": "medium",
                        "detail": "最近一次运行（%s）失败：%s" % (fmt(ran_at), (err[:200] or status or "未记录原因")),
                        "suggest": ("网络类抖动，可直接补跑一次" if is_network_error(err)
                                    else "查看报错原因后决定是否补跑"),
                        "error": err[:400],
                        "stale": False,
                    })
                else:
                    historical.append({
                        "kind": "FAILED", "name": t["name"],
                        "detail": "历史失败（%s）：%s（超出巡检窗口，仅备查）"
                                  % (fmt(ran_at), (err[:120] or status or "未记录原因")),
                    })

        # 5) 长期静默（兜底）
        ph = period_hours(t["rrule"]) if t["schedule_type"] == "recurring" else None
        if ph and last and not found:
            gap = (now - last).total_seconds()
            if gap > ph * 3600 * cfg["silent_factor"]:
                found.append({
                    "kind": "SILENT", "severity": "low",
                    "detail": "距上次运行已 %s，超过其正常周期（约 %.1f 小时）" % (human_delta(gap), ph),
                    "suggest": "观察一轮；若持续静默则按停摆处理",
                })

        # 同一任务只出一条，按最高严重级作主类型，其余作附注
        if found:
            primary = max(found, key=lambda x: SEVERITY_RANK.get(x["severity"], 0))
            notes = [f for f in found if f is not primary]
            issues.append({
                "automation_id": tid,
                "name": t["name"],
                "kind": primary["kind"],
                "kinds": [f["kind"] for f in found],
                "severity": primary["severity"],
                "detail": primary["detail"],
                "notes": [f["detail"] for f in notes],
                "suggest": primary["suggest"],
                "error": primary.get("error") or (rt.get("last_error") or "")[:400],
                "expected_at": primary.get("expected_at"),
                "stale": primary.get("stale", False),
                "schedule": sched,
                "last_run": last_h,
                "next_run": nxt_h,
                "last_error": (rt.get("last_error") or "")[:400],
            })
        elif recover:
            resolved.append({"automation_id": tid, "name": t["name"], **recover})

        rows.append({
            "id": tid, "name": t["name"], "status": t["status"], "schedule": sched,
            "last_run": last_h, "next_run": nxt_h, "runs_24h": runs_24h.get(tid, 0),
            "last_result": ("成功" if (run and run.get("success")) else ("失败" if run else "无记录")),
            "issue_kinds": [f["kind"] for f in found],
        })

    issues.sort(key=lambda x: (-SEVERITY_RANK.get(x["severity"], 0), x["kind"]))

    active = [t for t in tasks if t["status"].upper() == "ACTIVE"]
    summary = {
        "total": len(tasks),
        "active": len(active),
        "paused": len(tasks) - len(active),
        "runs_24h": sum(runs_24h.values()),
        "issue_count": len(issues),
        "by_severity": {s: len([i for i in issues if i["severity"] == s]) for s in ("high", "medium", "low")},
        "by_kind": {k: len([i for i in issues if k in i.get("kinds", [])]) for k in KIND_LABEL},
        "dormant": len(dormant),
        "resolved_count": len(resolved),
        "historical_count": len(historical),
        "healthy": len(issues) == 0,
    }
    return summary, issues, rows, dormant, resolved, historical


# ---------------------------------------------------------------- 输出

def render_text(pkg):
    s = pkg["summary"]
    lines = []
    lines.append("")
    lines.append("🩺 AI 监工体检报告   %s" % pkg["generated_at"])
    lines.append("─" * 52)
    lines.append("扫描 %d 个任务（启用 %d / 暂停 %d）· 近 24 小时共 %d 次运行"
                 % (s["total"], s["active"], s["paused"], s["runs_24h"]))
    if pkg.get("supervisor_note"):
        lines.append("⚠️  %s" % pkg["supervisor_note"])
    lines.append("")

    icons = {"high": "🔴", "medium": "🟠", "low": "🟡"}
    if s["healthy"]:
        lines.append("✅ 全部正常，没有发现掉链子。")
        lines.append("")
        if s.get("resolved_count"):
            lines.append("   已自动平账 %d 条：此前失败/逾期，但之后有成功运行（含补跑）兜住了。"
                         % s["resolved_count"])
            lines.append("")
    else:
        lines.append("发现 %d 处异常：高危 %d · 中危 %d · 低危 %d"
                     % (s["issue_count"], s["by_severity"]["high"],
                        s["by_severity"]["medium"], s["by_severity"]["low"]))
        lines.append("")

    for i, it in enumerate(pkg["issues"], 1):
        kinds = " ＋ ".join(KIND_LABEL.get(k, k).split("（")[0] for k in it.get("kinds", [it["kind"]]))
        lines.append("%s [%d] %s" % (icons.get(it["severity"], "•"), i, it["name"]))
        lines.append("    类型：%s%s" % (kinds, "   ⚑ 疑似废弃" if it.get("stale") else ""))
        lines.append("    情况：%s" % it["detail"])
        for n in it.get("notes", []):
            lines.append("    附注：%s" % n)
        lines.append("    上次运行 %s ｜ 下次应跑 %s" % (it.get("last_run", "—"), it.get("next_run", "—")))
        if it.get("last_error"):
            lines.append("    报错：%s" % it["last_error"].splitlines()[0][:120])
        lines.append("    ➜ 建议：%s" % it["suggest"])
        lines.append("")

    if pkg.get("resolved"):
        lines.append("─" * 52)
        lines.append("🤝 已自动平账 %d 条（曾失败/逾期，但之后有成功运行兜住，不算掉链子）："
                     % len(pkg["resolved"]))
        for d in pkg["resolved"]:
            lines.append("    · %s —— %s（%s）" % (d["name"], d["reason"], d.get("at", "—")))
        lines.append("")

    if pkg.get("historical"):
        lines.append("─" * 52)
        lines.append("📦 超出巡检窗口的旧账 %d 条（仅备查，不重复打扰）：" % len(pkg["historical"]))
        for d in pkg["historical"]:
            lines.append("    · %s：%s" % (d["name"], d["detail"]))
        lines.append("")

    if pkg.get("dormant"):
        lines.append("─" * 52)
        lines.append("💤 僵尸任务 %d 个（列表里显示启用，但有效期已过，不会再跑）："
                     % len(pkg["dormant"]))
        for d in pkg["dormant"]:
            lines.append("    · %s（有效期至 %s · 最后运行 %s）"
                         % (d["name"], d["until"], d["last_run"]))
        lines.append("    ➜ 建议：不需要就停用/删除；还需要就重设有效期")
        lines.append("")

    lines.append("─" * 52)
    lines.append("诊断包已存至 %s" % pkg["package_path"])
    lines.append("")
    return "\n".join(lines)


ALERT_STATE = os.path.join(STATE_DIR, "alert_state.json")


def _fingerprint(pkg):
    keys = sorted("%s:%s" % (i["automation_id"], i["kind"]) for i in pkg["issues"])
    return hashlib.md5("|".join(keys).encode("utf-8")).hexdigest()


def should_alert(pkg, cfg):
    """同一批异常在冷却期内只提醒一次；一旦出现新异常立刻提醒。"""
    fp = _fingerprint(pkg)
    now_ts = datetime.now(TZ).timestamp()
    try:
        with open(ALERT_STATE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if st.get("fingerprint") == fp and (now_ts - float(st.get("at", 0))) < cfg["alert_cooldown_hours"] * 3600:
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ALERT_STATE, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "at": now_ts, "count": int(st.get("count", 0)) + 1}, f)
    return True


def emit_alert(pkg, cfg):
    """响铃 + 语音播报（任务收尾必须响铃提醒）。

    跨平台：macOS 用 afplay/say/osascript；Windows 用 winsound + 系统自带
    SpeechSynthesizer；其它类 Unix 用 notify-send + 终端响铃。
    任何分支失败都静默忽略，绝不影响扫描与判定。
    """
    s = pkg["summary"]
    if s["healthy"] or not should_alert(pkg, cfg):
        return
    names = "、".join(i["name"][:12] for i in pkg["issues"][:2])
    more = "等" if len(pkg["issues"]) > 2 else ""
    msg = "监工发现 %d 个自动化任务掉链子了，%s%s，快看看。" % (
        s["issue_count"], names, more)

    if sys.platform == "darwin":
        for _ in range(3):
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                           stderr=subprocess.DEVNULL)
        subprocess.run(["say", "-v", "Ting-Ting", msg], stderr=subprocess.DEVNULL)
        try:
            subprocess.run([
                "osascript", "-e",
                'display notification "%s" with title "AI 监工" sound name "Glass"' % msg
            ], stderr=subprocess.DEVNULL)
        except Exception:
            pass

    elif os.name == "nt":
        try:
            import winsound
            for _ in range(3):
                winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
        try:
            # Windows 自带语音合成，无需装任何东西
            subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Speak('%s')" % msg.replace("'", "''")
            ], stderr=subprocess.DEVNULL)
        except Exception:
            pass

    else:
        # Linux / 其它类 Unix
        try:
            subprocess.run(["notify-send", "AI 监工", msg],
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass
        for _ in range(3):
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(0.4)


def emit_wake(pkg):
    """发现停摆时唤起 WorkBuddy，让调度器重新接手到期任务。"""
    stalled = [i for i in pkg["issues"] if i["kind"] in ("STALLED", "OVERDUE_ONCE")]
    if not stalled:
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", APP_NAME], stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            subprocess.run('start "" "%s"' % APP_NAME, shell=True,
                           stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["xdg-open", APP_NAME], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return True


def save_package(pkg, cfg):
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    latest = os.path.join(STATE_DIR, "latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
    if not pkg["summary"]["healthy"]:
        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        path = os.path.join(REPORT_DIR, "findings-%s.json" % stamp)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)

    # 清理过期报告
    cutoff = datetime.now(TZ) - timedelta(days=cfg["report_retention_days"])
    for fn in os.listdir(REPORT_DIR):
        fp = os.path.join(REPORT_DIR, fn)
        try:
            if os.path.getmtime(fp) < cutoff.timestamp():
                os.remove(fp)
        except Exception:
            pass
    return latest


# ---------------------------------------------------------------- 子命令

def read_last_scan():
    """读上一次巡检时间，用于判断「监工自己是不是也停摆了」。"""
    try:
        with open(os.path.join(STATE_DIR, "latest.json"), "r", encoding="utf-8") as f:
            return parse_iso(json.load(f).get("generated_at"))
    except Exception:
        return None


def cmd_scan(args):
    cfg = load_config()
    prev_scan = read_last_scan()
    now, tasks, latest_runs, runs_24h, runtime, last_success, last_attempt = collect(connect().cursor())
    summary, issues, rows, dormant, resolved, historical = analyze(
        now, tasks, latest_runs, runs_24h, runtime, cfg, last_success, last_attempt)

    pkg = {
        "tool": "ai-watchdog",
        "version": 2,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "issues": issues,
        "resolved": resolved,
        "historical": historical,
        "dormant": dormant,
        "tasks": rows,
        "config": cfg,
    }

    # 监工自查：距上次巡检这么久，说明监工期间也停摆过
    if prev_scan:
        gap = now - prev_scan
        if gap > timedelta(hours=cfg["supervisor_gap_hours"]):
            pkg["supervisor_note"] = ("监工自身距上次巡检已 %s，期间可能也停摆过"
                                      % human_delta(gap.total_seconds()))

    package_path = save_package(pkg, cfg)
    pkg["package_path"] = package_path

    if args.json:
        print(json.dumps(pkg, ensure_ascii=False, indent=2))
    else:
        print(render_text(pkg))

    if args.alert:
        emit_alert(pkg, cfg)
    if args.wake:
        if emit_wake(pkg):
            print("↻ 已唤起 %s，尝试让调度器重新接手到期任务。" % APP_NAME)

    return 1 if issues else 0


def cmd_brief(args):
    cfg = load_config()
    now, tasks, latest_runs, runs_24h, runtime, last_success, last_attempt = collect(connect().cursor())
    summary, issues, _, _, _, _ = analyze(
        now, tasks, latest_runs, runs_24h, runtime, cfg, last_success, last_attempt)
    if summary["healthy"]:
        print("✅ 监工正常 · %d 个启用任务 · 24h 内 %d 次运行 · 无异常"
              % (summary["active"], summary["runs_24h"]))
    else:
        print("🔴 %d 处异常（高危 %d）· %d 个启用任务 · 24h 内 %d 次运行"
              % (summary["issue_count"], summary["by_severity"]["high"],
                 summary["active"], summary["runs_24h"]))
    return 1 if issues else 0


PLIST_LABEL = "com.xiaohei.ai-watchdog"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % PLIST_LABEL)


def cmd_install_agent(args):
    """装一个常驻巡检：WorkBuddy 没开时也在盯着。

    macOS 走 launchd 自动安装；Windows / Linux 不往系统里塞无效配置，
    改为打印等价的系统计划任务命令，效果一致。
    """
    python_bin = sys.executable or "python3"
    script = os.path.abspath(__file__)

    if sys.platform != "darwin":
        alert_argv = "%s %s scan --alert%s" % (
            python_bin, script, " --wake" if args.with_wake else "")
        print("⚠️  install-agent 的自动安装只在 macOS（launchd）上可用。")
        print("   当前系统请用系统自带的计划任务，要跑的命令完全一样：")
        print()
        print("   %s" % alert_argv)
        print()
        if os.name == "nt":
            print("   Windows（任务计划程序，每 %d 分钟一次）：" % int(args.interval))
            print('     schtasks /Create /TN "ai-watchdog" /SC MINUTE /MO %d /TR "%s"'
                  % (int(args.interval), alert_argv))
            print('   之后删除：schtasks /Delete /TN "ai-watchdog" /F')
        else:
            print("   Linux（crontab -e 追加一行，每 %d 分钟一次）：" % int(args.interval))
            print("     */%d * * * * %s" % (int(args.interval), alert_argv))
            print("   或写一个 systemd oneshot service + timer，ExecStart 同上。")
        return 2
    os.makedirs(STATE_DIR, exist_ok=True)
    log = os.path.join(STATE_DIR, "agent.log")

    argv = [python_bin, script, "scan", "--alert"]
    if args.with_wake:
        argv.append("--wake")
    args_xml = "\n".join("    <string>%s</string>" % a for a in argv)
    plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>%s</string>
    <key>ProgramArguments</key>
    <array>
%s
    </array>
    <key>StartInterval</key>
    <integer>%d</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>%s</string>
    <key>StandardErrorPath</key>
    <string>%s</string>
</dict>
</plist>
""" % (PLIST_LABEL, args_xml, int(args.interval * 60), log, log)

    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(plist)

    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, PLIST_LABEL)],
                   stderr=subprocess.DEVNULL)
    r = subprocess.run(["launchctl", "bootstrap", domain, PLIST_PATH],
                       capture_output=True, text=True)
    if r.returncode != 0:
        # 老系统回退到 load
        r = subprocess.run(["launchctl", "load", "-w", PLIST_PATH],
                           capture_output=True, text=True)
    if r.returncode != 0:
        print("❌ 装载失败：%s" % (r.stderr.strip() or r.stdout.strip()))
        return 2
    print("✅ 常驻监工已装上：每 %d 分钟自动巡检一次，登录后自动生效。" % args.interval)
    print("   WorkBuddy 没开时也在盯着%s。" % ("，并会自动把它唤起补跑" if args.with_wake else "（只提醒，不唤起）"))
    print("   清单：%s" % PLIST_PATH)
    print("   日志：%s" % log)
    return 0


def cmd_uninstall_agent(args):
    if sys.platform != "darwin":
        print("ℹ️  本命令只负责清理 macOS 的 launchd 常驻巡检。")
        if os.name == "nt":
            print('   当前系统请执行：schtasks /Delete /TN "ai-watchdog" /F')
        else:
            print("   当前系统请删掉你自己加的 crontab / systemd timer。")
        return 2
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, PLIST_LABEL)],
                   stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "unload", PLIST_PATH], stderr=subprocess.DEVNULL)
    if os.path.exists(PLIST_PATH):
        os.remove(PLIST_PATH)
    print("🗑  常驻监工已卸载。")
    return 0


def main():
    p = argparse.ArgumentParser(description="ai-watchdog · 用 AI 监督 AI")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("scan", help="体检并输出报告")
    sp.add_argument("--json", action="store_true", help="输出 JSON 诊断包")
    sp.add_argument("--alert", action="store_true", help="有异常时响铃并语音播报")
    sp.add_argument("--wake", action="store_true", help="发现停摆时唤起 WorkBuddy")
    sp.set_defaults(func=cmd_scan)

    bp = sub.add_parser("brief", help="一行概览")
    bp.set_defaults(func=cmd_brief)

    ip = sub.add_parser("install-agent", help="装入常驻巡检（launchd，登录自启）")
    ip.add_argument("--interval", type=float, default=30, help="巡检间隔（分钟），默认 30")
    ip.add_argument("--with-wake", action="store_true",
                    help="发现停摆时自动唤起 WorkBuddy 触发补跑")
    ip.set_defaults(func=cmd_install_agent)

    up = sub.add_parser("uninstall-agent", help="卸载常驻巡检")
    up.set_defaults(func=cmd_uninstall_agent)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except Exception as e:
        print("❌ 监工运行出错：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
