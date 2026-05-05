#!/usr/bin/env bash
set -euo pipefail

APP_IN="${1:-dist/GEX Dashboard.app}"
SIGN_IDENTITY="${GEX_CODESIGN_IDENTITY:--}"
SIGN_WORK_DIR="${GEX_SIGNING_WORK_DIR:-}"
APP_NAME="$(basename "$APP_IN")"

if [[ ! -d "$APP_IN" ]]; then
  echo "App bundle not found: $APP_IN" >&2
  exit 1
fi

if [[ -z "$SIGN_WORK_DIR" ]]; then
  SIGN_WORK_DIR="$(mktemp -d /private/tmp/gex-dashboard-signing.XXXXXX)"
else
  mkdir -p "$SIGN_WORK_DIR"
fi

APP_OUT="$SIGN_WORK_DIR/$APP_NAME"
if [[ -e "$APP_OUT" ]]; then
  echo "Output already exists: $APP_OUT" >&2
  echo "Choose an empty GEX_SIGNING_WORK_DIR or remove the existing output." >&2
  exit 1
fi

echo "Copying clean app bundle:"
echo "  from: $APP_IN"
echo "  to:   $APP_OUT"
ditto --norsrc --noextattr "$APP_IN" "$APP_OUT"

# A clean ditto copy is usually enough, but keep this defensive cleanup for
# bundles copied from Desktop, iCloud, or File Provider-backed folders.
xattr -cr "$APP_OUT" 2>/dev/null || true
xattr -d com.apple.FinderInfo "$APP_OUT" 2>/dev/null || true
xattr -d com.apple.provenance "$APP_OUT" 2>/dev/null || true

SIGN_ARGS=(--force --deep --sign "$SIGN_IDENTITY")
if [[ -n "${GEX_CODESIGN_OPTIONS:-}" ]]; then
  SIGN_ARGS+=(--options "$GEX_CODESIGN_OPTIONS")
fi
if [[ -n "${GEX_ENTITLEMENTS_FILE:-}" ]]; then
  SIGN_ARGS+=(--entitlements "$GEX_ENTITLEMENTS_FILE")
fi

echo "Signing with identity: $SIGN_IDENTITY"
codesign "${SIGN_ARGS[@]}" "$APP_OUT"

echo "Verifying code signature"
codesign --verify --deep --strict --verbose=2 "$APP_OUT"

if [[ "${GEX_RUN_SPCTL:-0}" == "1" ]]; then
  echo "Assessing with Gatekeeper"
  spctl --assess --type execute --verbose=4 "$APP_OUT"
fi

echo "Signed app bundle: $APP_OUT"
