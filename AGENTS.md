# AGENTS.md · 给 AI 编码助手（Codex / Claude Code 等）的项目说明

## 这是什么

把「多平台定时发布」和「AI 监工巡检补跑」当成一件事来运维的 WorkBuddy 工具包。
**本仓库不含任何平台上传实现**——发布交给各自独立的 publisher skill，本仓库只做
编排、取证、判断、补跑。

## 目录结构

```
social-matrix-ops/      WorkBuddy Skill → 装到 ~/.workbuddy/skills/
  SKILL.md              意图路由 + 硬性铁律 + 标准巡检工作流
  references/
    schedule.md         账号矩阵与档期编排
    platforms.md        四平台发布脚本参数、平台限制、成功判据
    watchdog.md         取证命令 + issues 分类处置表 + 补跑模板
    runbook.md          常见故障处置
ai-watchdog/            检测引擎 → 装到 ~/.workbuddy/ai-watchdog/
  watchdog.py           只读扫描 WorkBuddy 运行库，产出诊断包；支持 scan/brief/install-agent
  music_audit.py        配乐审计：无音轨 / 静音轨 / 音轨短于时长 70%
  test_watchdog.py      判定逻辑回归测试（12 个场景）
  README.md             引擎自身的说明与调参
```

两个目录**要装到不同位置**，不要合并。

## 硬性约定（改代码前必读）

1. **检测引擎只读**。`watchdog.py` 必须以 `mode=ro` 打开数据库，任何情况下不得写入、
   停用或删除用户的自动化任务。新增功能也不要破坏这条。
2. **补跑只新增，不改动**。要拉起掉链子的任务，一律新建一次性任务；
   绝不修改、停用、删除原有任务。
3. **一个账号一天只挂一条发布任务**。挂多条会导致同一条内容连发两次。
4. **改档期必须同步改巡检点**。巡检点挂在发布任务之后约 50 分钟；
   新增/调整发布任务时，对应的巡检点要跟着新增/调整，否则整套体系会出现监控盲区。
5. **告警必须跨平台且不能影响主流程**。`emit_alert` / `emit_wake` 的所有系统调用
   都要吞掉异常（`stderr=DEVNULL` 或 try/except），失败时扫描与判定仍要正常完成。
   macOS 用 `afplay`/`say`/`osascript`；Windows 用 `winsound` + `System.Speech`；
   其它类 Unix 用 `notify-send` + 终端响铃。
6. **不写死绝对路径**。一律用 `os.path.expanduser("~")`。

## 提交前必须验证

```bash
python3 -m py_compile ai-watchdog/watchdog.py ai-watchdog/music_audit.py ai-watchdog/test_watchdog.py
python3 ai-watchdog/test_watchdog.py     # 必须 12/12 通过
python3 ai-watchdog/watchdog.py brief    # 实跑一次，确认不崩
```

改了判定逻辑（`classify` / 时效窗口 / 平账规则）时，**必须同步补 `test_watchdog.py` 的场景**，
并保证原有 12 个场景不回归。

## 已知限制（不要当成 bug 去"修"）

- `install-agent` 仅在 macOS 自动写入 launchd；Windows/Linux 只打印等价的
  `schtasks` / `crontab` 命令，这是有意为之，不是缺失。
- 判定逻辑绑定 WorkBuddy 运行库的表结构（`automations` / `automation_runs`）；
  上游大版本更新后可能需要同步调整。
- 仓库中的账号名、档期、automation id 均为**示例占位**，不是真实数据。
- 发布脚本若「业务失败仍返回成功」，检测引擎看不出问题——这需要脚本侧配合
  （写 `failure_code` 或返回非 0），不在本仓库可控范围。

## 改文档时

`references/schedule.md`、`platforms.md` 里的路径是 `~/WorkBuddy/<your-workspace>/...`
形式的占位符，保持这个风格，**不要填回任何真实本机路径或账号标识**。
