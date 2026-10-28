from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .constants import (
    AMITSE_RAW_SECTION_TYPE,
    AMITSE_SETUP_DATA_GUID,
    NV_INTERNAL_GUID,
    NV_INTERNAL_RAW_SECTION_TYPE,
    V9S_AMITSE_OPT_PATCHES,
)
from .firmware import select_primary_intelsetup_record
from .cstates import _amitse_state, _nv_state, _semantic_layout
from .families import classify_amitse_family


# XD3 compact reference profile recovered from the user's exact stock ->
# AMIBCP5-optimized pair on 2026-08-23.
OPTIMIZED_SETTINGS: tuple[dict[str, Any], ...] = (
    {"name": "ACPI T-States", "offset": 0x00AB, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "SATA AHCI ALPM", "offset": 0x0166, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "sSATA AHCI ALPM", "offset": 0x01A2, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "Patrol Scrub", "offset": 0x11C3, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "Demand Scrub", "offset": 0x11C5, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "Memory Power Savings Mode", "offset": 0x1264, "stock": 5, "desired": 0, "stock_label": "Auto", "desired_label": "Disabled"},
    {"name": "MDLL Off", "offset": 0x1284, "stock": 2, "desired": 0, "stock_label": "Auto", "desired_label": "Disabled"},
    {"name": "Energy Performance Tuning", "offset": 0x13C0, "stock": 0, "desired": 1, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "Energy Performance BIAS", "offset": 0x162C, "stock": 7, "desired": 0, "stock_label": "Balanced Performance", "desired_label": "Performance"},
    {"name": "SATA AHCI LPM", "offset": 0x1719, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "sSATA AHCI LPM", "offset": 0x171A, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
)

# MACHINIST X99-V9S controlled reference pair supplied 2026-08-26.
# The first seven offsets are unchanged from XD3. Sequence-context alignment of
# the two IntelSetup schemas proves that the later V9S region is shifted by
# 0x21 bytes: 0x13C0 -> 0x139F, 0x162C -> 0x160B, 0x1719/1A -> 0x16F8/F9.
# Energy Performance Tuning is already Disable in the stock V9S reference, so
# stock == desired and AMIBCP correctly produced no delta for that setting.
V9S_OPTIMIZED_SETTINGS: tuple[dict[str, Any], ...] = (
    {"name": "ACPI T-States", "offset": 0x00AB, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "SATA AHCI ALPM", "offset": 0x0166, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "sSATA AHCI ALPM", "offset": 0x01A2, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "Patrol Scrub", "offset": 0x11C3, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "Demand Scrub", "offset": 0x11C5, "stock": 1, "desired": 0, "stock_label": "Enable", "desired_label": "Disable"},
    {"name": "Memory Power Savings Mode", "offset": 0x1264, "stock": 5, "desired": 0, "stock_label": "Auto", "desired_label": "Disabled"},
    {"name": "MDLL Off", "offset": 0x1284, "stock": 2, "desired": 0, "stock_label": "Auto", "desired_label": "Disabled"},
    {"name": "Energy Performance Tuning", "offset": 0x139F, "stock": 1, "desired": 1, "stock_label": "Disable", "desired_label": "Disable", "already_stock_optimal": True},
    {"name": "Energy Performance BIAS", "offset": 0x160B, "stock": 7, "desired": 0, "stock_label": "Balanced Performance", "desired_label": "Performance"},
    {"name": "SATA AHCI LPM", "offset": 0x16F8, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
    {"name": "sSATA AHCI LPM", "offset": 0x16F9, "stock": 1, "desired": 0, "stock_label": "Enabled", "desired_label": "Disabled"},
)

OPTIMIZED_BY_OFFSET = {item["offset"]: item for item in OPTIMIZED_SETTINGS}

# AMIBCP compact-7bffc AMITSE default-table deltas for the same 11 settings.
OPT_AMITSE_COMPACT_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x15A91, 1, 0),
    (0x15AD9, 1, 0),
    (0x17091, 1, 0),
    (0x170D9, 1, 0),
    (0x55610, 5, 0),
    (0x55611, 5, 0),
    (0x55658, 5, 0),
    (0x55659, 5, 0),
    (0x556E1, 2, 0),
    (0x563F8, 1, 0),
    (0x563F9, 1, 0),
    (0x56490, 1, 0),
    (0x56491, 1, 0),
    (0x5BC21, 1, 0),
    (0x5C0C9, 0, 1),
    (0x5C110, 7, 0),
    (0x5C111, 7, 0),
)

# The classic XD3/XD4 AMITSESetupData layout is the same validated table with
# a +0x88-byte displacement. Every source byte and record neighborhood was
# checked against the supplied physical XD4 dump and AMIBCP-edited XD4 pair.
OPT_AMITSE_CLASSIC_PATCHES: tuple[tuple[int, int, int], ...] = tuple(
    (offset + 0x88, stock, desired)
    for offset, stock, desired in OPT_AMITSE_COMPACT_PATCHES
)


def _profile_for_rom(rom: bytes) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    family = classify_amitse_family(rom)
    board = family.get("board_family")
    if board == "xd3-compact":
        return family, OPTIMIZED_SETTINGS
    if board == "x99-v9s":
        return family, V9S_OPTIMIZED_SETTINGS
    if board == "jginue-argb":
        raise ValueError("Jginue ARGB optimized defaults require a controlled AMIBCP reference")
    if board == "xd3-classic":
        return family, OPTIMIZED_SETTINGS
    raise ValueError("Optimized BIOS Settings are not validated for this firmware family")


def _amitse_patches_for_state(state) -> tuple[tuple[int, int, int], ...]:
    if state.layout_name == "compact-7bffc":
        return OPT_AMITSE_COMPACT_PATCHES
    if state.layout_name == "classic-7c084":
        return OPT_AMITSE_CLASSIC_PATCHES
    if state.layout_name in ("v9s-791a0", "qiyida-k9s-791a0"):
        return V9S_AMITSE_OPT_PATCHES
    raise ValueError(
        "Optimized BIOS Settings are not validated for AMITSESetupData layout " + state.layout_name
    )


def _primary_record(data: bytes):
    return select_primary_intelsetup_record(data)


def _setting_values_from_record(data: bytes, record, settings: tuple[dict[str, Any], ...]) -> dict[str, int]:
    return {
        item["name"]: data[record.data_offset + item["offset"]]
        for item in settings
    }


def _validate_primary_values(
    values: dict[str, int],
    context: str,
    settings: tuple[dict[str, Any], ...],
) -> None:
    unexpected = []
    for item in settings:
        value = values[item["name"]]
        if value not in (item["stock"], item["desired"]):
            unexpected.append(
                f"{item['name']}={value} (expected known stock {item['stock']} or optimized {item['desired']})"
            )
    if unexpected:
        raise ValueError(context + " does not match the validated IntelSetup schema: " + "; ".join(unexpected))


def _summary(values: dict[str, int], settings: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    result = []
    for item in settings:
        value = values[item["name"]]
        result.append({
            "name": item["name"],
            "offset": f"0x{item['offset']:04X}",
            "value": value,
            "desired": item["desired"],
            "matches": value == item["desired"],
            "desired_label": item["desired_label"],
            "already_stock_optimal": bool(item.get("already_stock_optimal")),
        })
    return result


def inspect_optimized_settings(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    result: dict[str, Any] = {
        "supported": False,
        "matches": False,
        "profile": "X99 performance defaults / validated AMIBCP5 family mapping",
        "settings": [],
    }
    try:
        family, settings_def = _profile_for_rom(rom)
        primary = _primary_record(rom)
        primary_values = _setting_values_from_record(rom, primary, settings_def)
        _validate_primary_values(primary_values, "Primary IntelSetup", settings_def)

        nv_parsed, nv_record, _unused, _target = _nv_state(rom)
        nv_values = _setting_values_from_record(nv_parsed.decompressed, nv_record, settings_def)
        _validate_primary_values(nv_values, "NV-Internal IntelSetup", settings_def)

        am_state = _amitse_state(rom)
        am_patches = _amitse_patches_for_state(am_state)
        am_values = []
        for absolute, stock, desired in am_patches:
            value = am_state.parsed.decompressed[absolute]
            if value not in (stock, desired):
                raise ValueError(
                    f"AMITSE optimized-default byte 0x{absolute:X} has unexpected value {value}; expected {stock} or {desired}"
                )
            am_values.append({
                "offset": f"0x{absolute:X}",
                "value": value,
                "stock": stock,
                "desired": desired,
                "matches": value == desired,
            })

        settings = _summary(primary_values, settings_def)
        primary_matches = all(item["matches"] for item in settings)
        nv_matches = all(
            nv_values[item["name"]] == item["desired"]
            for item in settings_def
        )
        am_matches = all(item["matches"] for item in am_values)
        result.update({
            "supported": True,
            "matches": primary_matches and nv_matches and am_matches,
            "family": family.get("board_family"),
            "layout": am_state.layout_name,
            "primary": {
                "record_offset": primary.nvar_offset,
                "data_offset": primary.data_offset,
                "payload_size": primary.payload_size,
                "matches": primary_matches,
                "values": primary_values,
            },
            "nv_internal": {
                "matches": nv_matches,
                "values": nv_values,
                "decompressed_sha256": sha256(nv_parsed.decompressed).hexdigest(),
            },
            "amitse": {
                "matches": am_matches,
                "values": am_values,
                "decompressed_sha256": sha256(am_state.parsed.decompressed).hexdigest(),
            },
            "settings": settings,
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def patch_primary_optimized(path: Path) -> dict[str, Any]:
    buf = bytearray(path.read_bytes())
    _family, settings_def = _profile_for_rom(bytes(buf))
    record = _primary_record(bytes(buf))
    before = _setting_values_from_record(bytes(buf), record, settings_def)
    _validate_primary_values(before, "Primary IntelSetup", settings_def)
    changed: list[int] = []
    for item in settings_def:
        absolute = record.data_offset + item["offset"]
        if buf[absolute] != item["desired"]:
            buf[absolute] = item["desired"]
            changed.append(item["offset"])
    path.write_bytes(buf)
    final = path.read_bytes()
    final_record = _primary_record(final)
    after = _setting_values_from_record(final, final_record, settings_def)
    if any(after[item["name"]] != item["desired"] for item in settings_def):
        raise IOError("Primary optimized-settings post-write verification failed")
    return {
        "record_offset": record.nvar_offset,
        "data_offset": record.data_offset,
        "payload_size": record.payload_size,
        "before": before,
        "after": after,
        "changed_offsets": changed,
    }


def build_optimized_nv_body(rom: bytes, output_body: Path) -> dict[str, Any]:
    _family, settings_def = _profile_for_rom(rom)
    parsed, record, _unused, target = _nv_state(rom)
    before = _setting_values_from_record(parsed.decompressed, record, settings_def)
    _validate_primary_values(before, "NV-Internal IntelSetup", settings_def)
    patched = bytearray(parsed.decompressed)
    intended: set[int] = set()
    for item in settings_def:
        absolute = record.data_offset + item["offset"]
        if patched[absolute] != item["desired"]:
            intended.add(absolute)
            patched[absolute] = item["desired"]
    changed = [i for i, (old, new) in enumerate(zip(parsed.decompressed, patched)) if old != new]
    if set(changed) != intended:
        raise RuntimeError(
            f"NV optimized-settings changed unexpected offsets: expected {sorted(intended)}, got {changed}"
        )
    body_start = target.offset + target.header_size
    body_end = target.offset + target.size
    body = bytes(patched[body_start:body_end])
    output_body.write_bytes(body)
    after = _setting_values_from_record(bytes(patched), record, settings_def)
    _target, siblings = _semantic_layout(parsed.nested_sections, NV_INTERNAL_RAW_SECTION_TYPE)
    return {
        "guid": NV_INTERNAL_GUID,
        "section_type": NV_INTERNAL_RAW_SECTION_TYPE,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "before": before,
        "after": after,
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
        "body_size": len(body),
    }


def build_optimized_amitse_body(rom: bytes, output_body: Path) -> dict[str, Any]:
    _profile_for_rom(rom)  # explicit fail-closed family preflight
    state = _amitse_state(rom)
    patches = _amitse_patches_for_state(state)
    parsed = state.parsed
    target = state.target
    patched = bytearray(parsed.decompressed)
    before: dict[str, int] = {}
    intended: set[int] = set()
    for absolute, stock, desired in patches:
        value = patched[absolute]
        before[f"0x{absolute:X}"] = value
        if value not in (stock, desired):
            raise ValueError(
                f"AMITSE optimized-default byte 0x{absolute:X} has unexpected value {value}; expected {stock} or {desired}"
            )
        if value != desired:
            patched[absolute] = desired
            intended.add(absolute)
    changed = [i for i, (old, new) in enumerate(zip(parsed.decompressed, patched)) if old != new]
    if set(changed) != intended:
        raise RuntimeError(
            f"AMITSE optimized-settings changed unexpected offsets: expected {sorted(intended)}, got {changed}"
        )
    body_start = target.offset + target.header_size
    body_end = target.offset + target.size
    body = bytes(patched[body_start:body_end])
    output_body.write_bytes(body)
    _target, siblings = _semantic_layout(parsed.nested_sections, AMITSE_RAW_SECTION_TYPE)
    after = {f"0x{absolute:X}": patched[absolute] for absolute, _stock, _desired in patches}
    return {
        "guid": AMITSE_SETUP_DATA_GUID,
        "section_type": AMITSE_RAW_SECTION_TYPE,
        "layout": state.layout_name,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "before": before,
        "after": after,
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
        "body_size": len(body),
    }
