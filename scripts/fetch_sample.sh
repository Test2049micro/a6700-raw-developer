#!/usr/bin/env bash
# Fetch a CC0 Sony α6700 RAW sample from raw.pixls.us for local/CI testing.
# The sample is NOT committed to git (43 MB); tests skip when it is absent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${A6700_SAMPLE:-$HERE/tests/fixtures/ILCE-6700.ARW}"
URL="https://raw.pixls.us/data/Sony/ILCE-6700/DSC00001.ARW"
mkdir -p "$(dirname "$DEST")"
if [[ -f "$DEST" ]]; then
  echo "Sample already present: $DEST"
  exit 0
fi
echo "Downloading α6700 CC0 sample (raw.pixls.us) -> $DEST"
curl -fsSL -m 300 -A "a6700-rawsmith-test/1.0" "$URL" -o "$DEST"
echo "Done. $(wc -c < "$DEST") bytes."
