# WeChat Auto-Publish

Automated WeChat Official Account publishing pipeline. Takes a Markdown article, generates AI cover/inline images, converts to styled HTML, publishes to draft box, and sends preview — all in one command.

## Features

- **AI Image Generation** — Gemini Flash generates prompts, Imagen 4.0 creates cover (16:9) and inline (4:3) images
- **Markdown to WeChat HTML** — Inline-styled HTML that renders correctly in WeChat's editor
- **State Machine** — Resumable 8-step pipeline with automatic retry
- **3-Tier Preview** — API push > browser automation > manual fallback
- **Discord Notifications** — Optional progress updates via Discord bot (silent when unconfigured)
- **Claude Code Skill** — Install as `/wechat-publish` command

## Quick Start

```bash
git clone https://github.com/evan966890/wechat-autopublish.git
cd wechat-autopublish
bash install.sh
```

Set environment variables:

```bash
export GOOGLE_API_KEY='your-key'
export WECHAT_APP_ID='your-app-id'
export WECHAT_APP_SECRET='your-app-secret'
```

Run:

```bash
# Auto-detect latest article
python3 ~/.wechat-autopublish/scripts/pipeline.py --auto

# Specific article
python3 ~/.wechat-autopublish/scripts/pipeline.py --article ~/articles/post.md

# Publish after preview confirmation
python3 ~/.wechat-autopublish/scripts/pipeline.py --publish
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_API_KEY` | Yes | Gemini Flash + Imagen 4.0 |
| `WECHAT_APP_ID` | Yes | WeChat Official Account |
| `WECHAT_APP_SECRET` | Yes | WeChat Official Account |
| `WECHAT_PREVIEW_USER` | No | WeChat ID for API preview |
| `DISCORD_BOT_TOKEN` | No | Discord progress notifications |
| `DISCORD_CHANNEL_ID` | No | Discord channel |
| `HTTPS_PROXY` | No | Network proxy |

## Configuration

Edit `~/.wechat-autopublish/config.json` (created by `install.sh`):

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

## Pipeline Steps

1. **Locate Article** — Find `.md` file by path or auto-glob
2. **Generate Cover** — AI-generated 16:9 cover image
3. **Generate Inline** — AI-generated 4:3 inline illustration
4. **Generate Video** — Reserved
5. **Format WeChat** — Reserved
6. **Publish Draft** — Convert + upload + create draft
7. **Send Preview** — 3-tier fallback (API → browser → manual)
8. **Report Complete** — Notify and save draft info

## Preview Fallback Strategy

1. **WeChat API** (best) — Direct push via `WECHAT_PREVIEW_USER`
2. **Browser Automation** — Chrome CDP + agent-browser opens backend, clicks preview
3. **Screenshot + Report** — Takes screenshot, sends to Discord, user handles manually

## Requirements

- Python 3.9+
- `wechatpy` >= 2.0.0, `mistune` >= 3.0.0
- `curl`
- `npx agent-browser` (optional, for browser preview)

## License

MIT
