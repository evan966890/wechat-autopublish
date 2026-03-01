---
name: wechat-publish
description: WeChat Official Account auto-publish pipeline. Generates AI cover/inline images, converts Markdown to styled HTML, creates drafts, sends previews with 3-tier fallback (API/browser/manual).
---

# WeChat Auto-Publish Pipeline

微信公众号自动发布管线。从 Markdown 文章到草稿发布，全流程自动化。

## 8 步流程

1. **定位文章** — 指定路径或自动 glob 最新 `.md` 文件
2. **生成封面图** — Gemini Flash 生成 prompt → Imagen 4.0 生成 16:9 封面
3. **生成文内配图** — 同上，4:3 比例
4. **视频生成** — 预留接口
5. **微信排版** — 预留接口
6. **发布草稿** — Markdown → 内联样式 HTML → 上传封面 → 创建草稿
7. **发送预览** — 3 层降级策略（见下文）
8. **完成汇报** — Discord 通知 + 保存 draft_id

## 使用方法

### 自动模式（glob 最新文章）

```bash
python3 ~/.wechat-autopublish/scripts/pipeline.py --auto
```

### 指定文章

```bash
python3 ~/.wechat-autopublish/scripts/pipeline.py --article ~/articles/my-post.md --title "文章标题"
```

### 正式发布（预览确认后）

```bash
python3 ~/.wechat-autopublish/scripts/pipeline.py --publish
```

### 自定义配置

```bash
python3 ~/.wechat-autopublish/scripts/pipeline.py --auto --config /path/to/config.json --articles-dir ~/my-articles
```

## 3 层预览降级

1. **微信 API 直推**（最优）— 需要 `WECHAT_PREVIEW_USER` 环境变量（微信号），直接推送到手机
2. **浏览器自动化**（次优）— 通过 `agent-browser` + Chrome CDP 自动打开公众号后台、定位草稿、点击预览
3. **截图 + 报告**（兜底）— 截取当前页面发送到 Discord，提示用户手动操作

## 配置

### 环境变量

| 变量 | 必需 | 用途 |
|------|------|------|
| `GOOGLE_API_KEY` | 是 | Gemini Flash + Imagen 4.0 |
| `WECHAT_APP_ID` | 是 | 微信公众号 AppID |
| `WECHAT_APP_SECRET` | 是 | 微信公众号 AppSecret |
| `WECHAT_PREVIEW_USER` | 否 | API 预览目标微信号 |
| `DISCORD_BOT_TOKEN` | 否 | Discord 进度通知 |
| `DISCORD_CHANNEL_ID` | 否 | Discord 通知频道 |
| `HTTPS_PROXY` | 否 | 网络代理 |

### config.json

放在 `~/.wechat-autopublish/config.json`（从 `config.example.json` 复制修改）：

```json
{
  "articles_dir": "~/articles",
  "chrome_crawl_dir": "~/.chrome-crawl",
  "author": "My Account",
  "agents": {
    "chief-director": {"name": "Director", "color": 16028438},
    "visual-designer": {"name": "Designer", "color": 15315720},
    "wechat-ops": {"name": "Publisher", "color": 3066993}
  }
}
```

## 排查指南

### 封面图生成失败
- 检查 `GOOGLE_API_KEY` 是否设置且有效
- 检查代理是否通畅：`curl --proxy $HTTPS_PROXY https://generativelanguage.googleapis.com/`

### 草稿创建失败
- 检查 `WECHAT_APP_ID` / `WECHAT_APP_SECRET`
- 确认公众号 IP 白名单包含当前出口 IP

### API 预览失败
- `WECHAT_PREVIEW_USER` 必须是关注了该公众号的微信号
- 预览接口有频率限制（每日 100 次）

### 浏览器预览需要登录
- 检查 Chrome CDP 是否运行：`curl http://127.0.0.1:$(cat ~/.chrome-crawl/cdp-port)/json/version`
- 需要扫码登录时会自动截图发到 Discord

### Discord 通知不工作
- 无 `DISCORD_BOT_TOKEN` 时静默跳过（不报错）
- Bot 需要频道的发送消息 + 上传文件权限

## 依赖

- Python 3.9+
- `wechatpy` >= 2.0.0
- `mistune` >= 3.0.0
- `curl`（图片生成 API 调用）
- `npx agent-browser`（可选，浏览器自动化预览）
