#!/usr/bin/env python3
"""Use Tarsier-AI to ask Xcode to build, install, and launch the iOS app."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from tarsier import Desktop


FAILURE_MARKERS = (
    "build failed",
    "failed to build",
    "no accounts",
    "no profiles for",
    "requires a provisioning profile",
    "signing for",
)

# CoreDevice terminology differs across Xcode releases. Older versions commonly
# report an unlocked device as "available"; Xcode 26 may report "connected".
USABLE_DEVICE_STATES = {"available", "connected"}


def is_usable_device_state(state: str) -> bool:
    """Accept CoreDevice suffixes such as 'available (paired)'."""
    primary_state = state.strip().lower().split(maxsplit=1)[0]
    return primary_state in USABLE_DEVICE_STATES


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str

    def redact(self, text: str) -> str:
        result = text
        for secret in (self.password, self.user):
            if secret:
                result = result.replace(secret, "<redacted>")
        return result


@dataclass(frozen=True)
class IOSDevice:
    name: str
    identifier: str
    state: str
    model: str


def physical_ios_devices() -> list[IOSDevice]:
    """Return physical iPhones reported by CoreDevice; simulators are excluded."""
    result = subprocess.run(
        ["xcrun", "devicectl", "list", "devices"],
        check=True,
        capture_output=True,
        text=True,
    )
    devices: list[IOSDevice] = []
    uuid_pattern = re.compile(
        r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
        r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
    )
    for line in result.stdout.splitlines():
        columns = re.split(r"\s{2,}", line.strip())
        if len(columns) < 5 or not uuid_pattern.fullmatch(columns[2]):
            continue
        name, _hostname, identifier, state, model = columns[:5]
        if "iphone" in model.lower():
            devices.append(IOSDevice(name, identifier, state.lower(), model))
    return devices


def choose_physical_device(requested_id: str | None) -> IOSDevice:
    devices = physical_ios_devices()
    if requested_id:
        matches = [device for device in devices if device.identifier.lower() == requested_id.lower()]
        if not matches:
            raise RuntimeError(f"physical iPhone not found: {requested_id}")
        device = matches[0]
    else:
        available = [device for device in devices if is_usable_device_state(device.state)]
        if not available:
            states = ", ".join(f"{d.name}={d.state}" for d in devices) or "none found"
            raise RuntimeError(f"no available physical iPhone ({states})")
        device = available[0]

    if not is_usable_device_state(device.state):
        raise RuntimeError(
            f"physical iPhone is not available: {device.name} ({device.state}); "
            "unlock and reconnect it"
        )
    return device


def visible_step(message: str, delay: float) -> None:
    print(f"STEP: {message}", flush=True)
    if delay > 0:
        time.sleep(delay)


def select_xcode_destination(device: IOSDevice, step_delay: float) -> None:
    """Select the named physical device from Product > Destination using AX menus."""
    import atomacos

    visible_step("打开 Xcode 的 Product 菜单", step_delay)
    app = atomacos.getAppRefByBundleId("com.apple.dt.Xcode")
    product = next(
        item for item in app.AXMenuBar.AXChildren
        if getattr(item, "AXTitle", "") == "Product"
    )
    product.Press()
    time.sleep(max(0.3, step_delay))
    visible_step("展开 Product → Destination", step_delay)
    destination = next(
        item for item in product.AXChildren[0].AXChildren
        if getattr(item, "AXTitle", "") == "Destination"
    )
    destination.Press()
    time.sleep(max(0.3, step_delay))
    destination_menu = destination.AXChildren[0]
    candidates = [
        item for item in destination_menu.AXChildren
        if getattr(item, "AXRole", "") == "AXMenuItem"
        and (
            getattr(item, "AXTitle", "") == device.name
            or getattr(item, "AXTitle", "").startswith(f"{device.name} (")
        )
    ]
    if not candidates:
        destination.Cancel()
        raise RuntimeError(
            f"Xcode does not list physical destination: {device.name}; "
            "unlock the phone and confirm Trust This Computer"
        )
    visible_step(f"选择物理设备：{device.name}", step_delay)
    candidates[0].Press()
    time.sleep(max(1, step_delay))

    # Reopen the semantic menu and require a checkmark on the physical device.
    visible_step("重新打开 Destination，检查物理设备勾选状态", step_delay)
    product.Press()
    time.sleep(0.2)
    destination = next(
        item for item in product.AXChildren[0].AXChildren
        if getattr(item, "AXTitle", "") == "Destination"
    )
    destination.Press()
    time.sleep(0.2)
    selected = [
        item for item in destination.AXChildren[0].AXChildren
        if (
            getattr(item, "AXTitle", "") == device.name
            or getattr(item, "AXTitle", "").startswith(f"{device.name} (")
        )
        and bool(getattr(item, "AXMenuItemMarkChar", ""))
    ]
    destination.Cancel()
    if not selected:
        raise RuntimeError(f"Xcode did not select physical destination: {device.name}")


def load_credentials(config_path: Path) -> Credentials:
    """Load local credentials without placing them in argv, env, or logs."""
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Xcode account needs login; create {config_path} from "
            "docs/xcode-config.example.yaml and run chmod 600 on it"
        ) from exc

    if mode & 0o077:
        raise RuntimeError(
            f"Refusing insecure credential file {config_path}; run chmod 600 on it"
        )

    data: dict[str, str] = {}
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
        for line_number, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if raw_line[:1].isspace() or ":" not in raw_line:
                raise ValueError(f"unsupported YAML syntax on line {line_number}")
            key, raw_value = raw_line.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key not in ("user", "pass") or key in data:
                raise ValueError(f"unexpected or duplicate key on line {line_number}")
            if value[:1] in ('"', "'"):
                parsed = ast.literal_eval(value)
                if not isinstance(parsed, str):
                    raise ValueError(f"value must be a string on line {line_number}")
                value = parsed
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            data[key] = value
    except Exception as exc:
        raise RuntimeError(f"Unable to parse credential config {config_path}") from exc

    user = data.get("user")
    password = data.get("pass")
    if not isinstance(user, str) or not user.strip():
        raise RuntimeError(f"Missing non-empty 'user' in {config_path}")
    if not isinstance(password, str) or not password:
        raise RuntimeError(f"Missing non-empty 'pass' in {config_path}")
    if len(password) < 8:
        raise RuntimeError(
            f"The 'pass' value in {config_path} is too short to be an Apple Account password"
        )
    return Credentials(user=user.strip(), password=password)


def secure_type(text: str) -> None:
    """Inject Unicode keystrokes without exposing secrets through the clipboard."""
    import Quartz

    event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    Quartz.CGEventKeyboardSetUnicodeString(event, len(text), text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.1)


def focused_xcode_field_role() -> tuple[str, str]:
    """Return the focused AX role/subrole without reading its sensitive value."""
    import atomacos

    app = atomacos.getAppRefByBundleId("com.apple.dt.Xcode")
    element = app.AXFocusedUIElement
    return (
        getattr(element, "AXRole", "") or "",
        getattr(element, "AXSubrole", "") or "",
    )


def open_accounts_window(desktop: Desktop):
    """Open Xcode Settings using the atomacos backend bundled with Tarsier."""
    import atomacos

    try:
        return desktop.get_window("Apple Accounts")
    except Exception:
        pass

    app = atomacos.getAppRefByBundleId("com.apple.dt.Xcode")
    xcode_menu = next(
        item for item in app.AXMenuBar.AXChildren
        if getattr(item, "AXTitle", "") == "Xcode"
    )
    xcode_menu.Press()
    time.sleep(0.3)
    menu = xcode_menu.AXChildren[0]
    settings_item = next(
        item for item in menu.AXChildren
        if getattr(item, "AXTitle", "") in ("Settings…", "设置…")
    )
    settings_item.Press()
    return desktop.wait_for_window(
        regex_name=r".*(Apple Accounts|Accounts|Settings|账户|设置).*", timeout=15
    )


def ensure_xcode_account(
    desktop: Desktop,
    project_window,
    config_path: Path,
    step_delay: float,
) -> Credentials | None:
    """Log in only when Xcode explicitly shows that no Apple Account is configured."""
    accounts = open_accounts_window(desktop)
    accounts.focus()
    text = ui_text(accounts)
    needs_login = any(
        marker in text.lower()
        for marker in ("sign-in to an apple account", "sign in to an apple account")
    )
    if not needs_login:
        accounts.close()
        project_window.focus()
        print("Xcode Apple Account is already configured.", flush=True)
        return None

    credentials = load_credentials(config_path)
    print(f"Xcode needs account login; loading credentials from {config_path}.", flush=True)

    # The sign-in WebView does not expose named fields, but keyboard focus is stable.
    # Use semantic buttons where available, then inject text without using clipboard.
    raw_root = accounts._backend._control
    buttons = []
    pending = [raw_root]
    while pending:
        element = pending.pop()
        if getattr(element, "AXRole", "") == "AXButton" and getattr(element, "AXEnabled", True):
            buttons.append(element)
        pending.extend(getattr(element, "AXChildren", None) or [])
    if not buttons:
        raise RuntimeError("Xcode sign-in button was not exposed through Accessibility")
    # Ignore window chrome, scrollbars, and navigation buttons. The content sign-in
    # control is the largest enabled button in the Accounts pane.
    def button_area(button) -> float:
        size = getattr(button, "AXSize", (0, 0))
        return float(size[0]) * float(size[1])

    sign_in_button = max(buttons, key=button_area)
    visible_step("点击 Apple Account 登录按钮", step_delay)
    sign_in_button.Press()
    time.sleep(max(2, step_delay))

    visible_step("填写 Apple Account 用户名（内容不会输出）", step_delay)
    secure_type(credentials.user)
    desktop.hotkey("{enter}")
    password_deadline = time.monotonic() + 30
    while time.monotonic() < password_deadline:
        _role, subrole = focused_xcode_field_role()
        if subrole == "AXSecureTextField":
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Xcode did not advance from username to the secure password field")

    visible_step("填写 Apple Account 密码（内容不会输出）", step_delay)
    secure_type(credentials.password)
    desktop.hotkey("{enter}")
    visible_step("等待 Xcode 完成账户登录", step_delay)
    login_deadline = time.monotonic() + 30
    while time.monotonic() < login_deadline:
        _role, subrole = focused_xcode_field_role()
        if subrole != "AXSecureTextField":
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(
            "Xcode remained on the password field; login was not accepted. "
            "Check ~/.xcode/config.yaml before retrying"
        )

    try:
        accounts = desktop.get_window("Apple Accounts")
    except Exception:
        pass
    login_text = credentials.redact(ui_text(accounts))
    if any(marker in login_text.lower() for marker in ("verification code", "two-factor", "验证码")):
        raise RuntimeError("Apple two-factor verification is required; complete it in Xcode")
    if "sign-in to an apple account" in login_text.lower():
        raise RuntimeError("Xcode account login did not complete; check Xcode for details")

    accounts.close()
    project_window.focus()
    print("Xcode Apple Account login completed.", flush=True)
    return credentials


def ui_text(window) -> str:
    """Collect exposed AX text without using screenshots or fixed coordinates."""
    root = window._backend._control
    pending = [root]
    seen: set[int] = set()
    values: list[str] = []

    while pending:
        element = pending.pop()
        identity = id(element)
        if identity in seen:
            continue
        seen.add(identity)

        for attribute in ("AXTitle", "AXDescription", "AXValue", "AXHelp"):
            try:
                value = getattr(element, attribute, None)
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                values.append(value.strip())

        try:
            pending.extend(getattr(element, "AXChildren", None) or [])
        except Exception:
            pass

    return "\n".join(values)


def wait_for_xcode_window(desktop: Desktop, timeout: int):
    # Xcode 26 may title the window "Runner.xcodeproj" even when its semantic
    # document root is Runner.xcworkspace. Validate the AX document, not the title.
    window = desktop.wait_for_window(regex_name=r".*Runner.*", timeout=timeout)
    try:
        window.find(role="splitgroup", name="Runner.xcworkspace")
    except Exception as exc:
        raise RuntimeError(
            f"Xcode window is not backed by Runner.xcworkspace: {window.name}"
        ) from exc
    return window


def deploy(
    workspace: Path,
    timeout: int,
    config_path: Path,
    check_account: bool,
    requested_device_id: str | None,
    step_delay: float,
) -> int:
    visible_step("通过 CoreDevice 检查物理 iPhone", step_delay)
    try:
        device = choose_physical_device(requested_device_id)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Unable to prepare physical iPhone: {exc}", file=sys.stderr)
        return 2
    print(
        f"Physical iPhone found: {device.name} ({device.identifier}, {device.model})",
        flush=True,
    )

    visible_step(f"用 Xcode 打开 workspace：{workspace.name}", step_delay)
    subprocess.run(["open", "-a", "Xcode", str(workspace)], check=True)

    desktop = Desktop(highlight_actions=True)
    window = wait_for_xcode_window(desktop, min(timeout, 60))
    window.focus()
    print(f"Tarsier connected to Xcode window: {window.name}", flush=True)

    try:
        select_xcode_destination(device, step_delay)
    except (RuntimeError, StopIteration, AttributeError) as exc:
        print(f"Unable to select physical Xcode destination: {exc}", file=sys.stderr)
        return 2
    window.focus()
    print(f"Xcode physical destination selected: {device.name}", flush=True)

    credentials = None
    if check_account:
        visible_step("打开 Xcode Apple Accounts 并检查登录状态", step_delay)
        try:
            credentials = ensure_xcode_account(desktop, window, config_path, step_delay)
        except RuntimeError as exc:
            print(f"Unable to prepare Xcode account: {exc}", file=sys.stderr)
            return 2

        # Xcode may replace/close the project window while account state changes.
        # Reopen the workspace and reacquire fresh Accessibility references.
        visible_step("账户检查完成，重新打开 workspace 并刷新窗口引用", step_delay)
        subprocess.run(["open", "-a", "Xcode", str(workspace)], check=True)
        try:
            window = wait_for_xcode_window(desktop, min(timeout, 60))
            window.focus()
            select_xcode_destination(device, step_delay)
        except (RuntimeError, TimeoutError, StopIteration, AttributeError) as exc:
            print(f"Unable to restore Xcode workspace after account check: {exc}", file=sys.stderr)
            return 2

    try:
        run_button = window.find(role="button", name="Run")
        stop_button = window.find(role="button", name="Stop")
    except Exception as exc:
        print(f"Unable to find Xcode Run/Stop controls: {exc}", file=sys.stderr)
        print(window.to_yaml_snapshot(max_depth=12), file=sys.stderr)
        return 2

    if not run_button.is_enabled:
        print("Xcode Run button is disabled; check scheme and run destination.", file=sys.stderr)
        return 2

    if stop_button.is_enabled:
        print("Tarsier stopping the existing Xcode session…", flush=True)
        stop_button.click()
        stop_deadline = time.monotonic() + 30
        while stop_button.is_enabled and time.monotonic() < stop_deadline:
            time.sleep(0.5)
        if stop_button.is_enabled:
            print("Existing Xcode session did not stop.", file=sys.stderr)
            return 2

    visible_step("即将点击 Xcode Run，开始真机构建和部署", step_delay)
    run_button.click()

    deadline = time.monotonic() + timeout
    session_started_at: float | None = None
    build_observed = False
    last_status = ""

    while time.monotonic() < deadline:
        time.sleep(1)
        text = ui_text(window)
        lowered = text.lower()
        is_building = "building" in lowered
        build_observed = build_observed or is_building

        for marker in FAILURE_MARKERS:
            if marker in lowered:
                print(f"Xcode reported an error containing: {marker}", file=sys.stderr)
                output = credentials.redact(text) if credentials else text
                print(output[-4000:], file=sys.stderr)
                return 1

        status_lines = [
            line for line in text.splitlines()
            if any(word in line.lower() for word in ("build", "install", "launch", "running"))
        ]
        if status_lines:
            status = status_lines[-1]
            if status != last_status:
                print(f"Xcode: {status}", flush=True)
                last_status = status

        if stop_button.is_enabled and build_observed and not is_building:
            if session_started_at is None:
                session_started_at = time.monotonic()
            elif time.monotonic() - session_started_at >= 5:
                print("Xcode build/deploy succeeded; app debug session is running.", flush=True)
                return 0
        else:
            session_started_at = None

    print(f"Timed out after {timeout}s waiting for Xcode deployment.", file=sys.stderr)
    output = ui_text(window)
    if credentials:
        output = credentials.redact(output)
    print(output[-4000:], file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and deploy the iOS app by controlling Xcode with Tarsier-AI."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--device-id",
        help="physical iPhone CoreDevice identifier; defaults to the first available iPhone",
    )
    parser.add_argument(
        "--step-delay", type=float, default=2.0,
        help="seconds to pause before visible GUI steps (default: 2.0)",
    )
    parser.add_argument(
        "--config", type=Path, default=Path("~/.xcode/config.yaml"),
        help="local credential file (default: ~/.xcode/config.yaml)",
    )
    parser.add_argument(
        "--skip-account-check", action="store_true",
        help="do not open Xcode Accounts before deployment",
    )
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not workspace.exists():
        parser.error(f"workspace does not exist: {workspace}")
    config_path = args.config.expanduser().resolve()
    if args.step_delay < 0:
        parser.error("--step-delay must be zero or greater")
    return deploy(
        workspace,
        args.timeout,
        config_path,
        not args.skip_account_check,
        args.device_id,
        args.step_delay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
