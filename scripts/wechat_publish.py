#!/usr/bin/env python3
"""
wechat_publish.py — WeChat Official Account Auto-Publisher

Usage:
  wechat_publish.py --article <md-file> --cover <image-file> [--title <title>] [--author <author>] [--digest <digest>]

Workflow:
  1. Markdown -> WeChat HTML (inline styles)
  2. Upload cover as permanent material
  3. Upload inline images as permanent material, replace src
  4. Create draft
  5. Output draft media_id

Environment variables:
  WECHAT_APP_ID      — WeChat Official Account App ID (required)
  WECHAT_APP_SECRET  — WeChat Official Account App Secret (required)
"""

import argparse
import os
import re
import sys
from pathlib import Path

import mistune
from wechatpy import WeChatClient
from wechatpy.exceptions import WeChatClientException


# ─── Markdown -> WeChat HTML (inline styles) ───

class WeChatRenderer(mistune.renderers.html.HTMLRenderer):
    """WeChat-compatible HTML renderer with all styles inlined (mistune 3.x)."""

    NAME = "wechat"

    _P_STYLE = "font-size:16px;line-height:1.75;color:#333;margin:0 0 1em 0;letter-spacing:0.5px;"
    _H_STYLES = {
        1: "font-size:24px;font-weight:bold;color:#1a1a1a;margin:1.2em 0 0.6em 0;line-height:1.4;",
        2: "font-size:20px;font-weight:bold;color:#1a1a1a;margin:1em 0 0.5em 0;line-height:1.4;",
        3: "font-size:18px;font-weight:bold;color:#1a1a1a;margin:0.8em 0 0.4em 0;line-height:1.4;",
    }
    _BQ_STYLE = "border-left:4px solid #ddd;padding:8px 16px;margin:1em 0;color:#666;font-size:15px;background:#f9f9f9;"
    _CODE_INLINE = "background:#f5f5f5;padding:2px 6px;border-radius:3px;font-family:Menlo,Consolas,monospace;font-size:14px;color:#c7254e;"
    _CODE_BLOCK = "background:#f5f5f5;padding:16px;border-radius:6px;margin:1em 0;font-family:Menlo,Consolas,monospace;font-size:13px;line-height:1.6;overflow-x:auto;white-space:pre-wrap;word-wrap:break-word;color:#333;"
    _IMG = "max-width:100%;height:auto;display:block;margin:1em auto;border-radius:4px;"
    _LINK = "color:#576b95;text-decoration:none;"
    _LIST = "font-size:16px;line-height:1.75;color:#333;margin:0 0 1em 0;padding-left:2em;"
    _LI = "margin:0.3em 0;"

    def paragraph(self, text):
        return f'<p style="{self._P_STYLE}">{text}</p>\n'

    def heading(self, text, level, **attrs):
        style = self._H_STYLES.get(level, self._H_STYLES[3])
        return f'<h{level} style="{style}">{text}</h{level}>\n'

    def block_quote(self, text):
        return f'<blockquote style="{self._BQ_STYLE}">{text}</blockquote>\n'

    def block_code(self, code, info=None):
        from html import escape
        return f'<pre style="{self._CODE_BLOCK}"><code>{escape(code)}</code></pre>\n'

    def codespan(self, text):
        return f'<code style="{self._CODE_INLINE}">{text}</code>'

    def image(self, text, url, title=None):
        return f'<img src="{url}" alt="{text}" style="{self._IMG}" />'

    def link(self, text, url, title=None):
        return f'<a href="{url}" style="{self._LINK}">{text}</a>'

    def list(self, text, ordered, **attrs):
        tag = "ol" if ordered else "ul"
        return f'<{tag} style="{self._LIST}">{text}</{tag}>\n'

    def list_item(self, text):
        return f'<li style="{self._LI}">{text}</li>\n'

    def thematic_break(self):
        return '<hr style="border:none;border-top:1px solid #ddd;margin:2em 0;" />\n'


def md_to_wechat_html(md_text: str) -> str:
    """Convert Markdown to WeChat-compatible inline-styled HTML."""
    renderer = WeChatRenderer()
    md = mistune.create_markdown(renderer=renderer, plugins=["table", "strikethrough"])
    html = md(md_text)
    wrapper_style = (
        "max-width:100%;padding:0;margin:0;font-family:-apple-system,"
        "BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB',"
        "'Microsoft YaHei',sans-serif;"
    )
    return f'<div style="{wrapper_style}">{html}</div>'


# ─── WeChat Publishing Logic ───

def upload_image(client: WeChatClient, image_path: str) -> str:
    """Upload image as permanent material. Returns (media_id, url)."""
    print(f"  Uploading image: {image_path}")
    with open(image_path, "rb") as f:
        result = client.material.add("image", f)
    media_id = result.get("media_id", "")
    url = result.get("url", "")
    print(f"  -> media_id: {media_id}")
    if url:
        print(f"  -> url: {url}")
    return media_id, url


def upload_content_image(client: WeChatClient, image_path: str) -> str:
    """Upload inline image for article body. Returns WeChat CDN URL."""
    print(f"  Uploading inline image: {image_path}")
    with open(image_path, "rb") as f:
        result = client.material.add("image", f)
    url = result.get("url", "")
    print(f"  -> CDN URL: {url}")
    return url


def replace_local_images(client: WeChatClient, html: str, article_dir: str) -> str:
    """Scan HTML for local image paths, upload them, replace with CDN URLs."""
    img_pattern = re.compile(r'<img\s+[^>]*src="([^"]+)"', re.IGNORECASE)
    matches = img_pattern.findall(html)

    for src in matches:
        if src.startswith("http://") or src.startswith("https://"):
            continue

        if os.path.isabs(src):
            local_path = src
        else:
            local_path = os.path.join(article_dir, src)

        if not os.path.exists(local_path):
            print(f"  WARNING: Local image not found, skipping: {local_path}")
            continue

        cdn_url = upload_content_image(client, local_path)
        if cdn_url:
            html = html.replace(f'src="{src}"', f'src="{cdn_url}"')

    return html


def create_draft(client: WeChatClient, title: str, content: str,
                 cover_media_id: str, author: str = "", digest: str = "") -> str:
    """Create WeChat Official Account draft."""
    article = {
        "title": title,
        "author": author,
        "content": content,
        "thumb_media_id": cover_media_id,
        "digest": digest or title,
        "show_cover_pic": 1,
        "need_open_comment": 1,
    }
    print("  Creating draft...")
    result = client.post("draft/add", data={"articles": [article]})
    media_id = result.get("media_id", "")
    print(f"  -> draft media_id: {media_id}")
    return media_id


def extract_title_from_md(md_text: str) -> str:
    """Extract title from Markdown (first # heading)."""
    for line in md_text.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="WeChat Official Account Auto-Publisher")
    parser.add_argument("--article", required=True, help="Markdown article file path")
    parser.add_argument("--cover", required=True, help="Cover image file path")
    parser.add_argument("--title", default="", help="Article title (auto-detected from Markdown)")
    parser.add_argument("--author", default="", help="Author name")
    parser.add_argument("--digest", default="", help="Article digest")
    args = parser.parse_args()

    app_id = os.environ.get("WECHAT_APP_ID")
    app_secret = os.environ.get("WECHAT_APP_SECRET")
    if not app_id or not app_secret:
        print("ERROR: WECHAT_APP_ID and WECHAT_APP_SECRET must be set", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.article):
        print(f"ERROR: Article not found: {args.article}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.cover):
        print(f"ERROR: Cover image not found: {args.cover}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading article: {args.article}")
    md_text = Path(args.article).read_text(encoding="utf-8")

    title = args.title or extract_title_from_md(md_text)
    if not title:
        title = Path(args.article).stem
    print(f"Title: {title}")

    print("Converting Markdown -> WeChat HTML...")
    html = md_to_wechat_html(md_text)
    print(f"  HTML length: {len(html)} chars")

    print("Initializing WeChat client...")
    client = WeChatClient(app_id, app_secret)

    print("Uploading cover image...")
    cover_media_id, _ = upload_image(client, args.cover)
    if not cover_media_id:
        print("ERROR: Cover image upload failed", file=sys.stderr)
        sys.exit(1)

    article_dir = os.path.dirname(os.path.abspath(args.article))
    html = replace_local_images(client, html, article_dir)

    draft_media_id = create_draft(
        client,
        title=title,
        content=html,
        cover_media_id=cover_media_id,
        author=args.author,
        digest=args.digest,
    )

    if draft_media_id:
        print(f"\nPublish successful!")
        print(f"  draft_id: {draft_media_id}")
        print(f"  Please preview in WeChat backend before publishing")
    else:
        print("ERROR: Draft creation failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
