#!/usr/bin/env bash
# install.sh — One-click installer for wechat-autopublish
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.wechat-autopublish"
CLAUDE_COMMANDS_DIR="$HOME/.claude/commands"

echo "=== WeChat Auto-Publish Installer ==="
echo ""

# 1. Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Please install Python 3.9+."
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python $PY_VERSION"

# 2. Install Python dependencies
echo ""
echo "Installing Python dependencies..."
pip3 install -r "$REPO_DIR/requirements.txt" --quiet
echo "[OK] Dependencies installed"

# 3. Make scripts executable
chmod +x "$REPO_DIR/scripts/pipeline.py"
chmod +x "$REPO_DIR/scripts/wechat_publish.py"
chmod +x "$REPO_DIR/scripts/generate_image.sh"
echo "[OK] Scripts made executable"

# 4. Create install directory and symlink
mkdir -p "$INSTALL_DIR"
if [ -L "$INSTALL_DIR/scripts" ]; then
  rm "$INSTALL_DIR/scripts"
fi
ln -sf "$REPO_DIR/scripts" "$INSTALL_DIR/scripts"
echo "[OK] Symlinked $INSTALL_DIR/scripts -> $REPO_DIR/scripts"

# 5. Create config from template if not exists
if [ ! -f "$INSTALL_DIR/config.json" ]; then
  cp "$REPO_DIR/config.example.json" "$INSTALL_DIR/config.json"
  echo "[OK] Created $INSTALL_DIR/config.json (edit this with your settings)"
else
  echo "[OK] Config already exists: $INSTALL_DIR/config.json"
fi

# 6. Install Claude Code skill
mkdir -p "$CLAUDE_COMMANDS_DIR"
cp "$REPO_DIR/SKILL.md" "$CLAUDE_COMMANDS_DIR/wechat-publish.md"
echo "[OK] Installed Claude Code skill: $CLAUDE_COMMANDS_DIR/wechat-publish.md"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit config:"
echo "   \$EDITOR $INSTALL_DIR/config.json"
echo ""
echo "2. Set required environment variables (add to ~/.zshrc or ~/.bashrc):"
echo "   export GOOGLE_API_KEY='your-google-api-key'"
echo "   export WECHAT_APP_ID='your-app-id'"
echo "   export WECHAT_APP_SECRET='your-app-secret'"
echo ""
echo "3. Optional environment variables:"
echo "   export WECHAT_PREVIEW_USER='your-wechat-id'   # API preview target"
echo "   export DISCORD_BOT_TOKEN='your-bot-token'      # Discord notifications"
echo "   export DISCORD_CHANNEL_ID='your-channel-id'    # Discord channel"
echo "   export HTTPS_PROXY='http://host:port'           # Network proxy"
echo ""
echo "4. Run the pipeline:"
echo "   python3 $INSTALL_DIR/scripts/pipeline.py --auto"
echo ""
echo "5. Or use Claude Code skill:"
echo "   /wechat-publish"
