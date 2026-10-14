# social-matrix-ops · 自媒体矩阵「自动发布 + AI 监工」

把「多平台定时发布」和「AI 监工巡检补跑」当成一件事来运维的 WorkBuddy Skill。

核心思路：**发布交给现成的平台 skill，本 skill 只做编排、取证、判断、补跑**。
它不重复实现任何上传逻辑，因此不会出现「两套脚本、改一处漏一处」的问题。

```
┌──────────────┐   定时触发   ┌─────────────────┐
│ WorkBuddy    │ ──────────▶ │ 平台 publisher   │ ──▶ 快手 / 抖音 / 视频号 / 小红书
│ Automation   │             │ skill（各自维护）  │
└──────┬───────┘             └─────────────────┘
       │
       │ 巡检点（发布后 +50min）
       ▼
┌──────────────┐   诊断包    ┌─────────────────┐
│ ai-watchdog  │ ──────────▶ │ AI 监工：判断 +   │ ──▶ 补跑掉链子的任务
│ 检测引擎      │  latest.json│ 自动建一次性补跑任务│
└──────────────┘             └─────────────────┘
```

## 它管什么

| 能力 | 说明 |
|---|---|
| 档期编排 | 用一张档期表统一管理「哪个号、几点、发哪个平台」，避免重复挂任务导致连发 |
| 平台发布 | 按意图路由到对应 publisher skill 的脚本，附参数、平台限制、成功判据 |
| 故障巡检 | 通过 `ai-watchdog` 取证，识别「该跑没跑 / 跑挂了 / 卡死了」 |
| 自动补跑 | 只新增一次性补跑任务，绝不改动、停用、删除任何已有任务 |
| 配乐审计 | 检查视频成品是否缺音轨 / 静音轨 / 音轨过短，自动补配 |

## 前置依赖（重要）

本 skill 是**编排层**，本身不含发布能力。使用前需先具备：

| 依赖 | 作用 | 说明 |
|---|---|---|
| WorkBuddy 应用 + Automation 能力 | 定时调度、任务管理 | 必需 |
| 四个平台 publisher skill | 实际上号发布 | 需自备：快手 / 抖音 / 微信视频号 / 小红书 各一个，脚本路径在 `references/platforms.md` 里按你的实际位置改 |
| `ai-watchdog` 检测引擎 | 只读扫描运行库、产出诊断包 | 需自备 `watchdog.py` 与 `music_audit.py`，装到 `~/.workbuddy/ai-watchdog/` |
| 带调试端口的 Chrome | 承载各平台登录态 | `--remote-debugging-port=9222 --user-data-dir=<持久目录>`，并带 `--no-proxy-server` |
| ffmpeg / ffprobe | 视频配乐、卡片转视频 | 配乐脚本依赖 |

> 缺任何一个，本 skill 仍可当**运维手册**读——判定规则、补跑流程、踩坑经验都是通用的。

## 安装

```bash
git clone https://github.com/<your-name>/social-matrix-ops.git ~/.workbuddy/skills/social-matrix-ops
```

或手动把本仓库整个目录拷到 `~/.workbuddy/skills/social-matrix-ops/`（WorkBuddy 的 Skill 目录）。

## 使用前必须改的地方

仓库里的路径是**通用化后的占位形式**，请按你的实际环境替换：

| 文件 | 需要替换的内容 |
|---|---|
| `references/platforms.md` | 四个平台脚本的真实路径（本仓库用 `~/WorkBuddy/<your-workspace>/...` 占位） |
| `references/schedule.md` | 账号矩阵、档期表、以及表中占位的 automation id |
| `references/watchdog.md` | `ai-watchdog` 的实际位置与 Python 解释器路径 |
| `references/runbook.md` | 配乐库目录（默认 `~/Downloads/Suno/`）、辅助脚本路径 |
| `SKILL.md` | 若你的发布习惯不同，调整「硬性铁律」一节 |

## 落地三步

1. **建档期**：把 `references/schedule.md` 换成你自己的档期表（哪个账号、几点、哪个平台）。
2. **建任务**：在 WorkBuddy 里为每一行档期建一个定时 Automation，一账号一天只挂一条发布任务。
3. **挂监工**：按「发布后约 +50 分钟」建巡检点 Automation，让它读 `ai-watchdog` 的诊断包，
   按 `references/watchdog.md` 的处置表判断、补跑、告警。

新增或调整发布任务后，**务必同步新增/调整对应的巡检点**——这是整套体系不塌的关键。

## 核心设计取舍

- **为什么不把发布脚本也塞进来**：发布逻辑与平台 DOM 强耦合、迭代频繁（`references/platforms.md`
  里记了大量站点特有坑）。放在各自 skill 里维护，本层只引用，是单一维护点。
- **为什么监工只新增、不改动**：自动化任务一旦被就地修改，排期和幂等性都不可控。
  补跑一律新建一次性任务，原任务不受影响。
- **为什么判定要看「是否已被兜住」**：失败/逾期但之后已有成功运行（含补跑）的，属于已平账，
  再补一次会造成重复发文。同理，隔夜旧账只在汇总里带一句，不重复打扰。

## 已知限制

- 巡检点需要人工随档期变化同步维护，本 skill 不会自动生成巡检点。
- 发布脚本若「业务失败仍返回成功」，检测引擎看不出问题——需要脚本侧写 `failure_code` 或返回非 0。
- 本仓库中的账号名、档期、automation id 均为**示例**，请替换为你自己的。

## License

MIT
