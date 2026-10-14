# social-matrix-ops · 自媒体矩阵「自动发布 + AI 监工」

把「多平台定时发布」和「AI 监工巡检补跑」当成一件事来运维的 WorkBuddy 工具包。

核心思路：**发布交给现成的平台 skill，本工具包只做编排、取证、判断、补跑**。
它不重复实现任何上传逻辑，因此不会出现「两套脚本、改一处漏一处」的问题。

```
┌──────────────┐   定时触发   ┌──────────────────┐
│ WorkBuddy    │ ──────────▶ │ 平台 publisher     │ ──▶ 快手 / 抖音 / 视频号 / 小红书
│ Automation   │             │ skill（各自维护）    │
└──────┬───────┘             └──────────────────┘
       │
       │ 巡检点（发布后 +50min）
       ▼
┌──────────────┐   诊断包    ┌──────────────────┐
│ ai-watchdog  │ ──────────▶ │ AI 监工：判断 +    │ ──▶ 自动建一次性补跑任务
│ 检测引擎      │  latest.json│ 补跑掉链子的任务    │
└──────────────┘             └──────────────────┘
```

## 仓库内容

| 目录 | 是什么 | 装到哪 |
|---|---|---|
| `social-matrix-ops/` | WorkBuddy Skill：档期编排 + 平台发布路由 + 巡检补跑规则 | `~/.workbuddy/skills/` |
| `ai-watchdog/` | 检测引擎：只读扫描 WorkBuddy 运行库，产出 AI 可消费的诊断包 | `~/.workbuddy/ai-watchdog/` |

两部分配合工作：**引擎负责取证，AI 负责判断和补跑**。

## 它能做什么

| 能力 | 说明 |
|---|---|
| 档期编排 | 用一张档期表统一管理「哪个号、几点、发哪个平台」，避免重复挂任务导致连发 |
| 平台发布 | 按意图路由到对应 publisher skill 的脚本，附参数、平台限制、成功判据 |
| 故障巡检 | 识别「该跑没跑 / 跑挂了 / 卡死了 / 停摆超 3 天疑似废弃」 |
| 自动补跑 | 只新增一次性补跑任务，**绝不改动、停用、删除任何已有任务** |
| 配乐审计 | 检查视频成品是否缺音轨 / 静音轨 / 音频只配了一小段 |

## 前置依赖

| 依赖 | 作用 | 是否已包含 |
|---|---|---|
| WorkBuddy 应用 + Automation 能力 | 定时调度、任务管理 | 必需 |
| 四个平台 publisher skill | 实际上号发布 | 需自备（快手 / 抖音 / 微信视频号 / 小红书 各一个） |
| **ai-watchdog 检测引擎** | 只读扫描运行库、产出诊断包 | **本仓库已包含** |
| 带调试端口的 Chrome | 承载各平台登录态 | 需自备，`--remote-debugging-port=9222 --user-data-dir=<持久目录> --no-proxy-server` |
| ffmpeg / ffprobe | 视频配乐、卡片转视频 | 需自备 |

> 缺 publisher skill 时，本工具包仍可当**运维手册**读——判定规则、补跑流程、踩坑经验都是通用的。

## 安装

```bash
git clone https://github.com/<your-name>/social-matrix-ops.git ~/social-matrix-ops

# 1) 装 Skill
mkdir -p ~/.workbuddy/skills
cp -r ~/social-matrix-ops/social-matrix-ops ~/.workbuddy/skills/

# 2) 装监工引擎
mkdir -p ~/.workbuddy/ai-watchdog
cp ~/social-matrix-ops/ai-watchdog/*.py ~/.workbuddy/ai-watchdog/
cp ~/social-matrix-ops/ai-watchdog/README.md ~/.workbuddy/ai-watchdog/
```

验证装好了：

```bash
python3 ~/.workbuddy/ai-watchdog/watchdog.py brief        # 一行概览，能出结果即 OK
python3 ~/.workbuddy/ai-watchdog/test_watchdog.py         # 判定逻辑回归测试，应 12/12 通过
```

## 首次配置

**配乐巡检必须自己配扫描目录**，否则它扫不到文件。在 `~/.workbuddy/ai-watchdog/config.json` 里写：

```json
{
  "music_roots":        ["~/WorkBuddy/my-workspace/outputs/kuaishou"],
  "music_search_roots": ["~/WorkBuddy/my-workspace/outputs"]
}
```

- `music_roots` —— 直接扫描的产出目录（找近 N 小时有改动的 mp4）
- `music_search_roots` —— 按发布标题里的文件名反查素材时，递归搜索的根目录

其余参数（判定宽限、卡死阈值、告警冷却等）见 `ai-watchdog/README.md` 的「调参」一节。

## 使用前必须改的地方

仓库里的路径是**通用化后的占位形式**，请按你的实际环境替换：

| 文件 | 需要替换的内容 |
|---|---|
| `social-matrix-ops/references/platforms.md` | 四个平台脚本的真实路径（仓库用 `~/WorkBuddy/<your-workspace>/...` 占位） |
| `social-matrix-ops/references/schedule.md` | 账号矩阵、档期表，以及表中占位的 automation id |
| `social-matrix-ops/references/runbook.md` | 配乐库目录（示例为 `~/Downloads/Suno/`）、辅助脚本路径 |
| `~/.workbuddy/ai-watchdog/config.json` | `music_roots` / `music_search_roots` |

## 落地三步

1. **建档期**：把 `references/schedule.md` 换成你自己的档期表（哪个账号、几点、哪个平台）。
2. **建任务**：在 WorkBuddy 里为每一行档期建一个定时 Automation，**一个账号一天只挂一条发布任务**。
3. **挂监工**：按「发布后约 +50 分钟」建巡检点 Automation，让它读 ai-watchdog 的诊断包，
   按 `references/watchdog.md` 的处置表判断、补跑、告警。

新增或调整发布任务后，**务必同步新增/调整对应的巡检点**——这是整套体系不塌的关键。

## 核心设计取舍

- **为什么不把发布脚本也塞进来**：发布逻辑与平台 DOM 强耦合、迭代频繁
  （`references/platforms.md` 里记了大量站点特有的坑）。放在各自 skill 里维护，本层只引用，是单一维护点。
- **为什么监工只新增、不改动**：自动化任务一旦被就地修改，排期和幂等性都不可控。
  补跑一律新建一次性任务，原任务不受影响。
- **为什么判定要看「是否已被兜住」**：失败/逾期但之后已有成功运行（含补跑）的，属于已平账，
  再补一次会造成重复发文。同理，隔夜旧账只在汇总里带一句，不重复打扰。
- **为什么检测引擎只读**：以 `mode=ro` 打开数据库，从设计上杜绝「监工自己把任务改坏」的可能。
  补跑动作由上层 AI 通过正规接口新建任务完成。

## 已知限制

- 巡检点需要人工随档期变化同步维护，本工具包不会自动生成巡检点。
- 发布脚本若「业务失败仍返回成功」，检测引擎看不出问题——需要脚本侧写 `failure_code` 或返回非 0。
- 仓库中的账号名、档期、automation id 均为**示例**，请替换为你自己的。
- 判定逻辑绑定 WorkBuddy 运行库结构（`automation_runs` / `automations` 表），WorkBuddy 大版本更新后可能需同步调整。

## License

MIT
