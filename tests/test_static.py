from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil

import pytest

from x99toolkit.buildprogress import BuildProgressTracker
from x99toolkit.cstates import _amitse_state, _nv_state
from x99toolkit.firmware import select_primary_intelsetup_record
from x99toolkit.optimized import (
    build_optimized_amitse_body,
    build_optimized_nv_body,
    inspect_optimized_settings,
    patch_primary_optimized,
)
from x99toolkit.overclock import (
    OC_AMITSE_CLASSIC_PATCHES,
    build_overclock_amitse_body,
    build_overclock_nv_body,
    inspect_overclock_compatibility,
    patch_primary_overclock,
)
from x99toolkit.pipeline import default_output


DATA = Path(os.environ.get("X99_TEST_DATA", "/nonexistent"))


def fixture(name: str) -> Path:
    path = DATA / name
    if not path.is_file():
        pytest.skip("reference ROM not supplied: " + name)
    return path


def _xd4_stock() -> Path:
    for name in (
        "BIOS-Dump-20260914-211935-X99XD4(2).ROM",
        "x99xd4-PANDL-stock.ROM",
    ):
        path = DATA / name
        if path.is_file():
            return path
    pytest.skip("XD4 physical/stock ROM not supplied")


def test_xd4_physical_dump_supports_performance_defaults():
    state = inspect_optimized_settings(_xd4_stock())
    assert state["supported"] is True
    assert state["layout"] == "classic-7c084"
    assert state["matches"] is False


def test_xd4_performance_profile_changes_all_three_copies(tmp_path):
    stock = _xd4_stock()
    primary = tmp_path / "primary.rom"
    shutil.copy2(stock, primary)
    primary_plan = patch_primary_optimized(primary)
    nv_plan = build_optimized_nv_body(stock.read_bytes(), tmp_path / "nv.bin")
    am_plan = build_optimized_amitse_body(stock.read_bytes(), tmp_path / "am.bin")
    assert len(primary_plan["changed_offsets"]) == 11
    assert len(nv_plan["changed_decompressed_offsets"]) == 11
    assert len(am_plan["changed_decompressed_offsets"]) == 17
    assert am_plan["layout"] == "classic-7c084"


def test_xd4_oc_reference_is_detected_exactly():
    stock = inspect_overclock_compatibility(_xd4_stock())
    reference = inspect_overclock_compatibility(fixture("x99xd4-OC_SB2023-pp0.rom"))
    assert stock["supported"] and not stock["matches"]
    assert reference["supported"] and reference["matches"]
    assert reference["primary"]["values"] == {
        "Current Limitation": 2048,
        "Overclocking Lock": 1,
        "Current Config": 1,
    }
    assert len(reference["access"]) == 8


def test_xd4_oc_mutations_reproduce_reference_payloads_exactly(tmp_path):
    stock = _xd4_stock()
    reference = fixture("x99xd4-OC_SB2023-pp0.rom")

    primary = tmp_path / "primary.rom"
    shutil.copy2(stock, primary)
    primary_plan = patch_primary_overclock(primary)
    current = primary.read_bytes()
    expected = reference.read_bytes()
    current_record = select_primary_intelsetup_record(current)
    expected_record = select_primary_intelsetup_record(expected)
    assert current[current_record.data_offset:current_record.data_offset + current_record.payload_size] == expected[expected_record.data_offset:expected_record.data_offset + expected_record.payload_size]
    assert primary_plan["changed_offsets"] == [0x13D3, 0x13D4, 0x13FD, 0x162D]

    nv_plan = build_overclock_nv_body(stock.read_bytes(), tmp_path / "nv.bin")
    ref_nv = _nv_state(reference.read_bytes())[0].decompressed
    assert nv_plan["expected_full_decompressed_sha256"] == sha256(ref_nv).hexdigest()

    am_plan = build_overclock_amitse_body(stock.read_bytes(), tmp_path / "am.bin")
    ref_am = _amitse_state(reference.read_bytes()).parsed.decompressed
    assert am_plan["expected_full_decompressed_sha256"] == sha256(ref_am).hexdigest()
    assert am_plan["changed_decompressed_offsets"] == [item["offset"] for item in OC_AMITSE_CLASSIC_PATCHES]


def test_output_name_and_progress_include_new_oc_feature():
    source = Path("board.ROM")
    assert default_output(source, apply_overclock=True).name == "board_OC.ROM"
    tracker = BuildProgressTracker({"apply_overclock": True})
    assert "Validate multiplier OC compatibility" in tracker.stages
    for stage in tracker.stages:
        tracker.update(stage, "PASS")
    assert tracker.current_percent == 100
