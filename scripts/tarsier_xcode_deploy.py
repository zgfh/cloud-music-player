#!/usr/bin/env python3
"""Use Tarsier-AI to ask Xcode to build, install, and launch the iOS app."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
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
    return Credentials(user=user.strip(), password=password)


def secure_type(text: str) -> None:
    """Inject Unicode keystrokes without exposing secrets through the clipboard."""
    import Quartz

    event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    Quartz.CGEventKeyboardSetUnicodeString(event, len(text), text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.1)


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


def ensure_xcode_account(desktop: Desktop, project_window, config_path: Path) -> Credentials | None:
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
    buttons[-1].Press()
    time.sleep(2)

    secure_type(credentials.user)
    desktop.hotkey("{enter}")
    time.sleep(2)
    secure_type(credentials.password)
    desktop.hotkey("{enter}")
    time.sleep(4)

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
    return desktop.wait_for_window(regex_name=r".*Runner.*", timeout=timeout)


def deploy(workspace: Path, timeout: int, config_path: Path, check_account: bool) -> int:
    subprocess.run(["open", "-a", "Xcode", str(workspace)], check=True)

    desktop = Desktop(highlight_actions=True)
    window = wait_for_xcode_window(desktop, min(timeout, 60))
    window.focus()
    print(f"Tarsier connected to Xcode window: {window.name}", flush=True)

    credentials = None
    if check_account:
        try:
            credentials = ensure_xcode_account(desktop, window, config_path)
        except RuntimeError as exc:
            print(f"Unable to prepare Xcode account: {exc}", file=sys.stderr)
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

    print("Tarsier clicking Xcode Run…", flush=True)
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
    return deploy(workspace, args.timeout, config_path, not args.skip_account_check)


if __name__ == "__main__":
    raise SystemExit(main())
