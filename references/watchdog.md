# AI 监工 · 巡检与补跑

## 组件

| 组件 | 路径 | 作用 |
|---|---|---|
| 检测引擎 | `~/.workbuddy/ai-watchdog/watchdog.py` | 纯规则、零依赖、**只读数据库**，负责取证 |
| 配乐审计 | `~/.workbuddy/ai-watchdog/music_audit.py` | 查无音轨 / 静音轨 / 音轨过短 / 降级字样 |
| 诊断包 | `~/.workbuddy/ai-watchdog/state/latest.json` | 最近一次体检完整结果（AI 读这个） |
| 配乐结果 | `~/.workbuddy/ai-watchdog/state/music_latest.json` | 最近一次配乐巡检结果 |
| 留档 | `~/.workbuddy/ai-watchdog/reports/findings-*.json` | 有异常时留档，默认保留 14 天 |

## 取证命令

```bash
PY=~/.workbuddy/binaries/python/versions/3.13.12/bin/python3
WD=~/.workbuddy/ai-watchdog/watchdog.py

$PY $WD scan --json     # 结构化诊断包（AI 看）
$PY $WD scan            # 体检报告（人看）
$PY $WD scan --alert    # 有异常时响铃 + 语音 + 系统通知
$PY $WD scan --wake     # 发现停摆时唤起 WorkBuddy（仅 open -a，不写数据）
$PY $WD brief           # 一行概览
```

退出码：`0` 一切正常 · `1` 发现异常 · `2` 运行出错。

配乐巡检：

```bash
$PY ~/.workbuddy/ai-watchdog/music_audit.py scan --json --hours 30 --limit 80
```

退出码 `0` 正常 / `1` 有问题。

## 判定：先看要不要管

读 `state/latest.json` 的 `summary`：

- `healthy == true` 且 `dormant == 0` → **一切正常，静默结束：不输出内容、不响铃、不打扰用户**。
- 以下两组**永远不补跑**：
  - `resolved` —— 此前失败/逾期，但之后已有成功运行（含补跑）兜住，已平账；
  - `historical` —— 超出巡检窗口的隔夜旧账，只在汇总里带一句。

## issues 分类处置表

| kind | 附加条件 | 处置 |
|---|---|---|
| `STALLED` | `stale == false` | **真掉链子 → 补跑**（见下方模板） |
| `OVERDUE_ONCE` | `stale == false` | **补跑** |
| `FAILED` | error 含 `timeout` / `Stream timeout` / `ENOTFOUND` / `502` / `Request cancelled` / `网络` 等网络类字样 | **补跑一次** |
| `FAILED` | error 为空或非网络类（如 429 配额、interrupted 空跑） | **不补跑**，按误报/临时态记录 |
| `STUCK` | — | **不自动处理**，留给用户人工判断 |
| 任意 | `stale == true`（停摆超 3 天，疑似废弃） | **不补跑**，只在汇总里提醒用户确认是否还需要 |
| `SILENT` | 周期兜底类 | 结合 DB 实况判断，别急着补（补跑可能造成重复发文） |

## 补跑任务模板

同一原任务 **24 小时内最多补跑一次**：若已存在同日期、同原任务名的补跑任务，直接跳过。

1. 先读原任务的 prompt 与 cwds：

```
automation_update(mode="view", id="<automation_id>")
```

2. 新建一次性补跑任务（**只新增，绝不修改原任务**）：

```
automation_update(
  mode="create",
  name="补跑｜<原任务名>｜<MM-DD>",
  prompt=<与原任务完全一致>,
  scheduleType="once",
  scheduledAt=<当前时间 + 2 分钟>,
  cwds=<沿用原任务>,
  status="ACTIVE"
)
```

## 巡检点挂靠规则

监工不按固定小时轮询，而是挂在各波发布之后约 **+50 分钟**：

| 巡检点 | id | 盯的任务 |
|---|---|---|
| 15:00 | `f8097cb9-fa27-42a7-a841-e4d8d0887519` | 中午波 13:00-14:30 |
| 19:10 | `3cfe0d24-6da4-4fdf-8dc7-695157300e15` | 傍晚波 17:00-18:50 |
| 21:50 | `7f1afc53-3d8a-4a0a-b9ac-b1e60b5de188` | 晚上波 20:00-21:30 |
| 23:00 | `d4e42cbb-ccbb-44ad-9987-e8e562d338c7` | 配乐巡检（所有视频任务） |

**新增或调整发布任务时，必须同步新增/调整对应巡检点。** 若某个巡检点 2 分钟内被重复触发，
沿用「同一问题不重复响铃」原则，第二次静默。

## 响铃告警

只要做了补跑、或发现需人工处理的问题，就响铃（需 `dangerouslyDisableSandbox: true`）：

```bash
# 先检查是否静音——静音时 afplay 退出码也是 0，但完全没声
osascript -e 'get volume settings'
# 若 output muted:true，先解除：
osascript -e 'set volume output muted false'

for i in 1 2 3; do afplay /System/Library/Sounds/Glass.aiff; sleep 0.4; done
say -v Ting-Ting "小白，监工发现任务掉链子，已自动补跑，请查看。"
```

## 已知误报模式（务必先核 DB 再动手）

1. **空跑被当成最近一次**：发布脚本业务失败仍写 `result_success=1`，空跑记 `reason_code=interrupted`。
   watchdog 会把这条 `interrupted` 当「最近一次运行」→ 误报 FAILED。核 DB 时看**当天实发那笔是否 succ=1**。
2. **scan 时点仍在 IN_PROGRESS**：`last_result=失败` 但 `succ=None` → 实际是在跑，不是失败，别补跑。
3. **同一任务多次成功跑**：常是「有意补发不同期内容」，不是重复发布，核 `thread_title` 里发的期号。
