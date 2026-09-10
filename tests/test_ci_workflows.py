from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_ios_e2e_keeps_swift_package_manager_enabled():
    workflow = (REPOSITORY_ROOT / ".github/workflows/e2e.yml").read_text()

    assert "flet test ios --device-id" in workflow
    assert "--no-swift-package-manager" not in workflow


def test_ios_e2e_is_sharded_to_stay_within_job_timeout():
    workflow = (REPOSITORY_ROOT / ".github/workflows/e2e.yml").read_text()

    assert "shard: [gdrive, nextcloud, smb, integration]" in workflow
    assert '-k "${{ matrix.shard }}"' in workflow
    assert "flet-e2e-${{ github.run_id }}-${{ matrix.shard }}" in workflow


def test_flet_device_failures_do_not_override_unit_test_gate():
    workflow = (REPOSITORY_ROOT / ".github/workflows/e2e.yml").read_text()

    job = workflow.split("  flet-e2e:", 1)[1]
    assert "    needs: unit" in job
    assert "    continue-on-error: true" in job
