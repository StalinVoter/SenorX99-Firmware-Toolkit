from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--physical", type=Path, required=True)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--oc-reference", type=Path, required=True)
    args = parser.parse_args()

    physical = args.physical.resolve()
    stock = args.stock.resolve()
    reference = args.oc_reference.resolve()

    physical_opt = inspect_optimized_settings(physical)
    assert physical_opt["supported"] and physical_opt["layout"] == "classic-7c084"
    assert not inspect_overclock_compatibility(stock)["matches"]
    assert inspect_overclock_compatibility(reference)["matches"]

    with tempfile.TemporaryDirectory(prefix="X99-Gen2-v2.6.3-validation-") as temp_name:
        temp = Path(temp_name)
        primary = temp / "primary.rom"
        shutil.copy2(stock, primary)
        primary_plan = patch_primary_overclock(primary)
        current = primary.read_bytes()
        expected = reference.read_bytes()
        current_record = select_primary_intelsetup_record(current)
        expected_record = select_primary_intelsetup_record(expected)
        assert current[current_record.data_offset:current_record.data_offset + current_record.payload_size] == expected[expected_record.data_offset:expected_record.data_offset + expected_record.payload_size]
        assert primary_plan["changed_offsets"] == [0x13D3, 0x13D4, 0x13FD, 0x162D]

        nv_plan = build_overclock_nv_body(stock.read_bytes(), temp / "oc-nv.bin")
        assert nv_plan["expected_full_decompressed_sha256"] == sha256(_nv_state(reference.read_bytes())[0].decompressed).hexdigest()

        am_plan = build_overclock_amitse_body(stock.read_bytes(), temp / "oc-am.bin")
        assert am_plan["expected_full_decompressed_sha256"] == sha256(_amitse_state(reference.read_bytes()).parsed.decompressed).hexdigest()
        assert am_plan["changed_decompressed_offsets"] == [item["offset"] for item in OC_AMITSE_CLASSIC_PATCHES]

        optimized_primary = temp / "optimized-primary.rom"
        shutil.copy2(physical, optimized_primary)
        optimized_primary_plan = patch_primary_optimized(optimized_primary)
        optimized_nv_plan = build_optimized_nv_body(physical.read_bytes(), temp / "opt-nv.bin")
        optimized_am_plan = build_optimized_amitse_body(physical.read_bytes(), temp / "opt-am.bin")
        assert len(optimized_primary_plan["changed_offsets"]) == 11
        assert len(optimized_nv_plan["changed_decompressed_offsets"]) == 11
        assert len(optimized_am_plan["changed_decompressed_offsets"]) == 17

    print("Gen2-v2.6.3 XD4 binary validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
