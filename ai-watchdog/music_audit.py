#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配乐巡检 music_audit.py —— 检查短视频到底有没有配上音乐。

背景：
  「看音乐有没有配上，所有的」——所有视频产出都要带配乐，漏配要在巡检里被发现。

检测三件事：
  1) 有没有音轨（无音轨 = 必漏配）
  2) 音轨是不是静音（平均音量 <= -60 dB 等于没声音）
  3) 音轨是不是只配了一小段（音频时长 < 视频时长 * 0.7）

外加一条日志腿：扫数据库里当天发布任务的标题，命中
  「无配乐 / 配乐失败 / 保留无声版 / 音乐不存在」等降级字样 → 也算漏配。

用法：
  python3 music_audit.py scan                 # 人类可读
  python3 music_audit.py scan --json          # JSON（给自动化用）
  python3 music_audit.py scan --hours 30      # 回看窗口，默认 30 小时
  python3 music_audit.py scan --limit 60      # 最多检查多少个文件，默认 80

退出码：0 = 全部正常；1 = 发现问题。
"""
import argparse
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

HOME = os.path.expanduser("~")
FFPROBE = os.path.join(HOME, ".workbuddy/bin/ffprobe")
FFMPEG = os.path.join(HOME, ".workbuddy/bin/ffmpeg")
DB_PATH = os.path.join(HOME, ".workbuddy/workbuddy.db")
STATE = os.path.join(HOME, ".workbuddy/ai-watchdog/state/music_latest.json")

if not os.path.exists(FFPROBE):
    FFPROBE = "ffprobe"
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

# ------------------------------------------------------------ 扫描目录（必配）
# 视频产出目录不写死，改从 ~/.workbuddy/ai-watchdog/config.json 读：
#   {
#     "music_roots":        ["~/WorkBuddy/my-workspace/outputs/kuaishou"],
#     "music_search_roots": ["~/WorkBuddy/my-workspace/outputs"]
#   }
#   music_roots        —— 直接扫描的产出目录（找近 N 小时有改动的 mp4）
#   music_search_roots —— 按发布标题里的文件名反查素材时，递归搜索的根目录
CONFIG_PATH = os.path.join(HOME, ".workbuddy/ai-watchdog/config.json")
_CFG = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, encoding="utf-8") as _f:
            _CFG = json.load(_f) or {}
    except Exception:
        _CFG = {}

ROOTS = [os.path.expanduser(p) for p in (_CFG.get("music_roots") or [])]
SEARCH_ROOTS = [os.path.expanduser(p) for p in (_CFG.get("music_search_roots") or [])]
if not ROOTS:
    print("⚠️ music_roots 未配置，配乐巡检将扫不到任何文件。"
          "请在 %s 里配置（示例见仓库 README）" % CONFIG_PATH, file=sys.stderr)

SKIP_SUFFIX = (".bak-oldmusic", ".bak", ".tmp")
DEGRADE_WORDS = ["无配乐", "配乐失败", "保留无声版", "音乐不存在", "未配乐", "没配乐", "静音版"]
SILENT_DB = -60.0        # 平均音量低于此值视为静音
SHORT_RATIO = 0.7        # 音频/视频时长比低于此值视为只配了一小段


def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def probe(path):
    """返回 (video_dur, has_audio, audio_dur)"""
    r = sh([FFPROBE, "-v", "error", "-show_entries",
            "stream=codec_type,duration:format=duration", "-of", "json", path], timeout=60)
    if not r or r.returncode != 0:
        return None
    try:
        j = json.loads(r.stdout)
    except Exception:
        return None
    vdur = 0.0
    adur = None
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and not vdur:
            vdur = float(s.get("duration") or 0)
        if s.get("codec_type") == "audio":
            adur = float(s.get("duration") or 0)
    if not vdur:
        vdur = float(j.get("format", {}).get("duration") or 0)
    return vdur, adur is not None, adur


def mean_volume(path):
    """解码音频取平均音量（dB）；失败返回 None"""
    r = sh([FFMPEG, "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"], timeout=180)
    if not r:
        return None
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else None


def collect_files(hours):
    """近 N 小时有改动的视频 + 当天发布标题里点名的视频"""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    files = {}
    for root in ROOTS:
        for f in glob.glob(os.path.join(root, "**", "*.mp4"), recursive=True):
            if f.endswith(SKIP_SUFFIX):
                continue
            try:
                mt = datetime.datetime.fromtimestamp(os.path.getmtime(f))
            except OSError:
                continue
            if mt >= cutoff:
                files[f] = "mtime"
    # 标题点名的文件
    named = []
    if os.path.exists(DB_PATH):
        con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
        cur = con.cursor()
        since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
        try:
            cur.execute("SELECT thread_title, automation_id FROM automation_runs "
                        "WHERE created_at > ? AND thread_title IS NOT NULL", (since,))
            for title, _aid in cur.fetchall():
                for fn in re.findall(r"([\w\u4e00-\u9fff\-\.]+\.mp4)", title or ""):
                    named.append(fn)
        finally:
            con.close()
    named = list(dict.fromkeys(named))
    for fn in named:
        for root in SEARCH_ROOTS:
            for f in glob.glob(os.path.join(root, "**", fn), recursive=True):
                if not f.endswith(SKIP_SUFFIX) and f not in files:
                    files[f] = "named"
    return files, named


def scan_degraded_logs(hours):
    """扫近 N 小时发布标题里的降级字样"""
    out = []
    if not os.path.exists(DB_PATH):
        return out
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    cur = con.cursor()
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
    try:
        cur.execute("SELECT a.name, r.thread_title, r.created_at FROM automation_runs r "
                    "LEFT JOIN automations a ON a.id = r.automation_id "
                    "WHERE r.created_at > ? AND r.thread_title IS NOT NULL", (since,))
        for name, title, ca in cur.fetchall():
            hits = [w for w in DEGRADE_WORDS if w in (title or "")]
            if hits:
                out.append({
                    "task": name,
                    "at": datetime.datetime.fromtimestamp(ca / 1000).strftime("%m-%d %H:%M"),
                    "words": hits,
                    "excerpt": re.sub(r"\s+", " ", (title or ""))[:160],
                })
    finally:
        con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="scan")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hours", type=float, default=30.0)
    ap.add_argument("--limit", type=int, default=80)
    a = ap.parse_args()

    files, named = collect_files(a.hours)
    items = list(files.items())[: a.limit]
    issues = []
    ok = 0
    for path, src in items:
        p = probe(path)
        if p is None:
            issues.append({"file": path, "kind": "probe_failed", "level": "medium",
                           "detail": "ffprobe 读不出，文件可能损坏"})
            continue
        vdur, has_audio, adur = p
        if not has_audio:
            issues.append({"file": path, "kind": "no_audio", "level": "high",
                           "detail": "没有音轨（视频无配乐）", "video_dur": round(vdur, 2)})
            continue
        mv = mean_volume(path)
        if mv is not None and mv <= SILENT_DB:
            issues.append({"file": path, "kind": "silent", "level": "high",
                           "detail": "音轨存在但是静音（平均 %s dB）" % mv,
                           "video_dur": round(vdur, 2), "mean_volume": mv})
            continue
        if adur and vdur and adur < vdur * SHORT_RATIO:
            issues.append({"file": path, "kind": "short_audio", "level": "medium",
                           "detail": "音轨只有 %.1fs，视频 %.1fs，疑似只配了一小段" % (adur, vdur),
                           "video_dur": round(vdur, 2), "audio_dur": round(adur, 2),
                           "mean_volume": mv})
            continue
        ok += 1

    degraded = scan_degraded_logs(a.hours)
    for d in degraded:
        issues.append({"file": "(日志) " + (d["task"] or "?"), "kind": "degraded_log",
                       "level": "medium",
                       "detail": "%s 出现降级字样 %s：%s" % (d["at"], d["words"], d["excerpt"])})

    result = {
        "tool": "music-audit",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": a.hours,
        "checked": len(items),
        "named_hint": named[:20],
        "ok": ok,
        "issue_count": len(issues),
        "by_kind": {k: sum(1 for i in issues if i["kind"] == k)
                    for k in ("no_audio", "silent", "short_audio", "degraded_log", "probe_failed")},
        "issues": issues,
    }
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("配乐巡检 %s（回看 %.0fh）" % (result["generated_at"], a.hours))
        print("检查 %d 个视频：正常 %d，问题 %d" % (len(items), ok, len(issues)))
        for i in issues:
            print("  [%s/%s] %s —— %s" % (i["level"], i["kind"], i["file"], i["detail"]))
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
