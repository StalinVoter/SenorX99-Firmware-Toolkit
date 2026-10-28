from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .constants import (
    AMITSE_RAW_SECTION_TYPE,
    AMITSE_SETUP_DATA_GUID,
    NV_INTERNAL_GUID,
    NV_INTERNAL_RAW_SECTION_TYPE,
)
from .cstates import _amitse_state, _nv_state, _semantic_layout
from .families import classify_amitse_family
from .firmware import select_primary_intelsetup_record


# Exact AMIBCP/default changes recovered from the user's XD4 stock -> OC pair.
# Values are little-endian and relative to the 6124-byte IntelSetup payload.
OC_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "name": "Current Limitation",
        "offset": 0x13D3,
        "width": 2,
        "stock": 0x0438,
        "desired": 0x0800,
        "stock_label": "1080 (135 A)",
        "desired_label": "2048 (unlimited ICC on HEDT)",
    },
    {
        "name": "Overclocking Lock",
        "offset": 0x13FD,
        "width": 1,
        "stock": 0,
        "desired": 1,
        "stock_label": "Enable",
        "desired_label": "Disable",
    },
    {
        "name": "Current Config",
        "offset": 0x162D,
        "width": 1,
        "stock": 0,
        "desired": 1,
        "stock_label": "Disable",
        "desired_label": "Enable",
    },
)

# AMITSE access flags and Failsafe/Optimal defaults. These eight byte changes
# reproduce the controlled XD4 AMIBCP reference exactly.
OC_AMITSE_CLASSIC_PATCHES: tuple[dict[str, Any], ...] = (
    {"name": "OverClocking Feature access", "offset": 0x133BC, "stock": 0x01, "desired": 0x05},
    {"name": "XE Ratio Limit access", "offset": 0x5B0AC, "stock": 0x01, "desired": 0x05},
    {"name": "Overclocking Lock failsafe", "offset": 0x5B1D8, "stock": 0x00, "desired": 0x01},
    {"name": "Overclocking Lock optimal", "offset": 0x5B1D9, "stock": 0x00, "desired": 0x01},
    {"name": "PP0 current-control access", "offset": 0x5BF6C, "stock": 0x01, "desired": 0x05},
    {"name": "Current Config optimal", "offset": 0x5C6E9, "stock": 0x00, "desired": 0x01},
    {"name": "Current Limitation optimal low", "offset": 0x5C732, "stock": 0x38, "desired": 0x00},
    {"name": "Current Limitation optimal high", "offset": 0x5C733, "stock": 0x04, "desired": 0x08},
)


# Compact control identities were cross-checked with IntelRCSetup IFR question
# IDs, prompt/help tokens and IntelSetup offsets. These are not a uniform shift:
# the first menu moves by 0x40, while the later controls move by 0x88.
OC_AMITSE_COMPACT_PATCHES = tuple(
    {**item, "offset": offset}
    for item, offset in zip(OC_AMITSE_CLASSIC_PATCHES, (
        0x1337C, 0x5B024, 0x5B150, 0x5B151,
        0x5BEE4, 0x5C661, 0x5C6AA, 0x5C6AB,
    ))
)
OC_AMITSE_PATCHES = {
    "classic-7c084": OC_AMITSE_CLASSIC_PATCHES,
    "compact-7bffc": OC_AMITSE_COMPACT_PATCHES,
}

# Read-only control headers, excluding mutable access/default bytes. Confirm
# identity before any patch; matching the main AMITSE executable alone is not
# sufficient to assume these entries have remained in their expected positions.
COMPACT_OC_HEADERS = {
    0x5B12C: "0900000081120000221006006c5402000000000000000000000000000100010080120000",
    0x5C63C: "09000000201200006c100600985d0200000000000000000000000000010001001f120000",
    0x5C684: "09000000431200006d100600c15d020000000000f8ffffffffffffff0200020042120000",
}
COMPACT_OC_MENUS = {
    0x1337C: ("01002600c2000000", "7515000008000600a8010000"),
    0x5B024: ("0100ad00ae000000", "851200001e1006001c540200"),
    0x5BEE4: ("0100b200b5000000", "4813000051100600cc5a0200"),
}


def _patches_for_state(state) -> tuple[dict[str, Any], ...]:
    patches = OC_AMITSE_PATCHES.get(state.layout_name)
    if patches is None:
        raise ValueError("No verified OC mapping for AMITSE layout " + state.layout_name)
    if state.layout_name == "compact-7bffc":
        data = state.parsed.decompressed
        if len(data) != 0x7BFFC:
            raise ValueError("Compact OC setup-table size mismatch")
        for offset, expected_hex in COMPACT_OC_HEADERS.items():
            expected = bytes.fromhex(expected_hex)
            if data[offset:offset + len(expected)] != expected:
                raise ValueError(f"Compact OC control identity mismatch at 0x{offset:X}")
        for offset, (before, after) in COMPACT_OC_MENUS.items():
            if (data[offset - 8:offset] != bytes.fromhex(before)
                    or data[offset + 4:offset + 16] != bytes.fromhex(after)):
                raise ValueError(f"Compact OC menu identity mismatch at 0x{offset:X}")
    return patches


def _require_supported(rom: bytes) -> dict[str, Any]:
    family = classify_amitse_family(rom)
    expected_layout = {
        "xd3-classic": "classic-7c084",
        "xd3-compact": "compact-7bffc",
    }.get(family.get("board_family"))
    if expected_layout is None:
        raise ValueError("Unlocked-multiplier OC compatibility requires a verified classic or compact XD3/XD4 firmware family")
    state = _amitse_state(rom)
    if state.layout_name != expected_layout:
        raise ValueError("OC firmware family and AMITSE setup layout disagree")
    _patches_for_state(state)
    return family


def _read_settings(data: bytes, record) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in OC_SETTINGS:
        start = record.data_offset + item["offset"]
        result[item["name"]] = int.from_bytes(data[start:start + item["width"]], "little")
    return result


def _validate_settings(values: dict[str, int], context: str) -> None:
    unexpected = []
    for item in OC_SETTINGS:
        value = values[item["name"]]
        if value not in (item["stock"], item["desired"]):
            unexpected.append(
                f"{item['name']}={value} (expected {item['stock']} or {item['desired']})"
            )
    if unexpected:
        raise ValueError(context + " does not match the validated XD4 IntelSetup schema: " + "; ".join(unexpected))


def inspect_overclock_compatibility(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    result: dict[str, Any] = {
        "supported": False,
        "matches": False,
        "profile": "XD4 unlocked-multiplier OC compatibility",
        "settings": [],
        "access": [],
    }
    try:
        family = _require_supported(rom)
        primary = select_primary_intelsetup_record(rom)
        primary_values = _read_settings(rom, primary)
        _validate_settings(primary_values, "Primary IntelSetup")

        nv_parsed, nv_record, _unused, _target = _nv_state(rom)
        nv_values = _read_settings(nv_parsed.decompressed, nv_record)
        _validate_settings(nv_values, "NV-Internal IntelSetup")

        am_state = _amitse_state(rom)
        access = []
        for item in _patches_for_state(am_state):
            value = am_state.parsed.decompressed[item["offset"]]
            if value not in (item["stock"], item["desired"]):
                raise ValueError(
                    f"AMITSE OC byte 0x{item['offset']:X} has unexpected value {value}; "
                    f"expected {item['stock']} or {item['desired']}"
                )
            access.append({**item, "value": value, "matches": value == item["desired"]})

        settings = []
        for item in OC_SETTINGS:
            value = primary_values[item["name"]]
            settings.append({
                **item,
                "offset": f"0x{item['offset']:04X}",
                "value": value,
                "matches": value == item["desired"],
            })
        primary_matches = all(item["matches"] for item in settings)
        nv_matches = all(nv_values[item["name"]] == item["desired"] for item in OC_SETTINGS)
        am_matches = all(item["matches"] for item in access)
        result.update({
            "supported": True,
            "matches": primary_matches and nv_matches and am_matches,
            "family": family.get("family"),
            "layout": am_state.layout_name,
            "primary": {"matches": primary_matches, "values": primary_values},
            "nv_internal": {
                "matches": nv_matches,
                "values": nv_values,
                "decompressed_sha256": sha256(nv_parsed.decompressed).hexdigest(),
            },
            "amitse": {
                "matches": am_matches,
                "decompressed_sha256": sha256(am_state.parsed.decompressed).hexdigest(),
            },
            "settings": settings,
            "access": access,
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def patch_primary_overclock(path: Path) -> dict[str, Any]:
    buf = bytearray(path.read_bytes())
    _require_supported(bytes(buf))
    record = select_primary_intelsetup_record(bytes(buf))
    before = _read_settings(bytes(buf), record)
    _validate_settings(before, "Primary IntelSetup")
    changed: list[int] = []
    for item in OC_SETTINGS:
        start = record.data_offset + item["offset"]
        current = int.from_bytes(buf[start:start + item["width"]], "little")
        if current != item["desired"]:
            buf[start:start + item["width"]] = item["desired"].to_bytes(item["width"], "little")
            changed.extend(range(item["offset"], item["offset"] + item["width"]))
    path.write_bytes(buf)
    final = path.read_bytes()
    after_record = select_primary_intelsetup_record(final)
    after = _read_settings(final, after_record)
    if any(after[item["name"]] != item["desired"] for item in OC_SETTINGS):
        raise IOError("Primary OC-compatibility post-write verification failed")
    return {"before": before, "after": after, "changed_offsets": changed}


def build_overclock_nv_body(rom: bytes, output_body: Path) -> dict[str, Any]:
    _require_supported(rom)
    parsed, record, _unused, target = _nv_state(rom)
    before = _read_settings(parsed.decompressed, record)
    _validate_settings(before, "NV-Internal IntelSetup")
    patched = bytearray(parsed.decompressed)
    intended: set[int] = set()
    for item in OC_SETTINGS:
        start = record.data_offset + item["offset"]
        current = int.from_bytes(patched[start:start + item["width"]], "little")
        if current != item["desired"]:
            replacement = item["desired"].to_bytes(item["width"], "little")
            for index, value in enumerate(replacement):
                absolute = start + index
                if patched[absolute] != value:
                    intended.add(absolute)
                    patched[absolute] = value
    changed = [i for i, (old, new) in enumerate(zip(parsed.decompressed, patched)) if old != new]
    if set(changed) != intended:
        raise RuntimeError(f"NV OC changes escaped intended offsets: expected {sorted(intended)}, got {changed}")
    body = bytes(patched[target.offset + target.header_size:target.offset + target.size])
    output_body.write_bytes(body)
    _target, siblings = _semantic_layout(parsed.nested_sections, NV_INTERNAL_RAW_SECTION_TYPE)
    return {
        "guid": NV_INTERNAL_GUID,
        "section_type": NV_INTERNAL_RAW_SECTION_TYPE,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "before": before,
        "after": _read_settings(bytes(patched), record),
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
    }


def build_overclock_amitse_body(rom: bytes, output_body: Path) -> dict[str, Any]:
    _require_supported(rom)
    state = _amitse_state(rom)
    parsed = state.parsed
    target = state.target
    patched = bytearray(parsed.decompressed)
    intended: set[int] = set()
    before: dict[str, int] = {}
    for item in _patches_for_state(state):
        offset = item["offset"]
        value = patched[offset]
        before[f"0x{offset:X}"] = value
        if value not in (item["stock"], item["desired"]):
            raise ValueError(
                f"AMITSE OC byte 0x{offset:X} has unexpected value {value}; expected {item['stock']} or {item['desired']}"
            )
        if value != item["desired"]:
            patched[offset] = item["desired"]
            intended.add(offset)
    changed = [i for i, (old, new) in enumerate(zip(parsed.decompressed, patched)) if old != new]
    if set(changed) != intended:
        raise RuntimeError(f"AMITSE OC changes escaped intended offsets: expected {sorted(intended)}, got {changed}")
    body = bytes(patched[target.offset + target.header_size:target.offset + target.size])
    output_body.write_bytes(body)
    _target, siblings = _semantic_layout(parsed.nested_sections, AMITSE_RAW_SECTION_TYPE)
    return {
        "guid": AMITSE_SETUP_DATA_GUID,
        "section_type": AMITSE_RAW_SECTION_TYPE,
        "layout": state.layout_name,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "before": before,
        "after": {f"0x{item['offset']:X}": patched[item["offset"]] for item in _patches_for_state(state)},
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
    }
