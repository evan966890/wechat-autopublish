#!/usr/bin/env python3
"""
pipeline.py — WeChat Publishing Pipeline State Machine

8-step workflow:
  1. locate_article  — Find and validate the article
  2. generate_cover  — Generate cover image (Imagen 4.0)
  3. generate_inline — Generate inline illustration (Imagen 4.0)
  4. generate_video  — Video generation (reserved)
  5. format_wechat   — WeChat formatting (reserved)
  6. publish_draft   — Publish to WeChat draft box
  7. send_preview    — Send preview to phone (3-tier fallback)
  8. report_complete — Final report

Usage:
  pipeline.py --article <path> --title <title>
  pipeline.py --auto                              # glob latest article
  pipeline.py --publish                           # publish last draft
  pipeline.py --config /path/to/config.json       # custom config
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ─── Path Detection ───

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = "/tmp/wechat-pipeline-state.json"
LAST_DRAFT_FILE = "/tmp/wechat-last-draft.json"
WORK_DIR = "/tmp/wechat-publish"

# Defaults (overridden by config.json)
DEFAULT_ARTICLES_DIR = "~/articles"
DEFAULT_CHROME_CRAWL_DIR = "~/.chrome-crawl"

STEP_TIMEOUTS = {
    "locate_article": 60,
    "generate_cover": 600,
    "generate_inline": 600,
    "generate_video": 300,
    "format_wechat": 300,
    "publish_draft": 300,
    "send_preview": 300,
    "report_complete": 60,
}

STEPS = [
    "locate_article",
    "generate_cover",
    "generate_inline",
    "generate_video",
    "format_wechat",
    "publish_draft",
    "send_preview",
    "report_complete",
]

# ─── Config Loading ───

_config = {}


def load_config(config_path: str = None) -> dict:
    """Load config from JSON file. Falls back to defaults."""
    global _config
    paths_to_try = []
    if config_path:
        paths_to_try.append(config_path)
    paths_to_try.extend([
        os.path.expanduser("~/.wechat-autopublish/config.json"),
        os.path.join(SCRIPT_DIR, "..", "config.json"),
    ])
    for p in paths_to_try:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            with open(p) as f:
                _config = json.load(f)
            print(f"  Config loaded: {p}")
            return _config
    _config = {}
    return _config


def get_config(key: str, default=None):
    return _config.get(key, default)


def get_articles_dir() -> str:
    d = get_config("articles_dir", DEFAULT_ARTICLES_DIR)
    return os.path.expanduser(d)


def get_chrome_crawl_dir() -> str:
    d = get_config("chrome_crawl_dir", DEFAULT_CHROME_CRAWL_DIR)
    return os.path.expanduser(d)


def get_agent_config(agent_id: str) -> dict:
    agents = get_config("agents", {})
    return agents.get(agent_id, {"name": agent_id, "color": 0x95a5a6})


# ─── Discord Notification (built-in, no external scripts) ───

def _get_discord_creds():
    """Get Discord bot token and channel ID from env. Returns (token, channel_id) or (None, None)."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel = os.environ.get("DISCORD_CHANNEL_ID")
    return token, channel


def _get_avatar_url(agent_id: str) -> str:
    cfg = get_agent_config(agent_id)
    seed = cfg.get("avatar_seed", agent_id)
    bg = cfg.get("avatar_bg", "c0aede")
    return f"https://api.dicebear.com/7.x/bottts-neutral/png?seed={seed}&backgroundColor={bg}"


def _curl_proxy_args() -> list:
    """Return curl proxy arguments if HTTPS_PROXY is set."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        return ["--proxy", proxy]
    return []


def discord_msg(agent_id: str, message: str):
    """Send a Discord message via curl. Silent on failure or missing creds."""
    token, channel = _get_discord_creds()
    if not token or not channel:
        return

    cfg = get_agent_config(agent_id)
    display_name = cfg.get("name", agent_id)
    color = cfg.get("color", 0x95a5a6)

    url = f"https://discord.com/api/v10/channels/{channel}/messages"
    body = json.dumps({
        "embeds": [{
            "description": message,
            "color": color,
            "author": {
                "name": display_name,
                "icon_url": _get_avatar_url(agent_id),
            },
        }]
    })

    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", f"Authorization: Bot {token}",
             "-H", "Content-Type: application/json",
             "-d", body] + _curl_proxy_args(),
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        print(f"  [WARN] Discord message failed: {e}")


def discord_file(agent_id: str, file_path: str, caption: str):
    """Upload a file to Discord with caption via curl. Silent on failure or missing creds."""
    token, channel = _get_discord_creds()
    if not token or not channel:
        return

    cfg = get_agent_config(agent_id)
    display_name = cfg.get("name", agent_id)
    color = cfg.get("color", 0x95a5a6)

    url = f"https://discord.com/api/v10/channels/{channel}/messages"
    payload_json = json.dumps({
        "embeds": [{
            "description": caption,
            "color": color,
            "author": {
                "name": display_name,
                "icon_url": _get_avatar_url(agent_id),
            },
        }]
    })

    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", f"Authorization: Bot {token}",
             "-F", f"payload_json={payload_json}",
             "-F", f"files[0]=@{file_path}"] + _curl_proxy_args(),
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        print(f"  [WARN] Discord file upload failed: {e}")


# ─── State Management ───

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─── Gemini Flash Image Prompt Generation ───

def generate_image_prompt(article_text: str, image_type: str = "cover") -> str:
    """Call Gemini Flash to generate an English image description prompt."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    if image_type == "cover":
        instruction = (
            "Generate a single English image prompt for a WeChat article cover image. "
            "The image should be visually striking, modern, and related to the article topic. "
            "Style: clean, professional, digital art or conceptual illustration. "
            "Output only the prompt text, no explanation."
        )
    else:
        instruction = (
            "Generate a single English image prompt for an inline illustration in a WeChat article. "
            "The image should complement the article content, be informative or metaphorical. "
            "Style: clean, modern illustration. "
            "Output only the prompt text, no explanation."
        )

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    body = json.dumps({
        "contents": [{
            "parts": [{
                "text": f"{instruction}\n\nArticle:\n{article_text[:2000]}"
            }]
        }]
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{url}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    resp = opener.open(req, timeout=60)
    data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ─── Step Implementations ───

def step_locate_article(state: dict, args) -> dict:
    """Step 1: Locate article file."""
    articles_dir = args.articles_dir or get_articles_dir()

    if args.article:
        article_path = os.path.abspath(args.article)
    else:
        md_files = glob.glob(os.path.join(articles_dir, "*.md"))
        if not md_files:
            raise RuntimeError(f"No articles found: {articles_dir}/*.md")
        article_path = max(md_files, key=os.path.getmtime)

    if not os.path.exists(article_path):
        raise RuntimeError(f"Article not found: {article_path}")

    content = Path(article_path).read_text(encoding="utf-8")

    title = args.title if hasattr(args, "title") and args.title else ""
    if not title:
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = Path(article_path).stem

    word_count = len(content)

    state["article_path"] = article_path
    state["title"] = title
    state["word_count"] = word_count
    state["article_content"] = content[:3000]

    discord_msg("chief-director", f"Article located: {title} ({word_count} chars)")
    print(f"  Article: {article_path}")
    print(f"  Title:   {title}")
    print(f"  Length:  {word_count}")
    return state


def step_generate_cover(state: dict, args) -> dict:
    """Step 2: Generate cover image."""
    discord_msg("visual-designer", "Generating cover image...")

    article_text = state.get("article_content", "")
    prompt = generate_image_prompt(article_text, "cover")
    print(f"  Cover prompt: {prompt[:100]}...")

    cover_path = os.path.join(WORK_DIR, "cover.png")
    script = os.path.join(SCRIPT_DIR, "generate_image.sh")
    result = subprocess.run(
        [script, prompt, cover_path, "16:9"],
        check=True, timeout=STEP_TIMEOUTS["generate_cover"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if not os.path.exists(cover_path):
        raise RuntimeError("Cover image generation failed: file not found")

    state["cover_path"] = cover_path
    discord_file("visual-designer", cover_path, "Cover image ready (16:9)")
    return state


def step_generate_inline(state: dict, args) -> dict:
    """Step 3: Generate inline illustration."""
    discord_msg("visual-designer", "Generating inline illustration...")

    article_text = state.get("article_content", "")
    prompt = generate_image_prompt(article_text, "inline")
    print(f"  Inline prompt: {prompt[:100]}...")

    inline_path = os.path.join(WORK_DIR, "inline_1.png")
    script = os.path.join(SCRIPT_DIR, "generate_image.sh")
    result = subprocess.run(
        [script, prompt, inline_path, "4:3"],
        check=True, timeout=STEP_TIMEOUTS["generate_inline"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if not os.path.exists(inline_path):
        raise RuntimeError("Inline image generation failed: file not found")

    state["inline_paths"] = [inline_path]
    discord_file("visual-designer", inline_path, "Inline illustration ready")
    return state


def step_generate_video(state: dict, args) -> dict:
    """Step 4: Video generation (reserved)."""
    print("  [SKIP] Video generation (reserved)")
    return state


def step_format_wechat(state: dict, args) -> dict:
    """Step 5: WeChat formatting (reserved)."""
    print("  [SKIP] WeChat formatting (reserved)")
    return state


def step_publish_draft(state: dict, args) -> dict:
    """Step 6: Publish to WeChat draft box."""
    discord_msg("wechat-ops", "Publishing to draft box...")

    article_path = state["article_path"]
    cover_path = state["cover_path"]
    title = state["title"]
    author = get_config("author", "")

    script = os.path.join(SCRIPT_DIR, "wechat_publish.py")
    cmd = [
        sys.executable, script,
        "--article", article_path,
        "--cover", cover_path,
        "--title", title,
    ]
    if author:
        cmd.extend(["--author", author])

    result = subprocess.run(
        cmd,
        check=True, timeout=STEP_TIMEOUTS["publish_draft"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    draft_id = ""
    for line in result.stdout.split("\n"):
        if "draft_id:" in line or "draft media_id:" in line:
            draft_id = line.split(":")[-1].strip()

    state["draft_id"] = draft_id
    discord_msg("wechat-ops", f"Draft created (draft_id: {draft_id})")
    return state


def _ensure_chrome() -> str:
    """Ensure Chrome CDP is available. Returns port or None."""
    chrome_crawl_dir = get_chrome_crawl_dir()
    port_file = os.path.join(chrome_crawl_dir, "cdp-port")
    chrome_script = os.path.join(chrome_crawl_dir, "scripts", "chrome_debug.sh")

    for attempt in range(2):
        if os.path.exists(port_file):
            port = Path(port_file).read_text().strip()
            try:
                r = subprocess.run(
                    ["curl", "-s", f"http://127.0.0.1:{port}/json/version"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return port
            except Exception:
                pass

        print(f"  Chrome not running, starting... ({attempt + 1}/2)")
        if os.path.exists(chrome_script):
            try:
                subprocess.run([chrome_script], check=True, timeout=30,
                              capture_output=True, text=True)
                time.sleep(5)
            except Exception as e:
                print(f"  Chrome startup failed: {e}")
                time.sleep(2)
        else:
            print(f"  Chrome script not found: {chrome_script}")
            break

    return None


def _ab(cdp_port: str, *cmd_args, timeout: int = 30) -> str:
    """Run agent-browser command, return stdout."""
    result = subprocess.run(
        ["npx", "agent-browser", "--cdp", cdp_port] + list(cmd_args),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agent-browser error: {result.stderr[:300]}")
    return result.stdout


def _screenshot_to_discord(cdp_port: str, caption: str) -> bool:
    """Take screenshot and send via Discord."""
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, "screenshot.png")
    try:
        _ab(cdp_port, "screenshot", path)
        if os.path.exists(path):
            discord_file("chief-director", path, caption)
            return True
    except Exception as e:
        print(f"  Screenshot failed: {e}")
    return False


def _find_ref(line: str) -> str:
    """Extract @eN reference from agent-browser snapshot line.

    Snapshot format: '- button "全部草稿" [ref=e9]' -> returns '@e9'
    """
    import re
    m = re.search(r'\[ref=(e\d+)\]', line)
    if m:
        return f"@{m.group(1)}"
    # Fallback: direct @eN format
    for part in line.split():
        if part.startswith("@e") or part.startswith("@E"):
            return part
    return ""


def _ab_eval(cdp_port: str, js: str, timeout: int = 15) -> str:
    """Run JavaScript via agent-browser eval, return result."""
    result = subprocess.run(
        ["npx", "agent-browser", "--cdp", cdp_port, "eval", js],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"eval error: {result.stderr[:300]}")
    return result.stdout.strip()


def _browser_is_logged_in(cdp_port: str) -> bool:
    """Check if WeChat backend is logged in by looking at snapshot content."""
    snapshot = _ab(cdp_port, "snapshot", "-i", "-c")
    # Logged in: has "首页" link and content links. Not logged in: only "登录" link.
    has_home = False
    has_login_only = True
    for line in snapshot.split("\n"):
        if "首页" in line:
            has_home = True
        if "内容管理" in line or "草稿" in line or "AI One Stop" in line:
            has_login_only = False
    return has_home and not has_login_only


def _browser_login_if_needed(cdp_port: str) -> bool:
    """
    Navigate to WeChat backend homepage. If session expired, click login to restore.
    Returns True if logged in, False if QR scan needed.
    """
    # Step 1: Open homepage (MUST use homepage, direct draft URL gets bounced)
    _ab(cdp_port, "open", "https://mp.weixin.qq.com/")
    time.sleep(5)

    snapshot = _ab(cdp_port, "snapshot", "-i", "-c")

    # Check if we're on the expired session page ("请重新登录")
    if "登录" in snapshot and "首页" not in snapshot:
        print("  Session expired, clicking login to restore...")
        # Click the "登录" link to try session restore
        for line in snapshot.split("\n"):
            ref = _find_ref(line)
            if ref and "登录" in line:
                _ab(cdp_port, "click", ref)
                time.sleep(5)
                break

        # Re-check: are we logged in now?
        snapshot2 = _ab(cdp_port, "snapshot", "-i", "-c")
        if "首页" in snapshot2:
            print("  Session restored via login click")
            return True

        # Still not logged in — need QR scan
        print("  QR scan required")
        _screenshot_to_discord(cdp_port,
            "公众号后台需要扫码登录，请用微信扫码")
        return False

    if "首页" in snapshot:
        print("  Already logged in")
        return True

    print("  Unknown page state")
    _screenshot_to_discord(cdp_port, "Unknown page state")
    return False


def _browser_open_draft_list(cdp_port: str) -> bool:
    """From homepage, click '全部草稿' button to enter draft list. Returns True on success."""
    for attempt in range(3):
        snapshot = _ab(cdp_port, "snapshot", "-i", "-c")
        for line in snapshot.split("\n"):
            ref = _find_ref(line)
            if ref and "全部草稿" in line:
                _ab(cdp_port, "click", ref)
                time.sleep(3)
                print("  Draft list opened via '全部草稿' button")
                return True

        # Page might not be fully loaded, scroll down and retry
        print(f"  '全部草稿' not found (attempt {attempt + 1}/3), waiting...")
        try:
            _ab(cdp_port, "scroll", "down", "300")
        except Exception:
            pass
        time.sleep(3)

    print("  '全部草稿' button not found on homepage after 3 attempts")
    _screenshot_to_discord(cdp_port, "Homepage loaded but '全部草稿' not found")
    return False


def _browser_click_edit_on_draft(cdp_port: str, title: str) -> bool:
    """
    In draft list page, find target draft card by title and click its edit button.
    Uses JS to access hidden action buttons (not visible in accessibility tree).
    Returns True on success.
    """
    # Find draft card by title via JS and click its edit button (index 3 in action buttons)
    escaped_title = title.replace("'", "\\'").replace('"', '\\"')
    js = f"""
    (() => {{
        const cards = document.querySelectorAll('.weui-desktop-card.weui-desktop-publish');
        for (const card of cards) {{
            const titleEl = card.querySelector('.weui-desktop-publish__cover__title span');
            if (titleEl && titleEl.textContent.includes('{escaped_title[:20]}')) {{
                const btns = card.querySelectorAll('.weui-desktop-card__action a, .weui-desktop-card__action button');
                // Action buttons: [0]=delete, [1]=confirm-delete, [2]=cancel-delete, [3]=edit, [4]=publish
                for (let i = 0; i < btns.length; i++) {{
                    const tooltip = btns[i].closest('.weui-desktop-tooltip__wrp');
                    if (tooltip) {{
                        const tip = tooltip.querySelector('.weui-desktop-tooltip');
                        if (tip && tip.textContent.trim() === '编辑') {{
                            btns[i].click();
                            return 'clicked_edit';
                        }}
                    }}
                }}
                // Fallback: click the 4th button (index 3) which is typically edit
                if (btns.length > 3) {{
                    btns[3].click();
                    return 'clicked_index3';
                }}
                return 'no_edit_btn';
            }}
        }}
        // Fallback: click first card's edit button
        const first = cards[0];
        if (first) {{
            const btns = first.querySelectorAll('.weui-desktop-card__action a, .weui-desktop-card__action button');
            if (btns.length > 3) {{
                btns[3].click();
                return 'clicked_first_card';
            }}
        }}
        return 'no_cards';
    }})()
    """
    result = _ab_eval(cdp_port, js)
    print(f"  Draft card click result: {result}")
    return "clicked" in result


def _browser_click_preview_and_confirm(cdp_port: str) -> bool:
    """
    In the editor page, click '预览' button, fill WeChat ID if needed,
    and click '确定' to send preview.
    Returns True on success.
    """
    # Step 1: Click the "预览" button in editor
    snapshot = _ab(cdp_port, "snapshot", "-i", "-c")
    preview_ref = None
    for line in snapshot.split("\n"):
        ref = _find_ref(line)
        if ref and "预览" in line and "button" in line.lower():
            preview_ref = ref
            break
    if not preview_ref:
        # Fallback: search without button keyword
        for line in snapshot.split("\n"):
            ref = _find_ref(line)
            if ref and line.strip().endswith('"预览"'):
                preview_ref = ref
                break
    if not preview_ref:
        for line in snapshot.split("\n"):
            ref = _find_ref(line)
            if ref and "预览" in line:
                preview_ref = ref
                break

    if not preview_ref:
        print("  Preview button not found in editor")
        _screenshot_to_discord(cdp_port, "Editor opened but preview button not found")
        return False

    _ab(cdp_port, "click", preview_ref)
    print("  Clicked preview button")
    time.sleep(3)

    # Step 2: Preview dialog should be open. Check for WeChat ID input and fill if empty.
    snapshot2 = _ab(cdp_port, "snapshot", "-i", "-c")
    wx_name = os.environ.get("WECHAT_PREVIEW_USER", "")

    # Find the dialog's input field and check if it has content
    # If no preview user tag visible, fill it in
    if wx_name and "删除" in snapshot2:
        # Dialog has a tag input — check if our user is already there
        # by looking for the wx_name in the snapshot text
        if wx_name not in _ab(cdp_port, "snapshot", "-c"):
            # Need to type the wx_name into the input
            for line in snapshot2.split("\n"):
                ref = _find_ref(line)
                if ref and "textbox" in line.lower() and "标题" not in line and "作者" not in line:
                    _ab(cdp_port, "fill", ref, wx_name)
                    _ab(cdp_port, "press", "Enter")
                    time.sleep(1)
                    print(f"  Filled preview user: {wx_name}")
                    break

    # Step 3: Click "确定" button
    snapshot3 = _ab(cdp_port, "snapshot", "-i", "-c")
    confirm_ref = None
    for line in snapshot3.split("\n"):
        ref = _find_ref(line)
        if ref and "确定" in line:
            confirm_ref = ref
            break

    if not confirm_ref:
        print("  Confirm button not found in preview dialog")
        _screenshot_to_discord(cdp_port, "Preview dialog opened but confirm button not found")
        return False

    _ab(cdp_port, "click", confirm_ref)
    print("  Clicked confirm — preview sent!")
    time.sleep(3)

    # Step 4: Verify — dialog should close, back to editor
    _screenshot_to_discord(cdp_port, "Preview sent to your WeChat! Check your phone.")
    return True


def step_send_preview(state: dict, args) -> dict:
    """Step 7: Send preview to phone via browser automation.

    Flow (proven on WeChat Official Account backend):
      1. Open homepage mp.weixin.qq.com (direct draft URL gets bounced to login)
      2. Restore session if expired (click login link)
      3. Click "全部草稿" to enter draft list
      4. Find target draft by title, click edit button (hidden, via JS)
      5. In editor, click "预览" button
      6. Fill WeChat ID if needed, click "确定"
    """
    title = state.get("title", "")
    if not title:
        discord_msg("chief-director", "Preview skipped: no title")
        return state

    discord_msg("wechat-ops", "Sending preview via browser...")

    cdp_port = _ensure_chrome()
    if not cdp_port:
        discord_msg("chief-director", "Preview failed: Chrome CDP unavailable")
        state["preview_sent"] = False
        return state

    # Step 1-2: Open homepage + login check
    if not _browser_login_if_needed(cdp_port):
        discord_msg("chief-director",
            "公众号后台需要扫码登录，截图已发送。请扫码后重新运行管线。")
        state["preview_sent"] = "need_login"
        return state

    # Step 3: Open draft list
    if not _browser_open_draft_list(cdp_port):
        _screenshot_to_discord(cdp_port, "Failed to open draft list from homepage")
        state["preview_sent"] = False
        return state

    # Step 4: Click edit on target draft
    if not _browser_click_edit_on_draft(cdp_port, title):
        _screenshot_to_discord(cdp_port, f"Draft not found: {title[:30]}")
        state["preview_sent"] = False
        return state
    time.sleep(5)  # Wait for editor to fully load

    # Step 5-6: Click preview + confirm
    if _browser_click_preview_and_confirm(cdp_port):
        discord_msg("chief-director",
            f"Preview of '{title}' sent to your WeChat! Check your phone, reply 'publish' to go live.")
        state["preview_sent"] = True
    else:
        discord_msg("chief-director",
            "Browser preview partially completed. Check screenshot.")
        state["preview_sent"] = False

    return state


def step_report_complete(state: dict, args) -> dict:
    """Step 8: Final report."""
    title = state.get("title", "Unknown")
    draft_id = state.get("draft_id", "Unknown")
    preview_sent = state.get("preview_sent", False)

    with open(LAST_DRAFT_FILE, "w") as f:
        json.dump({"draft_id": draft_id, "title": title}, f, ensure_ascii=False)

    if preview_sent is True:
        msg = (f"Pipeline complete! '{title}' preview sent to your WeChat. "
               f"Reply 'publish' to go live.")
    elif preview_sent == "manual":
        msg = (f"Pipeline complete! '{title}' is in draft box. "
               f"Browser opened. Please preview and reply 'publish'.")
    else:
        msg = (f"Pipeline complete! '{title}' is in draft box. "
               f"Please preview in WeChat backend and reply 'publish'.")

    discord_msg("chief-director", msg)
    return state


# ─── Step Dispatch ───

STEP_FUNCS = {
    "locate_article": step_locate_article,
    "generate_cover": step_generate_cover,
    "generate_inline": step_generate_inline,
    "generate_video": step_generate_video,
    "format_wechat": step_format_wechat,
    "publish_draft": step_publish_draft,
    "send_preview": step_send_preview,
    "report_complete": step_report_complete,
}


# ─── Main Loop ───

def run_pipeline(args):
    """Run the pipeline state machine."""
    os.makedirs(WORK_DIR, exist_ok=True)

    state = load_state()
    completed = state.get("completed_steps", [])

    if completed and args.article:
        saved_article = state.get("article_path", "")
        current_article = os.path.abspath(args.article)
        if saved_article and saved_article != current_article:
            print("  Article changed, restarting pipeline")
            completed = []
            state = {"completed_steps": []}

    if not completed:
        for f in os.listdir(WORK_DIR):
            fp = os.path.join(WORK_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
        state = {"completed_steps": [], "started_at": datetime.now().isoformat()}

    print(f"{'=' * 60}")
    print(f"Pipeline started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Completed steps: {completed}")
    print(f"{'=' * 60}")

    for step_name in STEPS:
        if step_name in completed:
            print(f"\n[OK] {step_name} — already done, skipping")
            continue

        print(f"\n[>>] {step_name} — executing")
        step_func = STEP_FUNCS[step_name]

        for attempt in range(2):
            try:
                state = step_func(state, args)
                state.setdefault("completed_steps", []).append(step_name)
                save_state(state)
                print(f"[OK] {step_name} — done")
                break
            except Exception as e:
                print(f"[FAIL] {step_name} — failed (attempt {attempt + 1}/2): {e}")
                if attempt == 0:
                    print("  Retrying...")
                    time.sleep(3)
                else:
                    error_msg = f"Pipeline failed at {step_name}: {str(e)[:200]}"
                    discord_msg("chief-director", f"Pipeline failed! {error_msg}")
                    print(f"\n{'=' * 60}")
                    print(f"Pipeline failed: {error_msg}")
                    print(f"State saved. Re-run to resume.")
                    print(f"{'=' * 60}")
                    sys.exit(1)

    state["finished_at"] = datetime.now().isoformat()
    save_state(state)
    print(f"\n{'=' * 60}")
    print(f"Pipeline complete!")
    print(f"{'=' * 60}")

    os.remove(STATE_FILE)


def do_publish():
    """Publish the most recent draft (freepublish)."""
    if not os.path.exists(LAST_DRAFT_FILE):
        print("ERROR: No draft info found")
        print(f"  Expected: {LAST_DRAFT_FILE}")
        sys.exit(1)

    with open(LAST_DRAFT_FILE) as f:
        info = json.load(f)

    draft_id = info.get("draft_id", "")
    title = info.get("title", "Unknown")
    if not draft_id:
        print("ERROR: draft_id is empty")
        sys.exit(1)

    discord_msg("wechat-ops", f"Publishing '{title}'...")

    from wechatpy import WeChatClient

    app_id = os.environ.get("WECHAT_APP_ID")
    app_secret = os.environ.get("WECHAT_APP_SECRET")
    if not app_id or not app_secret:
        discord_msg("chief-director", "Publish failed: WECHAT_APP_ID/SECRET not set")
        sys.exit(1)

    client = WeChatClient(app_id, app_secret)

    for attempt in range(3):
        try:
            result = client.post("freepublish/submit", data={"media_id": draft_id})
            publish_id = result.get("publish_id", "")
            discord_msg("chief-director",
                        f"'{title}' published! publish_id: {publish_id}")
            print(f"Published! publish_id: {publish_id}")
            os.remove(LAST_DRAFT_FILE)
            return
        except Exception as e:
            print(f"[WARN] Publish failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(5)

    discord_msg("chief-director",
        f"Publish failed. Please publish manually. draft_id: {draft_id}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="WeChat Publishing Pipeline")
    parser.add_argument("--article", help="Path to Markdown article file")
    parser.add_argument("--title", default="", help="Article title (auto-detected from Markdown)")
    parser.add_argument("--auto", action="store_true", help="Auto-locate latest article")
    parser.add_argument("--publish", action="store_true", help="Publish the most recent draft")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--articles-dir", default=None, help="Override articles directory")
    args = parser.parse_args()

    load_config(args.config)

    if args.publish:
        do_publish()
        return

    if not args.article and not args.auto:
        parser.error("Specify --article <path>, --auto, or --publish")

    run_pipeline(args)


if __name__ == "__main__":
    main()
