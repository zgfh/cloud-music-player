from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_ios_e2e_keeps_swift_package_manager_enabled():
    workflow = (REPOSITORY_ROOT / ".github/workflows/e2e.yml").read_text()

    assert "flet test ios --device-id" in workflow
    assert "--no-swift-package-manager" not in workflow
