from __future__ import annotations

from pathlib import Path

import pytest

from signal_pipeline.step2 import (
    load_step2_config,
    status_step2,
    verify_step1_inputs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEP1_ROOT = PROJECT_ROOT / "artifacts" / "step1"


def test_step2_config_is_frozen():
    config = load_step2_config(PROJECT_ROOT / "configs" / "step2.yaml")
    assert config.fee_bps == 10
    assert config.initial_position == 0
    assert config.long_only is True
    assert config.execution == "same_bar_open_to_close"
    assert config.splits_independent is True
    assert config.bundle_schema_version == "verifiable-signal-bundle-v1"
    assert config.step1_root == STEP1_ROOT
    assert config.bundle_dir == PROJECT_ROOT / "artifacts" / "step2" / "bundle"
    assert len(config.config_sha256) == 64


@pytest.mark.skipif(
    not (STEP1_ROOT / "artifact_hashes.json").is_file(),
    reason="requires the locally generated Step 1 artifacts",
)
def test_step1_inputs_pass_independent_verification():
    config = load_step2_config(PROJECT_ROOT / "configs" / "step2.yaml")
    verified = verify_step1_inputs(config)
    assert verified.source.step1_code_commit == "183facb43dc83f94f7f6ca4830499a3662ab4c25"
    assert verified.source.tau == "0.001923089035"
    assert (
        verified.source.model_revision
        == "a971ba21945c4f1796b17a91fe69214b5f4ad472"
    )
    assert verified.freeze["passed"] is True
    assert len(verified.freeze["files"]) >= 10
    assert verified.validation_report["passed"] is True


def test_status_is_read_only_when_absent():
    config = load_step2_config(PROJECT_ROOT / "configs" / "step2.yaml")
    if (PROJECT_ROOT / "artifacts" / "step2").exists():
        pytest.skip("formal Step 2 output already exists")
    result = status_step2(config)
    assert result["step2_completed"] is False
    assert result["bundle_frozen"] is False
