#!/usr/bin/env bash
# Build Chaplin.app — double-clickable macOS app that opens the Chaplin UI.
#
#   ./build_app.sh
#
# Bundles the Electron shell plus the Chaplin Python source into one .app, so
# the whole thing launches from Finder with no terminal and no manual server.
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="Chaplin"
BUNDLE_ID="com.chaplin.trainer"
PROJECT_ROOT="$(cd .. && pwd)"

echo "==> checking packager"
if [ ! -d node_modules/@electron/packager ]; then
  npm install --no-save @electron/packager
fi

echo "==> packaging Electron shell"
rm -rf dist
npx electron-packager . "$APP_NAME" \
  --platform=darwin \
  --arch="$(uname -m)" \
  --out=dist \
  --overwrite \
  --app-bundle-id="$BUNDLE_ID" \
  --app-version="1.0.0" \
  --extend-info=extend-info.plist \
  --prune=true \
  --ignore="^/dist" \
  --ignore="^/node_modules/@electron/packager"

BUILT="dist/$APP_NAME-darwin-$(uname -m)/$APP_NAME.app"
RES_SRC="$BUILT/Contents/Resources/chaplin-src"

echo "==> bundling the Chaplin source into the app"
mkdir -p "$RES_SRC"

# Copy the Python project (source + static assets + models), skipping the
# things that are big, generated, or machine-specific.
rsync -a \
  --exclude 'data/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'mac-app/' \
  --exclude 'mac-electron/' \
  --exclude 'mac/' \
  --exclude 'recordings/' \
  --exclude '*.zip' \
  --exclude '.DS_Store' \
  "$PROJECT_ROOT/" "$RES_SRC/" 2>/dev/null || \
  cp -R "$PROJECT_ROOT/." "$RES_SRC/"

echo "==> ad-hoc codesign"
codesign --force --deep --sign - "$BUILT" >/dev/null 2>&1 || echo "    (codesign skipped)"

echo
echo "Built: $(pwd)/$BUILT"
echo
echo "Install:"
echo "  rm -rf /Applications/$APP_NAME.app"
echo "  cp -R \"$BUILT\" /Applications/"
echo "  open /Applications/$APP_NAME.app"
