#!/usr/bin/env bash
# generate_image.sh — Generate images via Google Imagen 4.0 API
# Usage: generate_image.sh <prompt> <output-path> [aspect-ratio]
# aspect-ratio: 1:1 | 3:4 | 4:3 | 9:16 | 16:9 (default: 16:9)
#
# Environment variables:
#   GOOGLE_API_KEY  — Google API Key (required)
#   HTTPS_PROXY     — HTTP/HTTPS proxy (optional)

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: generate_image.sh <prompt> <output-path> [aspect-ratio]"
  echo "  prompt:       English image description"
  echo "  output-path:  Output file path (PNG)"
  echo "  aspect-ratio: 1:1 | 3:4 | 4:3 | 9:16 | 16:9 (default: 16:9)"
  exit 1
fi

PROMPT="$1"
OUTPUT_PATH="$2"
ASPECT_RATIO="${3:-16:9}"

if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "ERROR: GOOGLE_API_KEY not set" >&2
  exit 1
fi

MODEL="imagen-4.0-generate-001"
API_URL="https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:predict"

REQUEST_BODY=$(cat <<EOF
{
  "instances": [{"prompt": "${PROMPT}"}],
  "parameters": {
    "sampleCount": 1,
    "aspectRatio": "${ASPECT_RATIO}"
  }
}
EOF
)

echo "Generating image..."
echo "  Model: ${MODEL}"
echo "  Ratio: ${ASPECT_RATIO}"
echo "  Prompt: ${PROMPT:0:80}..."

# Call API (with optional proxy)
PROXY_ARG=""
if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
  PROXY_ARG="--proxy ${HTTPS_PROXY:-${https_proxy:-}}"
fi

RESPONSE=$(curl -s -w "\n%{http_code}" \
  ${PROXY_ARG} \
  -X POST "${API_URL}" \
  -H "x-goog-api-key: ${GOOGLE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "${REQUEST_BODY}" \
  --max-time 120)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: API returned HTTP ${HTTP_CODE}" >&2
  echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY" >&2
  exit 1
fi

# Extract base64 image data and save
echo "$BODY" | python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
preds = data.get('predictions', [])
if not preds:
    print('ERROR: No predictions in response', file=sys.stderr)
    print(json.dumps(data, indent=2), file=sys.stderr)
    sys.exit(1)
b64 = preds[0].get('bytesBase64Encoded', '')
if not b64:
    print('ERROR: No base64 image data in response', file=sys.stderr)
    sys.exit(1)
img = base64.b64decode(b64)
with open('${OUTPUT_PATH}', 'wb') as f:
    f.write(img)
print(f'Image saved: ${OUTPUT_PATH} ({len(img)} bytes)')
"

echo "Done!"
