#!/bin/bash
# 构建 macOS .app 应用包 + DMG 安装镜像
# 用法: ./build.sh
# 输出: dist/System Monitor.app + dist/SystemMonitor-2.1.0.dmg

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="System Monitor"
VERSION="1.1.1"
DIST="$DIR/dist"
APP="$DIST/$APP_NAME.app"
CONTENTS="$APP/Contents"
DMG_NAME="SystemMonitor-${VERSION}.dmg"

echo "🔨 Building $APP_NAME.app v${VERSION} ..."

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# 拷贝源文件
cp "$DIR/app.py" "$CONTENTS/Resources/"
cp "$DIR/apple_metrics.py" "$CONTENTS/Resources/"
cp "$DIR/requirements.txt" "$CONTENTS/Resources/"
# 拷贝图标
[ -f "$DIR/AppIcon.icns" ] && cp "$DIR/AppIcon.icns" "$CONTENTS/Resources/"
[ -f "$DIR/icon.png" ] && cp "$DIR/icon.png" "$CONTENTS/Resources/"

# 探测 arm64 Python3 路径
PYTHON3=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ]; then
        PYTHON3="$candidate"
        break
    fi
done
echo "   Python: $PYTHON3 ($(file -b "$PYTHON3" | grep -o 'arm64\|x86_64'))"

# 启动器脚本
cat > "$CONTENTS/MacOS/SystemMonitor" << 'LAUNCHER'
#!/bin/bash
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
VENV="$HOME/Library/Application Support/SystemMonitor/venv"
LOG="$HOME/Library/Application Support/SystemMonitor/launch.log"

# 查找 Python3
PYTHON3=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$p" ] && PYTHON3="$p" && break
done
if [ -z "$PYTHON3" ]; then
    osascript -e 'display alert "System Monitor" message "Python 3 not found.\nInstall via: brew install python" as critical'
    exit 1
fi

# 首次运行：弹通知 + 安装依赖
if [ ! -f "$VENV/bin/python3" ]; then
    osascript -e 'display notification "Installing dependencies (~30s)..." with title "System Monitor" subtitle "First Launch"'
    mkdir -p "$HOME/Library/Application Support/SystemMonitor"
    "$PYTHON3" -m venv "$VENV" 2>/dev/null
    "$VENV/bin/python3" -m pip install -q -r "$RESOURCES/requirements.txt" 2>"$LOG"
    if [ $? -ne 0 ]; then
        osascript -e 'display alert "System Monitor" message "Dependency install failed. Check internet." as critical'
        exit 1
    fi
    osascript -e 'display notification "Look for CPU% in menu bar ↗" with title "System Monitor" subtitle "Ready!"'
fi

# 运行 app，崩溃自动重启（最多 3 次）
RETRIES=0
while [ $RETRIES -lt 3 ]; do
    "$VENV/bin/python3" "$RESOURCES/app.py" >>"$LOG" 2>&1
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 143 ] || [ $EXIT_CODE -eq 15 ]; then
        break  # 正常退出或被 SIGTERM 终止
    fi
    RETRIES=$((RETRIES + 1))
    echo "$(date): Crashed (exit=$EXIT_CODE), restart $RETRIES/3" >> "$LOG"
    sleep 2
done
LAUNCHER
chmod +x "$CONTENTS/MacOS/SystemMonitor"

# Info.plist
cat > "$CONTENTS/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SystemMonitor</string>
    <key>CFBundleName</key>
    <string>System Monitor</string>
    <key>CFBundleDisplayName</key>
    <string>macOS System Monitor</string>
    <key>CFBundleIdentifier</key>
    <string>com.marshallzheng.systemmonitor</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>LSUIElement</key>
    <true/>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2026 Marshall Zheng</string>
</dict>
</plist>
PLIST

# 清除 quarantine 标记，防止 macOS 静默拒绝启动
xattr -cr "$APP" 2>/dev/null

echo "✅ $APP_NAME.app 构建完成"

# ── 构建 DMG ──
echo ""
echo "📦 Building DMG ..."

DMG_TMP="$DIST/dmg_tmp"
rm -rf "$DMG_TMP" "$DIST/$DMG_NAME"
mkdir -p "$DMG_TMP"

# 拷贝 .app 到临时目录
cp -r "$APP" "$DMG_TMP/"

# 创建 Applications 快捷方式
ln -s /Applications "$DMG_TMP/Applications"

# 生成 DMG
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$DMG_TMP" \
    -ov -format UDZO \
    "$DIST/$DMG_NAME" \
    -quiet

rm -rf "$DMG_TMP"

DMG_SIZE=$(du -h "$DIST/$DMG_NAME" | cut -f1 | xargs)
echo "✅ DMG 构建完成: dist/$DMG_NAME ($DMG_SIZE)"
echo ""
echo "═══════════════════════════════════════════"
echo "  安装方法:"
echo "    1. 双击 dist/$DMG_NAME"
echo "    2. 将 System Monitor 拖入 Applications"
echo ""
echo "  直接运行:"
echo "    open 'dist/$APP_NAME.app'"
echo "═══════════════════════════════════════════"
