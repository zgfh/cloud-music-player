#!/usr/bin/env python3
"""Use Tarsier-AI to ask Xcode to build, install, and launch the iOS app."""

from __future__ import annotations

import argparse
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


def deploy(workspace: Path, timeout: int) -> int:
    subprocess.run(["open", "-a", "Xcode", str(workspace)], check=True)

    desktop = Desktop(highlight_actions=True)
    window = wait_for_xcode_window(desktop, min(timeout, 60))
    window.focus()
    print(f"Tarsier connected to Xcode window: {window.name}", flush=True)

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
                print(text[-4000:], file=sys.stderr)
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
    print(ui_text(window)[-4000:], file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and deploy the iOS app by controlling Xcode with Tarsier-AI."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not workspace.exists():
        parser.error(f"workspace does not exist: {workspace}")
    return deploy(workspace, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
