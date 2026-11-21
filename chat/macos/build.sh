#!/bin/sh
# Build "Resolve AI Chat.app" (the docked panel) with the system Swift toolchain.
#   chat/macos/build.sh <output-dir> [config.json]
# config.json: {"python": "...", "server": ".../chat/server.py", "home": "..."}
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=${1:?usage: build.sh <output-dir> [config.json]}
config=${2:-}
app="$out/Resolve AI Chat.app"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/app/Contents/MacOS" "$work/app/Contents/Resources"
xcrun swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos13.0" \
  -o "$work/app/Contents/MacOS/ResolveAIChat" "$here/main.swift"
cat > "$work/app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Resolve AI Chat</string>
  <key>CFBundleDisplayName</key><string>Resolve AI Chat</string>
  <key>CFBundleIdentifier</key><string>dev.resolve-ai-bridge.chat</string>
  <key>CFBundleExecutable</key><string>ResolveAIChat</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleURLTypes</key>
  <array><dict>
    <key>CFBundleURLName</key><string>Resolve AI Chat</string>
    <key>CFBundleURLSchemes</key><array><string>resolveaichat</string></array>
  </dict></array>
</dict>
</plist>
PLIST
if [ -n "$config" ]; then cp "$config" "$work/app/Contents/Resources/config.json"; fi
codesign --force --sign - "$work/app" >/dev/null 2>&1 || true
mkdir -p "$out"
rm -rf "$app"
mv "$work/app" "$app"
# Register the resolveaichat:// scheme so the Resolve menu entry can open the panel.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
echo "$app"
