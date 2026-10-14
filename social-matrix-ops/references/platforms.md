# 四平台发布调用速查

发布前统一前置：CDP Chrome 9222 在跑、对应平台已登录、CDP proxy 就绪。

```bash
# CDP proxy 依赖自检（web-access skill）
node ~/.workbuddy/skills/web-access/scripts/check-deps.mjs
```

> ⚠️ **任何用 9222 跑的自动化脚本，必须装 `~/.workbuddy/scripts/cdp-nodialog.mjs`**，
> 接入一行 `await installDialogBlocker(ws, send, log);` 放在 `Page.enable` 之后、任何 navigate 之前。
> 不装的话，页面 `beforeunload` 弹窗会让整个 CDP 会话永久挂起。

## 快手 · kuaishou-publisher

```bash
~/.workbuddy/binaries/python/envs/default/bin/python3 \
  ~/.workbuddy/skills/kuaishou-publisher/scripts/publish.py \
  --video <视频路径> --title "<标题>" --desc "<描述>" --tags "<#标签 #标签>"
```

- 标题上限 **22 字**；话题标签**最多 4 个**（超了只弹提示不影响发布）
- `--dry-run`：走完上传+填写、停在发布前截图，**检查登录态用这个**
- ⚠️ **别拿 `--no-login-wait` 当登录检查**——已登录时它会照常走完整个发布流程，真把视频发出去
- 删除已发布作品：打开 `https://cp.kuaishou.com/article/manage/video`，卡片操作按钮只在 hover 可见，
  Playwright `.hover()` 会丢 hover 态，须用页面内 JS 派发 `mouseenter`/`mouseover`

## 抖音 · douyin-publisher

```bash
~/.workbuddy/binaries/python/envs/default/bin/python3 \
  ~/.workbuddy/skills/douyin-publisher/scripts/publish.py \
  --video <视频路径> --title "<标题>" --desc "<描述>" --tags "<#标签 #标签>"
```

- `--test` 仅启动浏览器不发布
- 另有 `.mjs` 分步脚本（`publish-video.mjs` / `fill-title.mjs` / `publish-commit.mjs`），调试时可用

## 微信视频号 · weixin-channels-publisher

**图文线**（卡片 PNG 序列）：

```bash
node ~/.workbuddy/skills/weixin-channels-publisher/scripts/publish.mjs <卡片目录> "<标题>" <desc.txt>
# 只存草稿不发表（联调用）：
node ~/.workbuddy/skills/weixin-channels-publisher/scripts/publish.mjs <卡片目录> "<标题>" <desc.txt> --draft-only
```

卡片目录里图片须命名为 `p1.png`、`p2.png`…（按文件名排序上传）。

**视频线**（卡片合成视频 + BGM）：

```bash
python3 ~/.workbuddy/bin/cards-to-video.py <卡片目录> --secs 5 --bgm
```

- 限制：图文标题 **≤22 字符**；视频「短标题」**≤16 字符**（含空格，超一字表单报错且按钮全灰）；
  描述 ≤1000；图片 ≤18 张（单张 ≤20MB）；视频 ≤8 小时 / ≤20GB
- 成功判据：`location.href` 变成 `.../post/finderNewLifePostList`，列表首条标题与本次一致
- ⚠️ **Chrome 必须带 `--no-proxy-server` 启动**，否则页面永远停在「页面初始化中」。
  排查：`ps aux | grep "[c]hrome-cdp-profile" | grep -o -- "--no-proxy-server"`
- ⛔ **绝不自作主张重启 Chrome**，缺参数就告诉用户，让用户自己关

## 小红书 · xhs-publish

```bash
node ~/WorkBuddy/<your-workspace>/outputs/xhs/xhs-publish.mjs <卡片目录> "<标题>" <desc.txt>
# 稳健重发版（等上传完成 + 严格判 success 页，推荐）：
node ~/WorkBuddy/<your-workspace>/outputs/xhs/republish.mjs <卡片目录> "<标题>" <desc.txt>
```

- 限制：标题 **≤20 字**（含 emoji，超了发布按钮不响应）；正文 **≤1000 字**（超了静默失败，只闪一个 toast）
- **只做图文，不做视频**（用户 2026-09-09 拍板，别再主动提议做视频）
- 发布按钮是 Vue 自定义组件（`xhs-publish-btn`），JS `.click()` 无效，**必须用 CDP `Input.dispatchMouseEvent` 按坐标点**
- 成功判据：URL 跳到 `.../publish/success`，或正文出现「发布成功」「笔记已发布」。
  **别用 `published=true` 当判据**（URL 残留会上次发布后一直挂着）
- 内容红线：**不带来源、不提任何其他平台名字、不写站外导流话术**，只在站内引导
- 笔记删除：`node .../delete-note.mjs "<标题关键字>"`；列表查看：`node .../probe-notes.mjs`

## 公众号 · wechat-mp-auto-publish

详见 `~/.workbuddy/skills/wechat-mp-auto-publish/`（AI 赚钱案例三场 13:00 / 18:10 / 20:50 走这条）。
文章固定结构、封面双主角、赞赏卡等规范见该 skill 与用户 MEMORY。
