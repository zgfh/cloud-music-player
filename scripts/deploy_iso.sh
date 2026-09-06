#!/bin/bash
set -euo pipefail

# 确保在项目根目录执行
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# launchd/cron 不会加载交互式 shell 环境。Flet 在生成 Flutter
# 工程时就需要 flutter/dart/pod，因此必须在 flet build 之前补全 PATH。
FLUTTER_ROOT="${FLUTTER_ROOT:-/Users/zzg/flutter/3.44.8}"
export PATH="$FLUTTER_ROOT/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

mkdir -p scripts/logs

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_message() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

usage() {
    echo "用法: bash scripts/deploy_iso.sh [选项]"
    echo ""
    echo "选项:"
    echo "  (无参数)     自动判断：源码有更新则完整重建，否则仅刷新签名"
    echo "  --rebuild    强制完整重建（Flet 生成工程 + Xcode 构建签名）"
    echo "  --refresh    优先仅刷新签名；产物不安全时自动完整重建"
    echo "  --fast       快速模式：debug 增量构建代替 release archive，迭代测试快数倍"
    echo "  --help       显示帮助"
    echo ""
    echo "说明: 免费开发者账号签名有效期 7 天，请每 7 天内重跑一次以续签。"
}

# ====== 参数解析 ======
FORCE_REBUILD=0
FORCE_REFRESH=0
FAST_MODE=0
for arg in "$@"; do
    case "$arg" in
        --rebuild|-r) FORCE_REBUILD=1 ;;
        --refresh|-s) FORCE_REFRESH=1 ;;
        --fast|-f) FAST_MODE=1 ;;
        --help|-h) usage; exit 0 ;;
        *) log_message "${RED}❌ 未知参数: $arg${NC}"; usage; exit 1 ;;
    esac
done

if [ "$FORCE_REBUILD" = "1" ] && [ "$FORCE_REFRESH" = "1" ]; then
    log_message "${RED}❌ --rebuild 与 --refresh 不能同时使用${NC}"
    exit 1
fi

# ====== 自动检测设备 ======
log_message "${YELLOW}📱 查找连接的 iOS 设备...${NC}"

DEVICE_LINE=$(xcrun devicectl list devices 2>&1 | grep -i "iphone" | grep -v "Simulator" | head -1)

if [ -z "$DEVICE_LINE" ]; then
    log_message "${RED}❌ 未找到连接的 iOS 设备，请连接后重试${NC}"
    exit 1
fi

DEVICE_ID=$(echo "$DEVICE_LINE" | grep -oE '[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}' | head -1)
DEVICE_NAME=$(echo "$DEVICE_LINE" | awk '{print $1}')

# 提取设备状态（available / unavailable / booted 等）
# 注意: 锁屏或待机时状态为 unavailable，此时 devicectl 无法安装应用
if echo "$DEVICE_LINE" | grep -qi "unavailable"; then
    DEVICE_STATE="unavailable"
else
    DEVICE_STATE="available"
fi

log_message "${GREEN}✅ 找到设备: $DEVICE_NAME ($DEVICE_ID) [状态: $DEVICE_STATE]${NC}"

if [ "$DEVICE_STATE" = "unavailable" ]; then
    log_message "${RED}⚠️ 设备不可用（手机可能锁屏/待机/断开），devicectl 无法安装应用${NC}"
    log_message "${YELLOW}将跳过安装，仅构建并刷新签名；请解锁手机后运行 --refresh 安装${NC}"
fi

# ====== 判断是否需要 flet 打包 ======
# flet 优先用本仓库 venv，其次回退到 PATH 中的 flet（可用 FLET_BIN 覆盖）
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -z "${FLET_BIN:-}" ]; then
    if [ -f "$SCRIPT_DIR/../.venv/bin/flet" ]; then
        FLET_CMD="$SCRIPT_DIR/../.venv/bin/flet"
    elif command -v flet >/dev/null 2>&1; then
        FLET_CMD="$(command -v flet)"
    else
        FLET_CMD=""
    fi
else
    FLET_CMD="$FLET_BIN"
fi
PYTHON_APP_REF="build/python-app/main.pyc"
FLUTTER_IOS_DIR="build/flutter/ios"
# flet 的 ios-simulator 与 ipa 会复用 build/flutter。用真机构建标记识别
# 外部执行过 ios-simulator 后留下的模拟器工程，避免直接拿它构建真机 IPA。
DEVICE_BUILD_STAMP="build/.flet-ipa-device-ready"

NEED_FLET_BUILD=0
if [ "$FORCE_REBUILD" = "1" ]; then
    NEED_FLET_BUILD=1
    REASON="--rebuild 强制重建"
elif [ "$FORCE_REFRESH" = "1" ]; then
    if [ ! -f "$DEVICE_BUILD_STAMP" ]; then
        NEED_FLET_BUILD=1
        REASON="缺少真机构建标记，不能安全复用产物"
    elif find build/ios-simulator -type d -name "*.app" -newer "$DEVICE_BUILD_STAMP" \
        -print -quit 2>/dev/null | grep -q .; then
        NEED_FLET_BUILD=1
        REASON="检测到更新的模拟器产物，需恢复真机构建环境"
    else
        NEED_FLET_BUILD=0
        REASON="--refresh 仅续签"
    fi
elif [ ! -d "$FLUTTER_IOS_DIR" ] || [ ! -f "$PYTHON_APP_REF" ]; then
    NEED_FLET_BUILD=1
    REASON="首次构建（缺少打包产物）"
elif [ ! -f "$DEVICE_BUILD_STAMP" ]; then
    NEED_FLET_BUILD=1
    REASON="缺少真机构建标记"
elif find build/ios-simulator -type d -name "*.app" -newer "$DEVICE_BUILD_STAMP" \
    -print -quit 2>/dev/null | grep -q .; then
    NEED_FLET_BUILD=1
    REASON="检测到更新的模拟器产物"
elif [ -n "$(find src -type f -newer "$DEVICE_BUILD_STAMP" 2>/dev/null | head -1)" ] || [ "pyproject.toml" -nt "$DEVICE_BUILD_STAMP" ]; then
    NEED_FLET_BUILD=1
    REASON="源码有更新"
else
    NEED_FLET_BUILD=0
    REASON="源码无变化，仅刷新签名"
fi

log_message "${YELLOW}🧭 决策: $REASON${NC}"

# ====== 1. Flet 打包 Python（如需要） ======
if [ "$NEED_FLET_BUILD" = "1" ]; then
    if [ ! -f "$FLET_CMD" ]; then
        log_message "${RED}❌ 未找到 flet 命令: $FLET_CMD${NC}"
        log_message "${YELLOW}请先安装 flet，或用 FLET_BIN=/path/to/flet 指定${NC}"
        exit 1
    fi

    if ! command -v flutter >/dev/null 2>&1; then
        log_message "${RED}❌ 未找到 flutter: $FLUTTER_ROOT/bin/flutter${NC}"
        exit 1
    fi
    if ! command -v pod >/dev/null 2>&1; then
        log_message "${RED}❌ 未找到 CocoaPods (pod)，请检查 /usr/local/bin 或 /opt/homebrew/bin${NC}"
        exit 1
    fi

    log_message "${YELLOW}🔨 打包 Python 应用并生成 Flutter 工程...${NC}"
    # 失败必须中止：继续往下会用旧的 build/flutter 产物打包出旧代码的应用
    FLET_BUILD_LOG="scripts/logs/flet_build_$(date '+%Y%m%d_%H%M%S').log"
    if ! $FLET_CMD build ipa --yes --no-rich-output >"$FLET_BUILD_LOG" 2>&1; then
        log_message "${RED}❌ flet build 失败，最后 120 行输出：${NC}"
        tail -120 "$FLET_BUILD_LOG"
        log_message "${YELLOW}📄 完整日志已保留: $FLET_BUILD_LOG${NC}"
        exit 1
    fi
    rm -f "$FLET_BUILD_LOG"
    touch "$DEVICE_BUILD_STAMP"
else
    log_message "${GREEN}⏭️ 跳过 flet 打包，复用已有 build/python-app 与 build/flutter${NC}"
fi

# ====== 2. 写入自动签名 exportOptions ======
log_message "${YELLOW}🔏 配置自动签名 (development / team 6CS69Y977H)...${NC}"

# 不删除缓存的描述文件来“强制续新”。只要 Xcode 已登录 Apple Account，下面的
# xcodebuild -allowProvisioningUpdates 会在 profile 缺失或过期时自动申请/下载。

# flet build 每次重新生成 Xcode 工程时会把 Runner 签名重置为 Manual（且不带 profile），
# 导致 "requires a provisioning profile" 构建失败；这里强制改回 Automatic
PBXPROJ="build/flutter/ios/Runner.xcodeproj/project.pbxproj"
if [ -f "$PBXPROJ" ] && grep -q "CODE_SIGN_STYLE = Manual;" "$PBXPROJ"; then
    sed -i '' 's/CODE_SIGN_STYLE = Manual;/CODE_SIGN_STYLE = Automatic;/' "$PBXPROJ"
    log_message "${GREEN}✅ 已将 Runner 签名修正为 Automatic${NC}"
fi

cat > build/flutter/ios/exportOptions.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key>
    <string>development</string>
    <key>teamID</key>
    <string>6CS69Y977H</string>
    <key>signingStyle</key>
    <string>automatic</string>
    <key>compileBitcode</key>
    <false/>
    <key>stripSwiftSymbols</key>
    <true/>
    <key>uploadSymbols</key>
    <false/>
</dict>
</plist>
PLIST

# ====== 3. Xcode 构建、签名并导出 IPA（每次都做，刷新签名） ======
XCODE_CONFIGURATION="Release"
if [ "$FAST_MODE" = "1" ]; then
    XCODE_CONFIGURATION="Debug"
    log_message "${YELLOW}⚡ 快速模式：使用 debug 增量构建${NC}"
fi

BUILD_RUN_ID=$(date '+%Y%m%d_%H%M%S')
ARCHIVE_PATH="$PROJECT_ROOT/build/flutter/build/ios/archive/CloudMusicPlayer_${BUILD_RUN_ID}.xcarchive"
EXPORT_PATH="$PROJECT_ROOT/build/flutter/build/ios/ipa/$BUILD_RUN_ID"
XCODE_BUILD_LOG="$PROJECT_ROOT/scripts/logs/xcode_build_${BUILD_RUN_ID}.log"

# archive 自身携带 -allowProvisioningUpdates，因此 profile 缺失或过期时会由
# 已登录 Apple Account 的 Xcode 自动申请，无需先执行一次重复的预构建。
log_message "${YELLOW}📦 使用 Xcode archive 构建并自动刷新签名 ($XCODE_CONFIGURATION)...${NC}"
if ! xcodebuild \
    -workspace "$PROJECT_ROOT/build/flutter/ios/Runner.xcworkspace" \
    -scheme Runner \
    -configuration "$XCODE_CONFIGURATION" \
    -destination 'generic/platform=iOS' \
    -archivePath "$ARCHIVE_PATH" \
    -allowProvisioningUpdates \
    -allowProvisioningDeviceRegistration \
    archive >"$XCODE_BUILD_LOG" 2>&1; then
    log_message "${RED}❌ Xcode archive 失败，最后 80 行输出：${NC}"
    tail -80 "$XCODE_BUILD_LOG"
    log_message "${YELLOW}📄 完整日志已保留: $XCODE_BUILD_LOG${NC}"
    log_message "${YELLOW}请确认 Xcode > Settings > Accounts 已登录 Apple Account${NC}"
    exit 1
fi

log_message "${YELLOW}📤 使用 Xcode 导出 development IPA...${NC}"
if ! xcodebuild \
    -exportArchive \
    -archivePath "$ARCHIVE_PATH" \
    -exportPath "$EXPORT_PATH" \
    -exportOptionsPlist "$PROJECT_ROOT/build/flutter/ios/exportOptions.plist" \
    -allowProvisioningUpdates >>"$XCODE_BUILD_LOG" 2>&1; then
    log_message "${RED}❌ Xcode 导出 IPA 失败，最后 80 行输出：${NC}"
    tail -80 "$XCODE_BUILD_LOG"
    log_message "${YELLOW}📄 完整日志已保留: $XCODE_BUILD_LOG${NC}"
    exit 1
fi
rm -f "$XCODE_BUILD_LOG"
log_message "${GREEN}✅ Xcode 构建、签名和 IPA 导出完成${NC}"

# ====== 4. 查找 IPA ======
IPA_PATH=$(find "$EXPORT_PATH" -name "*.ipa" -type f 2>/dev/null | head -1)

if [ -z "$IPA_PATH" ]; then
    log_message "${RED}❌ 构建失败，未找到 IPA 文件${NC}"
    exit 1
fi

log_message "${GREEN}✅ 构建成功: $IPA_PATH${NC}"

# ====== 5. 检查签名有效期 ======
check_signature() {
    local ipa="$1"
    local tmp
    tmp=$(mktemp -d)
    unzip -q -o "$ipa" "Payload/*.app/embedded.mobileprovision" -d "$tmp" 2>/dev/null
    local mp
    mp=$(find "$tmp" -name "embedded.mobileprovision" 2>/dev/null | head -1)
    if [ -z "$mp" ]; then
        rm -rf "$tmp"
        return
    fi
    local expiry
    expiry=$(security cms -D -i "$mp" 2>/dev/null | grep -A1 "ExpirationDate" | grep "<date>" | sed 's/.*<date>\(.*\)<\/date>.*/\1/')
    rm -rf "$tmp"
    [ -z "$expiry" ] && return

    local expiry_epoch now_epoch days_left
    expiry_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$expiry" "+%s" 2>/dev/null)
    now_epoch=$(date "+%s")
    days_left=$(( (expiry_epoch - now_epoch) / 86400 ))

    # 变量后紧跟全角括号时，部分 Bash/locale 组合会把非 ASCII 字符误判为
    # 变量名的一部分；始终用花括号明确变量边界。
    log_message "${YELLOW}⏳ 签名过期时间: ${expiry}（剩余 ${days_left} 天）${NC}"
    if [ "$days_left" -le 1 ]; then
        log_message "${RED}⚠️ 签名即将过期，请尽快重新运行本脚本续签${NC}"
    fi
}

check_signature "$IPA_PATH"

# ====== 6. 安装 ======
if [ "$DEVICE_STATE" = "unavailable" ]; then
    log_message "${RED}⏭️ 跳过安装：设备不可用。IPA 已生成并刷新签名${NC}"
    log_message "${YELLOW}解锁手机后运行: bash scripts/deploy_iso.sh --refresh${NC}"
    exit 0
fi

log_message "${YELLOW}📲 安装到 $DEVICE_NAME...${NC}"
xcrun devicectl device install app --device "$DEVICE_ID" "$IPA_PATH" -t 600 2>&1

log_message "${GREEN}🎉 完成！应用已安装到 $DEVICE_NAME${NC}"
log_message "${YELLOW}💡 提示: 免费签名 7 天过期，建议每周运行一次 bash scripts/deploy_iso.sh 续签${NC}"
