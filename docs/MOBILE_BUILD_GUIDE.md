# 📱 移动平台构建指南（Flet）

本文档介绍如何为 Cloud Music Player 构建移动平台应用。
当前项目基于 [Flet](https://flet.dev/) 0.86（Flutter 引擎），构建体系为 `flet build`。
（旧 Toga/BeeWare + Briefcase 流程已于 2026-08 废弃，见文末历史文档说明。）

## 🍎 iOS 平台

### 环境要求

- **操作系统**: macOS（Xcode 依赖）
- **Xcode**: 14.0+
- **CocoaPods**: 必须（缺失会导致 `flutter build ipa` 失败）`brew install cocoapods`
- **Flutter SDK**: 与 flet 版本对应（flet 0.86.5 → Flutter 3.44.x）
- **Python**: 3.10+
- **Apple 开发者账号**: 免费个人账号即可（签名 7 天有效，需定期续签）

### 一键部署（推荐）

```bash
bash scripts/deploy_iso.sh           # 自动：源码有更新则完整重建，否则仅刷新签名
bash scripts/deploy_iso.sh --rebuild # 强制完整重建（flet 打包 + flutter 签名）
bash scripts/deploy_iso.sh --refresh # 仅刷新签名（代码未变时用，速度快）
```

脚本自动完成：检测连接的 iPhone → 判断是否需要重新打包 → 写入自动签名配置 →
`xcodebuild archive` 构建签名 → Xcode 导出 IPA → 检查签名有效期 →
`devicectl` 安装到设备。

### Tarsier-AI 操作 Xcode GUI

需要验证 Xcode 桌面自动化时，可让 Tarsier-AI 读取 Accessibility 语义树并点击
Xcode 的 Run，由 Xcode 完成构建、签名、安装和启动：

```bash
uv tool install 'tarsier-ai==0.6.0' # 首次安装
cp .xcode/config.example.yaml .xcode/config.yaml
chmod 600 .xcode/config.yaml         # 填写 user/pass，仅本机可读
bash scripts/deploy_xcode_tarsier.sh
bash scripts/deploy_xcode_tarsier.sh --timeout 1200 # 可选：延长超时
```

运行前需在 Xcode 中选择目标 iPhone，并确保 Xcode Apple Account 已登录；运行脚本的
Tarsier Python 还需获得 macOS“辅助功能”权限。GUI 自动化要求用户桌面会话保持登录且
未锁屏，因此无人值守的定时续签仍推荐 `deploy_iso.sh`。本地凭据文件
`.xcode/config.yaml` 已加入 `.gitignore`；脚本要求权限为 `0600`，并且不会通过参数、
环境变量、剪贴板或日志传递密码。若 Apple 要求两步验证码，需在 Xcode 中人工完成。

### 手动构建

```bash
# 1. flet 打包 Python 并生成 Flutter 工程
#    （注意：src 布局需要 pyproject.toml 中 [tool.flet.app] path = "src"，
#     且 src/main.py 作为 app 入口）
flet build ipa --yes

# 2. 写入签名配置 build/flutter/ios/exportOptions.plist
cat > build/flutter/ios/exportOptions.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key><string>development</string>
    <key>teamID</key><string><你的TeamID></string>
    <key>signingStyle</key><string>automatic</string>
    <key>compileBitcode</key><false/>
    <key>stripSwiftSymbols</key><true/>
    <key>uploadSymbols</key><false/>
</dict>
</plist>
PLIST

# 3. 用 Flutter 构建签名 IPA（TeamID 在 pyproject.toml [tool.flet.ios] 中配置）
cd build/flutter
flutter build ipa --release --export-options-plist ios/exportOptions.plist

# 4. 安装到真机
xcrun devicectl list devices  # 找到设备 ID
xcrun devicectl device install app --device <DEVICE_ID> \
    build/flutter/build/ios/ipa/nextcloud_music_player.ipa
```

### ⚠️ 常见坑

- **`flet build ipa` 直接产物无法装真机**：没有 provisioning profile 时 flet 会走
  `--no-codesign`，产物未签名。必须执行上面第 2、3 步完成 development 签名。
- **免费账号签名 7 天过期**：到期后 App 无法启动，重跑 `deploy_iso.sh` 续签即可。
- **设备不可用（unavailable）**：手机锁屏/待机时 devicectl 无法安装，解锁后运行
  `bash scripts/deploy_iso.sh --refresh`。

## 🤖 Android 平台

```bash
flet build apk          # debug
flet build apk --release
```

权限已在 `pyproject.toml [tool.flet.android]` 中配置
（INTERNET / WAKE_LOCK / FOREGROUND_SERVICE）。

## 📚 历史文档（基于旧 Toga/BeeWare 框架，已过时）

以下文档记录的是迁移到 Flet 之前的排查与修复过程，仅作历史参考，
其中的 Briefcase/Toga 命令**不适用于当前框架**：

- [iOS_SIGNING_GUIDE.md](iOS_SIGNING_GUIDE.md)
- [iOS_BACKGROUND_PLAYBACK.md](iOS_BACKGROUND_PLAYBACK.md)
- [iOS_MUSIC_PERSISTENCE_FIX.md](iOS_MUSIC_PERSISTENCE_FIX.md)
- [iOS_COMPLETE_FIX.md](iOS_COMPLETE_FIX.md)
- [iOS_PROGRESS_FIX.md](iOS_PROGRESS_FIX.md)
- [ANDROID_BUILD_FIX.md](ANDROID_BUILD_FIX.md)
- [DEPENDENCY_FIX.md](DEPENDENCY_FIX.md)
