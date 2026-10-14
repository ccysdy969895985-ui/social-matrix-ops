---
name: social-matrix-ops
description: 自媒体矩阵「自动发布 + AI 监工」总控。统一管理「学校号」「AI白娘子」两个账号在快手、抖音、微信视频号、小红书四个平台的定时发布矩阵（档期表 + 平台发布调用 + 补跑），并通过 AI 监工巡检任务「该跑没跑 / 跑挂了 / 卡死了」，自动补跑掉链子的任务。适用于维护发布档期、新增或调整发布任务、巡检与补跑、排查发布失败，以及任何涉及这四个平台批量发布或监工的任务。触发词：矩阵发布、4 个平台发布、自媒体矩阵、发布档期、监工、巡检、补跑、掉链子、该跑没跑。
agent_created: true
---

# 自媒体矩阵 · 自动发布 + AI 监工

把「四平台定时发布」和「AI 监工巡检补跑」当成一件事来运维。本 skill 是总控层，只做
**编排、取证、判断、补跑**；具体上号发布交给已有的四个平台 publisher skill，不重复实现。

## 三层结构

| 层 | 是什么 | 位置 |
|---|---|---|
| 发布层 | 四个平台 publisher skill（各带脚本与踩坑文档） | `~/.workbuddy/skills/{kuaishou-publisher,douyin-publisher,weixin-channels-publisher,xhs-publish}/` |
| 调度层 | WorkBuddy automation（每日定时 / 一次性补跑） | automation 列表，档期见 `references/schedule.md` |
| 监工层 | 检测引擎 + AI 监工自动化 | `~/.workbuddy/ai-watchdog/`，规则见 `references/watchdog.md` |

## 按意图路由

1. **要发内容 / 调某个平台的脚本** → 读 `references/platforms.md`（脚本路径、参数、平台限制、成功判据），
   然后加载对应平台 skill 执行。
2. **要改档期 / 加任务 / 停任务** → 读 `references/schedule.md`，改完**必须**同步监工巡检点。
3. **巡检 / 补跑 / 排查「该跑没跑」** → 读 `references/watchdog.md`，走取证 → 判定 → 补跑流程。
4. **发布失败 / 登录失效 / 配乐缺失 / 标签页残留** → 读 `references/runbook.md` 的故障处置。

## 硬性铁律（最高优先级，任何情况都不许违背）

- **绝不关闭、退出或重启调试 Chrome**（`--user-data-dir=~/.workbuddy/chrome-cdp-profile --remote-debugging-port=9222`）。
  它是四平台登录态的宿主，关掉就得重新扫码。需要改启动参数时**先告知用户，由用户自己关**。
  只关标签页可以，关窗口 / 杀进程绝对不行；也禁止 `pkill -9 -f "Google Chrome"`。
- **只处理 status 为 ACTIVE 的任务**。监工只新增补跑任务，绝不修改、停用或删除任何已有任务。
- **一个账号一天只挂一条发布任务**。同一个号挂多条会连发重复内容（2026-10-09 曾因此同一条视频连发两次）。
- **改档期 / 增删任务后，必须同步其挂靠的监工巡检点**（约定：发布后约 +50 分钟）。
- **短视频配乐只能来自 `~/Downloads/Suno/`**。禁止 `bgm-samples` / `bgm-default.mp3` 等其它来源。
  配乐由 AI 按内容调性自己挑，不用问用户。
- **跑完要清场**：关掉过程中打开的标签页（CDP `Target.closeTarget`），只留各平台登录入口页。
  清场要在出最终回复**之前**完成。用精确 targetId 列表关，别用可能误关用户页面的白名单脚本。
- **耗时任务收尾必须响铃**：`afplay` / `say` 需 `dangerouslyDisableSandbox: true`；
  播放前先用 `osascript -e 'get volume settings'` 检查是否静音，静音则先解除。

## 标准巡检工作流（AI 监工每次触发都走这条）

1. 取证：`watchdog.py scan --json`，再读 `state/latest.json`。
2. 判定：`summary.healthy == true` 且 `summary.dormant == 0` → **静默结束，不输出、不响铃**。
   `resolved`（已被成功运行兜住）与 `historical`（隔夜旧账）**都不补跑**。
3. 逐条处理 `issues`：分类处置规则见 `references/watchdog.md`。
4. 有补跑或需人工项 → 响铃告警（3 声 Glass + `say -v Ting-Ting`）。
5. 汇报：检测到几处异常、补跑了哪些、哪些需人工处理。无异常则保持安静。

详细命令、issues 分类处置表、补跑任务模板、配乐巡检，全部在 `references/watchdog.md`。
