from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import lzma
import uuid

from .constants import (
    AMITSE_CSTATE_LAYOUTS,
    AMITSE_RECORD_SIZE,
    AMITSE_RECORD_TAIL_MARKER,
    AMITSE_RECORD_TRAILER_A,
    AMITSE_RECORD_TRAILER_B,
    AMITSE_RAW_SECTION_TYPE,
    AMITSE_SETUP_DATA_GUID,
    CSTATE_TARGETS,
    INTELSETUP_EXPECTED_PAYLOAD_SIZE,
    LZMA_CUSTOM_DECOMPRESS_GUID,
    V9S_AMITSE_DECOMPRESSED_SIZE,
    V9S_AMITSE_NORMALIZED_SHA256,
    K9S_AMITSE_NORMALIZED_SHA256,
    V9S_AMITSE_CSTATE_PATCHES,
    V9S_AMITSE_OPT_PATCHES,
    NV_INTERNAL_GUID,
    NV_INTERNAL_RAW_SECTION_TYPE,
)
from .firmware import (
    FfsFile,
    find_ffs_by_guid,
    find_intelsetup_records,
    inspect_primary_cstates,
    select_intelsetup_record,
)
from .families import classify_amitse_family


@dataclass(frozen=True)
class NestedSection:
    offset: int
    size: int
    section_type: int
    common_header_size: int
    header_size: int
    header_extension: bytes
    body: bytes
    padding: bytes


@dataclass(frozen=True)
class GuidedModule:
    ffs: FfsFile
    section_size: int
    section_type: int
    section_guid: str
    data_offset: int
    attributes: int
    compressed_payload: bytes
    decompressed: bytes
    nested_sections: tuple[NestedSection, ...]
    trailing_padding: bytes


def _one_module(rom: bytes, guid: str) -> FfsFile:
    hits = find_ffs_by_guid(rom, guid)
    if len(hits) != 1:
        raise ValueError(f"Expected exactly one {guid} module, found {len(hits)}")
    return hits[0]


def _align4(value: int) -> int:
    return (value + 3) & ~3


def parse_nested_sections(data: bytes) -> tuple[tuple[NestedSection, ...], bytes]:
    """Parse a decompressed PI section stream.

    Section headers and alignment bytes are reconstruction metadata. Their
    byte representation may legitimately be normalized by UEFIReplace. The
    section *bodies* are the meaningful data that must remain exact.
    """
    sections: list[NestedSection] = []
    offset = 0
    while offset < len(data):
        remaining = data[offset:]
        if remaining and all(byte in (0x00, 0xFF) for byte in remaining):
            return tuple(sections), remaining
        if offset + 4 > len(data):
            raise ValueError(f"Truncated nested section header at 0x{offset:X}")

        size = int.from_bytes(data[offset:offset + 3], "little")
        section_type = data[offset + 3]
        common_header_size = 4
        if size == 0xFFFFFF:
            if offset + 8 > len(data):
                raise ValueError(f"Truncated extended nested section header at 0x{offset:X}")
            size = int.from_bytes(data[offset + 4:offset + 8], "little")
            common_header_size = 8

        # PI section type 0x18 is EFI_SECTION_FREEFORM_SUBTYPE_GUID.
        # Its section header contains an additional 16-byte subtype GUID.
        # UEFIReplace preserves that complete header in REPLACE_MODE_BODY, so
        # those 16 bytes must never be included in the replacement body.
        header_extension_size = 16 if section_type == 0x18 else 0
        header_size = common_header_size + header_extension_size

        if size < header_size:
            raise ValueError(f"Invalid nested section size 0x{size:X} at 0x{offset:X}")
        end = offset + size
        if end > len(data):
            raise ValueError(
                f"Nested section at 0x{offset:X} extends beyond decompressed container: "
                f"0x{end:X} > 0x{len(data):X}"
            )
        aligned_end = _align4(end)
        if aligned_end > len(data):
            # A final section is allowed to end exactly at EOF without its
            # conceptual alignment bytes being physically present.
            aligned_end = end
        padding = data[end:aligned_end]
        sections.append(
            NestedSection(
                offset=offset,
                size=size,
                section_type=section_type,
                common_header_size=common_header_size,
                header_size=header_size,
                header_extension=data[
                    offset + common_header_size:offset + header_size
                ],
                body=data[offset + header_size:end],
                padding=padding,
            )
        )
        offset = aligned_end

    return tuple(sections), b""


def _section_fingerprint(section: NestedSection) -> dict[str, Any]:
    return {
        "section_type": section.section_type,
        "header_extension_size": len(section.header_extension),
        "header_extension_sha256": sha256(section.header_extension).hexdigest(),
        "body_size": len(section.body),
        "body_sha256": sha256(section.body).hexdigest(),
    }


def _semantic_layout(
    sections: tuple[NestedSection, ...],
    target_type: int,
) -> tuple[NestedSection, list[dict[str, Any]]]:
    targets = [section for section in sections if section.section_type == target_type]
    if len(targets) != 1:
        raise ValueError(
            f"Expected exactly one nested section type 0x{target_type:02X}, "
            f"found {len(targets)}"
        )
    target = targets[0]
    siblings = [
        _section_fingerprint(section)
        for section in sections
        if section is not target
    ]
    return target, siblings


def parse_guided_lzma_ffs(ffs: FfsFile, expected_raw_type: int) -> GuidedModule:
    section = 24
    if len(ffs.data) < section + 24:
        raise ValueError("FFS is too small for GUID-defined LZMA section")
    section_size = int.from_bytes(ffs.data[section:section + 3], "little")
    section_type = ffs.data[section + 3]
    section_guid = str(uuid.UUID(bytes_le=ffs.data[section + 4:section + 20])).upper()
    data_offset = int.from_bytes(ffs.data[section + 20:section + 22], "little")
    attributes = int.from_bytes(ffs.data[section + 22:section + 24], "little")
    if section_type != 0x02:
        raise ValueError(f"Expected GUID-defined section type 0x02, got 0x{section_type:02X}")
    if section_guid != LZMA_CUSTOM_DECOMPRESS_GUID:
        raise ValueError(f"Unexpected compression GUID {section_guid}")
    if data_offset < 24 or section_size > len(ffs.data) - section or section + data_offset >= len(ffs.data):
        raise ValueError("Invalid GUID-defined section bounds")
    payload = ffs.data[section + data_offset:section + section_size]
    decompressed = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
    nested_sections, trailing_padding = parse_nested_sections(decompressed)
    _semantic_layout(nested_sections, expected_raw_type)
    return GuidedModule(
        ffs=ffs,
        section_size=section_size,
        section_type=section_type,
        section_guid=section_guid,
        data_offset=data_offset,
        attributes=attributes,
        compressed_payload=payload,
        decompressed=decompressed,
        nested_sections=nested_sections,
        trailing_padding=trailing_padding,
    )


def _nv_state(rom: bytes) -> tuple[GuidedModule, Any, dict[str, int], NestedSection]:
    family = classify_amitse_family(rom)
    expected_size = family.get("intelsetup_payload_size")
    if not family.get("known") or not isinstance(expected_size, int):
        raise ValueError("firmware family is not validated for an NV-Internal IntelSetup schema")
    parsed = parse_guided_lzma_ffs(
        _one_module(rom, NV_INTERNAL_GUID),
        NV_INTERNAL_RAW_SECTION_TYPE,
    )
    target, _siblings = _semantic_layout(parsed.nested_sections, NV_INTERNAL_RAW_SECTION_TYPE)
    record = select_intelsetup_record(parsed.decompressed, expected_size, guid_index=0)
    values = {
        f"0x{offset:04X}": parsed.decompressed[record.data_offset + offset]
        for offset in CSTATE_TARGETS
    }
    return parsed, record, values, target


@dataclass(frozen=True)
class AmitseCStateState:
    parsed: GuidedModule
    target: NestedSection
    layout_name: str
    block_offset: int
    record_offset: int
    patch_offsets: tuple[int, ...]
    desired_values: tuple[int, ...]
    absolute_offsets: tuple[int, ...]
    values: tuple[int, ...]

    @property
    def matches(self) -> bool:
        return self.values == self.desired_values


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _amitse_record_shape(record: bytes) -> bool:
    """Return True for the 0x48-byte AMITSE control-record framing we know.

    We intentionally do not match the full record. Question/control IDs and
    several payload fields vary between firmware revisions. Only the stable
    framing and internal relationship between the paired IDs are checked.
    """
    if len(record) != AMITSE_RECORD_SIZE:
        return False
    if _u32(record, 0x34) != AMITSE_RECORD_TAIL_MARKER:
        return False
    if _u32(record, 0x3C) != 0:
        return False
    if _u32(record, 0x40) != AMITSE_RECORD_TRAILER_A:
        return False
    if _u32(record, 0x44) != AMITSE_RECORD_TRAILER_B:
        return False
    record_type = _u32(record, 0x00)
    if record_type == 0x09:
        first_id = _u32(record, 0x04)
        paired_id = _u32(record, 0x20)
        if first_id != paired_id + 1:
            return False
        if _u32(record, 0x1C) != 0x00010001:
            return False
    elif record_type != 0:
        return False
    return True


def _scan_amitse_cstate_blocks(target: NestedSection) -> list[dict[str, Any]]:
    """Find structurally valid six-record CPU C-state blocks.

    The supported XD3 families expose the six records with the same stable
    8-byte trailer. We use that trailer only as a record-boundary anchor, then
    validate the complete six-record relationship: one placeholder-style first
    record, five type-9 records, consecutive tail IDs, consecutive tag fields,
    and paired IDs differing by exactly one.
    """
    body = target.body
    trailer = (
        AMITSE_RECORD_TRAILER_A.to_bytes(4, "little")
        + AMITSE_RECORD_TRAILER_B.to_bytes(4, "little")
    )
    record_bases: list[int] = []
    search = 0
    while True:
        trailer_offset = body.find(trailer, search)
        if trailer_offset < 0:
            break
        base = trailer_offset - 0x40
        if base >= 0:
            record = body[base:base + AMITSE_RECORD_SIZE]
            if _amitse_record_shape(record):
                record_bases.append(base)
        search = trailer_offset + 1

    base_set = set(record_bases)
    result: list[dict[str, Any]] = []
    for base in record_bases:
        expected_bases = tuple(base + index * AMITSE_RECORD_SIZE for index in range(6))
        if not all(candidate in base_set for candidate in expected_bases):
            continue
        records = tuple(
            body[candidate:candidate + AMITSE_RECORD_SIZE]
            for candidate in expected_bases
        )
        if _u32(records[0], 0x00) != 0:
            continue
        if any(_u32(record, 0x00) != 0x09 for record in records[1:]):
            continue
        qids = tuple(_u32(record, 0x38) for record in records)
        if qids != tuple(range(qids[0], qids[0] + 6)):
            continue
        tags = tuple(_u32(record, 0x08) for record in records[1:])
        if tags != tuple(range(tags[0], tags[0] + 5)):
            continue
        result.append({
            "body_offset": base,
            "qids": qids,
            "tags": tags,
            "records": records,
        })
    return result


def _v9s_amitse_normalized_hash(data: bytes) -> str:
    if len(data) != V9S_AMITSE_DECOMPRESSED_SIZE:
        return ""
    normalized = bytearray(data)
    for absolute, stock, _desired in V9S_AMITSE_CSTATE_PATCHES + V9S_AMITSE_OPT_PATCHES:
        normalized[absolute] = stock
    return sha256(normalized).hexdigest()


def _v9s_amitse_state(parsed: GuidedModule, target: NestedSection) -> AmitseCStateState:
    actual_norm = _v9s_amitse_normalized_hash(parsed.decompressed)
    if actual_norm not in (V9S_AMITSE_NORMALIZED_SHA256, K9S_AMITSE_NORMALIZED_SHA256):
        raise ValueError(
            "V9S AMITSESetupData normalized fingerprint mismatch: "
            f"{actual_norm or 'wrong-size'} != {V9S_AMITSE_NORMALIZED_SHA256}"
        )
    offsets = tuple(item[0] for item in V9S_AMITSE_CSTATE_PATCHES)
    desired = tuple(item[2] for item in V9S_AMITSE_CSTATE_PATCHES)
    values = tuple(parsed.decompressed[offset] for offset in offsets)
    for value, (_off, stock, want) in zip(values, V9S_AMITSE_CSTATE_PATCHES):
        if value not in (stock, want):
            raise ValueError(
                f"V9S C-state default byte has unexpected value {value}; expected {stock} or {want}"
            )
    return AmitseCStateState(
        parsed=parsed,
        target=target,
        layout_name="qiyida-k9s-791a0" if actual_norm == K9S_AMITSE_NORMALIZED_SHA256 else "v9s-791a0",
        block_offset=0x58E00,
        record_offset=0x58E00,
        patch_offsets=(0x38, 0x39, 0xC8, 0xC9),
        desired_values=desired,
        absolute_offsets=offsets,
        values=values,
    )


def _locate_amitse_cstate_layout(parsed: GuidedModule, target: NestedSection) -> AmitseCStateState:
    blocks = _scan_amitse_cstate_blocks(target)
    matches: list[AmitseCStateState] = []
    body_start = target.offset + target.header_size

    for block in blocks:
        qids = block["qids"]
        tags = block["tags"]
        for layout in AMITSE_CSTATE_LAYOUTS:
            if qids[0] != layout["first_qid"] or tags[0] != layout["first_tag"]:
                continue
            record_body_offset = block["body_offset"] + AMITSE_RECORD_SIZE * 2
            record_absolute = body_start + record_body_offset
            patch_offsets = tuple(layout["patch_offsets"])
            desired_values = tuple(layout["desired_values"])
            absolute_offsets = tuple(record_absolute + rel for rel in patch_offsets)
            values = tuple(parsed.decompressed[offset] for offset in absolute_offsets)
            if any(value not in (0, 1, 2, 3) for value in values):
                continue
            matches.append(AmitseCStateState(
                parsed=parsed,
                target=target,
                layout_name=layout["name"],
                block_offset=body_start + block["body_offset"],
                record_offset=record_absolute,
                patch_offsets=patch_offsets,
                desired_values=desired_values,
                absolute_offsets=absolute_offsets,
                values=values,
            ))

    if len(matches) != 1:
        diagnostic = [
            {
                "body_offset": hex(block["body_offset"]),
                "first_qid": hex(block["qids"][0]),
                "first_tag": hex(block["tags"][0]),
            }
            for block in blocks
        ]
        raise ValueError(
            "Expected exactly one supported AMITSE CPU C-state block, "
            f"found {len(matches)} supported match(es); structural candidates={diagnostic}"
        )
    return matches[0]


def _amitse_state(rom: bytes) -> AmitseCStateState:
    family = classify_amitse_family(rom)
    parsed = parse_guided_lzma_ffs(
        _one_module(rom, AMITSE_SETUP_DATA_GUID),
        AMITSE_RAW_SECTION_TYPE,
    )
    target, _siblings = _semantic_layout(parsed.nested_sections, AMITSE_RAW_SECTION_TYPE)
    if family.get("board_family") == "x99-v9s":
        return _v9s_amitse_state(parsed, target)
    return _locate_amitse_cstate_layout(parsed, target)


def inspect_all_cstates(path: Path) -> dict:
    rom = path.read_bytes()
    primary = inspect_primary_cstates(path)
    result: dict[str, Any] = {"primary_intelsetup": primary}
    try:
        parsed, record, values, _target = _nv_state(rom)
        nv_matches = all(
            values[f"0x{offset:04X}"] == value
            for offset, value in CSTATE_TARGETS.items()
        )
        result["nv_internal_default"] = {
            "supported": True,
            "matches": nv_matches,
            "ffs_offset": parsed.ffs.offset,
            "ffs_size": parsed.ffs.size,
            "record_offset": record.nvar_offset,
            "data_offset": record.data_offset,
            "values": values,
            "decompressed_sha256": sha256(parsed.decompressed).hexdigest(),
        }
    except Exception as exc:
        result["nv_internal_default"] = {
            "supported": False,
            "matches": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        state = _amitse_state(rom)
        result["amitse_setup_default"] = {
            "supported": True,
            "matches": state.matches,
            "layout": state.layout_name,
            "ffs_offset": state.parsed.ffs.offset,
            "ffs_size": state.parsed.ffs.size,
            "block_offset": state.block_offset,
            "record_offset": state.record_offset,
            "decompressed_offsets": list(state.absolute_offsets),
            "values": list(state.values),
            "desired_values": list(state.desired_values),
            "value": state.values[-1] if len(state.values) == 1 else list(state.values),
            "decompressed_sha256": sha256(state.parsed.decompressed).hexdigest(),
        }
    except Exception as exc:
        result["amitse_setup_default"] = {
            "supported": False,
            "matches": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    result["matches"] = all(
        result[name].get("matches")
        for name in (
            "primary_intelsetup",
            "nv_internal_default",
            "amitse_setup_default",
        )
    )
    result["supported"] = all(
        result[name].get("supported")
        for name in (
            "primary_intelsetup",
            "nv_internal_default",
            "amitse_setup_default",
        )
    )
    return result


def build_nv_internal_body(rom: bytes, output_body: Path) -> dict:
    parsed, record, values_before, target = _nv_state(rom)
    patched = bytearray(parsed.decompressed)
    intended: set[int] = set()
    for offset, value in CSTATE_TARGETS.items():
        absolute = record.data_offset + offset
        if patched[absolute] != value:
            intended.add(absolute)
            patched[absolute] = value
    changed = [
        index
        for index, (before, after) in enumerate(zip(parsed.decompressed, patched))
        if before != after
    ]
    if set(changed) != intended:
        raise RuntimeError(
            f"NV-Internal changed unexpected decompressed offsets: "
            f"expected {sorted(intended)}, got {changed}"
        )
    target_body_start = target.offset + target.header_size
    target_body_end = target.offset + target.size
    body = bytes(patched[target_body_start:target_body_end])
    output_body.write_bytes(body)
    values_after = {
        f"0x{offset:04X}": patched[record.data_offset + offset]
        for offset in CSTATE_TARGETS
    }
    _target, siblings = _semantic_layout(parsed.nested_sections, NV_INTERNAL_RAW_SECTION_TYPE)
    return {
        "guid": NV_INTERNAL_GUID,
        "section_type": NV_INTERNAL_RAW_SECTION_TYPE,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "before": values_before,
        "after": values_after,
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
        "body_size": len(body),
    }


def build_amitse_body(rom: bytes, output_body: Path) -> dict:
    state = _amitse_state(rom)
    parsed = state.parsed
    target = state.target
    patched = bytearray(parsed.decompressed)
    before = tuple(state.values)
    intended: set[int] = set()
    for absolute, desired in zip(state.absolute_offsets, state.desired_values):
        if patched[absolute] != desired:
            intended.add(absolute)
            patched[absolute] = desired

    changed = [
        index
        for index, (old, new) in enumerate(zip(parsed.decompressed, patched))
        if old != new
    ]
    if set(changed) != intended:
        raise RuntimeError(
            f"AMITSE changed unexpected decompressed offsets for {state.layout_name}: "
            f"expected {sorted(intended)}, got {changed}"
        )

    target_body_start = target.offset + target.header_size
    target_body_end = target.offset + target.size
    body = bytes(patched[target_body_start:target_body_end])
    output_body.write_bytes(body)
    _target, siblings = _semantic_layout(parsed.nested_sections, AMITSE_RAW_SECTION_TYPE)
    after = tuple(patched[offset] for offset in state.absolute_offsets)
    body_offsets = tuple(offset - target_body_start for offset in state.absolute_offsets)
    return {
        "guid": AMITSE_SETUP_DATA_GUID,
        "section_type": AMITSE_RAW_SECTION_TYPE,
        "layout": state.layout_name,
        "changed": bool(changed),
        "changed_decompressed_offsets": changed,
        "decompressed_offsets": list(state.absolute_offsets),
        "target_body_offsets": list(body_offsets),
        "before": list(before),
        "after": list(after),
        "expected_values": list(state.desired_values),
        "expected_full_decompressed_sha256": sha256(bytes(patched)).hexdigest(),
        "expected_target_header_extension_sha256": sha256(target.header_extension).hexdigest(),
        "expected_target_header_extension_size": len(target.header_extension),
        "expected_target_body_sha256": sha256(body).hexdigest(),
        "expected_sibling_sections": siblings,
        "body_size": len(body),
    }


def validate_nested_body(
    output_path: Path,
    guid: str,
    raw_type: int,
    expected_target_body_sha256: str,
    expected_target_header_extension_sha256: str,
    expected_target_header_extension_size: int,
    expected_sibling_sections: list[dict[str, Any]],
    expected_full_decompressed_sha256: str,
    expected_cstate_body_offsets: list[int] | tuple[int, ...] | None = None,
    expected_cstate_values: list[int] | tuple[int, ...] | None = None,
) -> dict:
    """Validate semantic section contents after UEFIReplace reconstruction.

    The target section body and every non-target sibling body must match
    exactly. Section headers, four-byte alignment padding, and trailing
    00/FF padding are deliberately excluded from the hard equality check,
    because UEFIReplace may normalize those metadata bytes while preserving
    all meaningful contents.
    """
    parsed = parse_guided_lzma_ffs(_one_module(output_path.read_bytes(), guid), raw_type)
    target, siblings = _semantic_layout(parsed.nested_sections, raw_type)

    actual_header_extension_hash = sha256(target.header_extension).hexdigest()
    if len(target.header_extension) != expected_target_header_extension_size:
        raise RuntimeError(
            f"{guid} target section header-extension size changed: "
            f"{len(target.header_extension)} != {expected_target_header_extension_size}"
        )
    if actual_header_extension_hash != expected_target_header_extension_sha256:
        raise RuntimeError(
            f"{guid} target section header extension changed: "
            f"{actual_header_extension_hash} != "
            f"{expected_target_header_extension_sha256}"
        )

    actual_target_hash = sha256(target.body).hexdigest()
    if actual_target_hash != expected_target_body_sha256:
        raise RuntimeError(
            f"{guid} target section body hash mismatch: "
            f"{actual_target_hash} != {expected_target_body_sha256}"
        )
    if siblings != expected_sibling_sections:
        raise RuntimeError(
            f"{guid} non-target nested sections changed: "
            f"actual {siblings!r} != expected {expected_sibling_sections!r}"
        )
    if expected_cstate_body_offsets is not None:
        offsets = tuple(expected_cstate_body_offsets)
        values = tuple(expected_cstate_values or (1 for _ in offsets))
        if len(offsets) != len(values):
            raise RuntimeError(f"{guid} C-state validation offset/value count mismatch")
        for body_offset, expected_value in zip(offsets, values):
            if not (0 <= body_offset < len(target.body)):
                raise RuntimeError(
                    f"{guid} C-state body offset 0x{body_offset:X} "
                    f"is outside target body size 0x{len(target.body):X}"
                )
            if target.body[body_offset] != expected_value:
                raise RuntimeError(
                    f"{guid} C-state value at body offset 0x{body_offset:X} is "
                    f"{target.body[body_offset]}, expected {expected_value}"
                )

    actual_full_hash = sha256(parsed.decompressed).hexdigest()
    return {
        "target_header_extension_sha256": actual_header_extension_hash,
        "target_header_extension_size": len(target.header_extension),
        "target_body_sha256": actual_target_hash,
        "sibling_sections": siblings,
        "full_decompressed_sha256": actual_full_hash,
        "expected_full_decompressed_sha256": expected_full_decompressed_sha256,
        "full_container_hash_matches": actual_full_hash == expected_full_decompressed_sha256,
        "metadata_normalized": actual_full_hash != expected_full_decompressed_sha256,
        "ffs_offset": parsed.ffs.offset,
        "ffs_size": parsed.ffs.size,
        "trailing_padding_size": len(parsed.trailing_padding),
    }
