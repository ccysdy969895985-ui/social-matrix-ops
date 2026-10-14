# 运维铁律 · 故障处置

## 环境

| 项 | 值 |
|---|---|
| 调试 Chrome | `--user-data-dir=~/.workbuddy/chrome-cdp-profile --remote-debugging-port=9222` |
| 白娘子实例 | 9223 |
| CDP 弹窗拦截 | `~/.workbuddy/scripts/cdp-nodialog.mjs` |
| Python | `~/.workbuddy/binaries/python/versions/3.13.12/bin/python3` |
| 隔离 venv | `~/.workbuddy/binaries/python/envs/default/bin/python3` |
| Node | `~/.workbuddy/binaries/node/versions/22.22.2-6/bin/node` |
| Suno 配乐库 | `~/Downloads/Suno/` |

## 故障 1 · CDP 会话挂起（脚本卡住不返回）

**症状**：`Runtime.evaluate` / `Page.navigate` 全不返回，任务只能超时。
**根因**：页面 `beforeunload` 弹窗（「要离开网页？」）没人点，整个 CDP 会话永久挂起。
**处置**：确认脚本已装 `cdp-nodialog.mjs`，接入一行 `await installDialogBlocker(ws, send, log);`
放在 **`Page.enable` 之后、任何 navigate 之前**。DOM 层自定义弹窗仍需脚本里自己点按钮。

## 故障 2 · 视频号发布页停在「页面初始化中」

**根因**：Chrome 缺 `--no-proxy-server`（系统代理 `127.0.0.1:6789` 拦国内域名）。
**排查**：
```bash
ps aux | grep "[c]hrome-cdp-profile" | grep -o -- "--no-proxy-server"
```
**处置**：**先告诉用户，让用户自己关 Chrome 再加参数重启**。绝不自作主张 `pkill` / `killall` / `osascript quit`。
这条是最高优先级铁律——这个窗口是四平台登录态宿主，关掉就得全部重新扫码。

## 故障 3 · 视频号登录态失效

**症状**：发布脚本 12~13s 内跳到 `login.html`，连续多场 FAIL。
**处置**：不能靠补跑解决（补跑必再失败）。响铃告知用户重新扫码登录视频号助手；
若有「视频号登录巡检」任务，确认其是否启用。
**注意**：登录失效时发布脚本可能仍写 `result_success=1`，所以 watchdog 不一定报得出来——需要抽查 `thread_title` 实况。

## 故障 4 · 视频缺配乐 / 配乐失败

**巡检**：
```bash
~/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
  ~/.workbuddy/ai-watchdog/music_audit.py scan --json --hours 30 --limit 80
```
查四类：无音轨 / 静音轨（mean_volume ≤ -60dB）/ 音轨 < 视频时长 70% / DB 标题含「无配乐·配乐失败·保留无声版」降级字样。

**补配**（从 Suno 库选曲，视频流直拷不重编码 + 淡入淡出 + 自动备份原文件）：
```bash
~/WorkBuddy/<your-workspace>/outputs/add_suno_music.sh <视频> <Suno音乐名>
# 例：add_suno_music.sh outputs/x.mp4 向前冲.mp3
```
曲目按内容调性挑（校园招生向：向前冲 / 燃爆全场 / 阳光舞步 / 甜蜜轻快曲 / Glossy Hook；
古风抒情留给非招生内容）。**不用问用户**，选完在报告里说明用了哪首即可。
配完后复核一遍 `music_audit.py scan`。

## 故障 5 · 残留标签页

**时机**：每次自动化/批量操作结束后，**在出最终回复之前**清场。
**做法**：用精确 `targetId` 列表关（CDP `Target.closeTarget`），**别用白名单脚本**——
`cleanup-tabs.mjs` 的 KEEP 白名单不含 bilibili / kuaishou，会误关用户自己开的页。
只保留各平台登录入口页（公众号后台 / 小红书创作平台 / 视频号助手 / 抖音快手创作中心）。
**只关标签页，绝不碰窗口和进程。**

## 故障 6 · 响铃没声音

**根因 1**：没加 `dangerouslyDisableSandbox: true` → `afplay` / `say` 静默失败。
**根因 2**：系统输出静音 → `afplay` 退出码仍是 0，但完全没声。
**处置**：
```bash
osascript -e 'get volume settings'            # 看 output muted
osascript -e 'set volume output muted false'  # 静音则先解除
```

## 已知遗留问题

- 发布类脚本「业务失败仍写 `result_success=1`」→ watchdog 判不出。
  建议脚本失败时返回非 0 或写 `failure_code`（**尚未获用户确认**）。
- 监工覆盖空档：22:05 学校号小红书 / 22:30 白娘子抖音之后无巡检点（22:20、22:45 已停用）。
