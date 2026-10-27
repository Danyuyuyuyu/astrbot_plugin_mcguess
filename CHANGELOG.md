# 更新日志

## v1.2.0 · 2026-10-27

- 每局默认猜测次数从 8 提高到 **25**（配置项 `max_attempts` 支持 3-50）。
- 重做卡片表头：去掉重复的「第 N/M 次 + 剩余次数」双计数，改为「第 N 次 + 剩余 N/M + 进度条」，表头高度略降。
- 模式主色：方块草绿、物品金色、生物红色，作用于图标框与进度条，便于一眼区分模式。

## v1.1.0 · 2026-10-27

- 新增**本地 Playwright 渲染后端**：默认优先在插件进程内用无头 Chromium 截图，失败自动回退 AstrBot 的 `html_render`（远端 t2i），最后回退纯文本。
- 验证机上卡片渲染从远端 t2i 的 1–10 秒降到：冷启动约 1.1 秒，热态约 0.1 秒。
- 新增配置：`render_backend`、`card_image_format`、`card_image_quality`、`render_scale`、`browser_path`、`local_render_timeout_seconds`、`remote_render_fallback`。
- Chromium 自动探测：当 Playwright 版本与已下载浏览器构建不匹配（常见于 venv 升级）时，自动使用 `ms-playwright` 缓存中最新的 Chromium；仍失败则回退远端。
- 渲染架构拆分：`renderer.py`（浏览器生命周期与截图）、`render_service.py`（后端选择、超时、熔断、回退），两者都不依赖 AstrBot，可独立单测。
- 图片改走本地字节 + `Image.fromBytes` 发送，避免公网图片下载；默认 JPEG q80（约为 PNG 体积的 1/2）。
- 新增 29 项渲染相关单测（总计 175 项）。

## v1.0.0 · 2026-10-27

- 首个版本：Wordle 式属性对比，覆盖方块 296 / 物品 195 / 生物 84 条中文词库，267 条别名。
- 指令 `/mc猜`（开始/猜/提示/状态/放弃/帮助）、`/猜方块`、`/猜物品`、`/猜生物`，支持免命令直猜与 LLM 工具 `mc_guess`。
- 每会话对局 JSON 持久化、闲置超时、群聊开局约束；图片卡片 + 纯文本降级。
