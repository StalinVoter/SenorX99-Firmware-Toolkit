from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import struct

FIT_POINTER_DISTANCE_FROM_4G = 0x40
FIT_ENTRY_SIZE = 16
FIT_HEADER_SIGNATURE = b'_FIT_   '
FIT_HEADER_TYPE = 0
FIT_MICROCODE_TYPE = 1
FIT_EXPECTED_VERSION = 0x0100
MAX_FIT_ENTRIES = 4096


@dataclass(frozen=True)
class MicrocodeRecord:
    offset: int
    physical_address: int
    revision: int
    date_raw: int
    signature: int
    checksum: int
    loader_revision: int
    processor_flags: int
    data_size: int
    total_size: int
    sha256: str

    def identity(self) -> dict[str, Any]:
        return {
            'sha256': self.sha256,
            'signature': self.signature,
            'revision': self.revision,
            'processor_flags': self.processor_flags,
            'date_raw': self.date_raw,
            'total_size': self.total_size,
        }


@dataclass(frozen=True)
class FitEntry:
    index: int
    offset: int
    address: int
    size: int
    reserved: int
    version: int
    entry_type: int
    checksum_valid: bool
    checksum: int
    raw: bytes


@dataclass(frozen=True)
class FitTable:
    image_base: int
    pointer_file_offset: int
    pointer_address: int
    table_file_offset: int
    entries: tuple[FitEntry, ...]

    @property
    def header(self) -> FitEntry:
        return self.entries[0]

    @property
    def type1_entries(self) -> tuple[FitEntry, ...]:
        return tuple(entry for entry in self.entries if entry.raw != b'\xFF' * FIT_ENTRY_SIZE and entry.entry_type == FIT_MICROCODE_TYPE)


def image_base_for_top_mapped_rom(size: int) -> int:
    if size <= 0 or size > (1 << 32):
        raise ValueError(f'unsupported ROM size: {size}')
    return (1 << 32) - size


def physical_to_offset(address: int, image_size: int) -> int:
    base = image_base_for_top_mapped_rom(image_size)
    if not (base <= address < (1 << 32)):
        raise ValueError(f'physical address 0x{address:X} is outside top-mapped {image_size}-byte ROM')
    return address - base


def offset_to_physical(offset: int, image_size: int) -> int:
    if not (0 <= offset < image_size):
        raise ValueError(f'file offset 0x{offset:X} outside ROM')
    return image_base_for_top_mapped_rom(image_size) + offset


def _parse_fit_entry(buf: bytes, offset: int, index: int) -> FitEntry:
    if offset < 0 or offset + FIT_ENTRY_SIZE > len(buf):
        raise ValueError('truncated FIT entry')
    raw = buf[offset:offset + FIT_ENTRY_SIZE]
    address = int.from_bytes(raw[0:8], 'little')
    size = int.from_bytes(raw[8:11], 'little')
    reserved = raw[11]
    version = int.from_bytes(raw[12:14], 'little')
    type_cv = raw[14]
    entry_type = type_cv & 0x7F
    checksum_valid = bool(type_cv & 0x80)
    checksum = raw[15]
    return FitEntry(index, offset, address, size, reserved, version, entry_type, checksum_valid, checksum, raw)


def parse_fit(buf: bytes) -> FitTable:
    if len(buf) < FIT_POINTER_DISTANCE_FROM_4G:
        raise ValueError('ROM is too small to contain the fixed FIT pointer')
    pointer_file_offset = len(buf) - FIT_POINTER_DISTANCE_FROM_4G
    pointer_address = int.from_bytes(buf[pointer_file_offset:pointer_file_offset + 8], 'little')
    if pointer_address & 0xF:
        raise ValueError(f'FIT pointer 0x{pointer_address:X} is not 16-byte aligned')
    table_file_offset = physical_to_offset(pointer_address, len(buf))
    header = _parse_fit_entry(buf, table_file_offset, 0)
    if header.raw[0:8] != FIT_HEADER_SIGNATURE:
        raise ValueError(
            f'FIT pointer resolves to 0x{table_file_offset:X}, but header signature is {header.raw[0:8]!r}'
        )
    if header.entry_type != FIT_HEADER_TYPE:
        raise ValueError(f'first FIT entry is type {header.entry_type}, not type 0')
    if header.size < 1 or header.size > MAX_FIT_ENTRIES:
        raise ValueError(f'implausible FIT entry count {header.size}')
    table_end = table_file_offset + header.size * FIT_ENTRY_SIZE
    if table_end > len(buf):
        raise ValueError('FIT table extends beyond ROM')
    entries = tuple(
        _parse_fit_entry(buf, table_file_offset + index * FIT_ENTRY_SIZE, index)
        for index in range(header.size)
    )
    return FitTable(
        image_base=image_base_for_top_mapped_rom(len(buf)),
        pointer_file_offset=pointer_file_offset,
        pointer_address=pointer_address,
        table_file_offset=table_file_offset,
        entries=entries,
    )


def scan_intel_microcodes(buf: bytes) -> list[MicrocodeRecord]:
    """Scan for valid, uncompressed, 16-byte-aligned Intel microcode updates.

    Candidate discovery uses the fixed HeaderVersion dword (1) instead of
    unpacking at every 16-byte position in a 16 MiB image. Full structural and
    checksum validation is still performed on every candidate.
    """
    result: list[MicrocodeRecord] = []
    image_base = image_base_for_top_mapped_rom(len(buf))
    needle = b'\x01\x00\x00\x00'
    search = 0
    while True:
        off = buf.find(needle, search)
        if off < 0:
            break
        search = off + 1
        if off & 0xF or off + 48 > len(buf):
            continue
        try:
            (
                header_version,
                revision,
                date_raw,
                signature,
                checksum,
                loader_revision,
                processor_flags,
                data_size,
                total_size,
            ) = struct.unpack_from('<9I', buf, off)
        except struct.error:
            continue
        if header_version != 1 or loader_revision != 1:
            continue
        total = 2048 if total_size == 0 else total_size
        data = 2000 if data_size == 0 else data_size
        if total < 48 or total > 0x40000 or total % 4:
            continue
        if off + total > len(buf) or data > total - 48:
            continue
        words = struct.unpack_from(f'<{total // 4}I', buf, off)
        if sum(words) & 0xFFFFFFFF:
            continue
        blob = buf[off:off + total]
        result.append(
            MicrocodeRecord(
                offset=off,
                physical_address=image_base + off,
                revision=revision,
                date_raw=date_raw,
                signature=signature,
                checksum=checksum,
                loader_revision=loader_revision,
                processor_flags=processor_flags,
                data_size=data,
                total_size=total,
                sha256=sha256(blob).hexdigest(),
            )
        )
    return result


def _table_checksum_valid(buf: bytes, table: FitTable) -> bool | None:
    if not table.header.checksum_valid:
        return None
    start = table.table_file_offset
    end = start + len(table.entries) * FIT_ENTRY_SIZE
    return (sum(buf[start:end]) & 0xFF) == 0


def _entry_target(entry: FitEntry, buf: bytes, micro_by_address: dict[int, MicrocodeRecord]) -> dict[str, Any]:
    item: dict[str, Any] = {
        'index': entry.index,
        'address': entry.address,
        'address_hex': f'0x{entry.address:08X}',
        'size': entry.size,
        'version': entry.version,
        'checksum_valid_bit': entry.checksum_valid,
        'checksum': entry.checksum,
    }
    if entry.address in micro_by_address:
        record = micro_by_address[entry.address]
        item.update(
            status='PASS',
            target='microcode',
            signature=record.signature,
            signature_hex=f'0x{record.signature:08X}',
            revision=record.revision,
            revision_hex=f'0x{record.revision:08X}',
            processor_flags=record.processor_flags,
            total_size=record.total_size,
            microcode_sha256=record.sha256,
            actual_address=record.physical_address,
            actual_address_hex=f'0x{record.physical_address:08X}',
            delta=0,
        )
        return item
    try:
        off = physical_to_offset(entry.address, len(buf))
    except ValueError:
        item.update(status='INVALID', target='outside-rom')
        return item
    if off + 4 <= len(buf) and buf[off:off + 4] == b'\xFF\xFF\xFF\xFF':
        item.update(status='EMPTY', target='empty-slot')
        return item
    item.update(status='STALE', target='not-a-microcode-header')
    return item


def _common_delta_plan(
    unresolved: list[FitEntry],
    available_microcodes: list[MicrocodeRecord],
) -> tuple[int | None, dict[int, MicrocodeRecord]]:
    if not unresolved:
        return 0, {}
    if len(available_microcodes) < len(unresolved):
        return None, {}
    by_address = {record.physical_address: record for record in available_microcodes}
    candidates: list[tuple[int, dict[int, MicrocodeRecord]]] = []
    first = unresolved[0]
    for record in available_microcodes:
        delta = record.physical_address - first.address
        if delta == 0 or (delta & 0xF):
            continue
        mapping: dict[int, MicrocodeRecord] = {}
        used: set[int] = set()
        valid = True
        for entry in unresolved:
            target_address = entry.address + delta
            target = by_address.get(target_address)
            if target is None or target.physical_address in used:
                valid = False
                break
            mapping[entry.index] = target
            used.add(target.physical_address)
        if valid:
            candidates.append((delta, mapping))
    # A repair is intentionally permitted only when there is exactly one
    # common displacement that maps every unresolved Type-1 entry one-to-one
    # onto real, checksum-valid microcode headers.
    if len(candidates) != 1:
        return None, {}
    return candidates[0]


def inspect_fit_bytes(buf: bytes) -> dict[str, Any]:
    table = parse_fit(buf)
    microcodes = scan_intel_microcodes(buf)
    micro_by_address = {record.physical_address: record for record in microcodes}

    errors: list[str] = []
    warnings: list[str] = []
    if table.header.version != FIT_EXPECTED_VERSION:
        errors.append(f'FIT header version 0x{table.header.version:04X} != 0x{FIT_EXPECTED_VERSION:04X}')
    if table.header.reserved != 0:
        warnings.append(f'FIT header reserved byte is 0x{table.header.reserved:02X}')

    # Some Aptio IV images reserve unused FIT slots as all-FF records inside
    # the header-declared table. They are holes, not real Type-0x7F entries,
    # and must be ignored for ordering checks.
    types = [entry.entry_type for entry in table.entries if entry.raw != b'\xFF' * FIT_ENTRY_SIZE]
    if types != sorted(types):
        errors.append('FIT entries are not ordered by ascending type')

    checksum_state = _table_checksum_valid(buf, table)
    if checksum_state is False:
        errors.append('FIT header C_V is set but the table checksum is not zero')

    type1_items: list[dict[str, Any]] = []
    direct_addresses: set[int] = set()
    unresolved: list[FitEntry] = []
    for entry in table.type1_entries:
        item = _entry_target(entry, buf, micro_by_address)
        if entry.version != FIT_EXPECTED_VERSION:
            item['format_error'] = f'version 0x{entry.version:04X}'
            errors.append(f'Type-1 entry {entry.index} has version 0x{entry.version:04X}')
        if entry.size != 0:
            item['format_error'] = f'size={entry.size}'
            errors.append(f'Type-1 entry {entry.index} has non-zero size {entry.size}')
        if entry.checksum_valid:
            item['format_error'] = 'C_V=1'
            errors.append(f'Type-1 entry {entry.index} has C_V set')
        if entry.address & 0xF:
            item['format_error'] = 'unaligned address'
            errors.append(f'Type-1 entry {entry.index} address 0x{entry.address:X} is not 16-byte aligned')
        if item['status'] == 'PASS':
            direct_addresses.add(entry.address)
        elif item['status'] == 'STALE':
            unresolved.append(entry)
        elif item['status'] == 'INVALID':
            errors.append(f'Type-1 entry {entry.index} points outside the ROM')
        type1_items.append(item)

    if not table.type1_entries:
        errors.append('FIT contains no Type-1 microcode entries')

    available = [record for record in microcodes if record.physical_address not in direct_addresses]
    delta, mapping = _common_delta_plan(unresolved, available)
    if unresolved and delta is not None:
        for item in type1_items:
            target = mapping.get(item['index'])
            if target is None:
                continue
            item.update(
                repairable=True,
                proposed_address=target.physical_address,
                proposed_address_hex=f'0x{target.physical_address:08X}',
                proposed_signature=target.signature,
                proposed_signature_hex=f'0x{target.signature:08X}',
                proposed_revision=target.revision,
                proposed_revision_hex=f'0x{target.revision:08X}',
                proposed_microcode_sha256=target.sha256,
                delta=delta,
            )

    referenced = {
        item.get('actual_address') for item in type1_items if item.get('status') == 'PASS'
    }
    if mapping:
        referenced.update(record.physical_address for record in mapping.values())
    unreferenced = [record for record in microcodes if record.physical_address not in referenced]

    stale_count = sum(item['status'] == 'STALE' for item in type1_items)
    invalid_count = sum(item['status'] == 'INVALID' for item in type1_items)
    repairable = bool(stale_count) and invalid_count == 0 and delta is not None and not errors
    if errors:
        status = 'INVALID'
    elif stale_count:
        status = 'STALE' if repairable else 'INVALID'
    else:
        status = 'PASS'

    return {
        'supported': True,
        'status': status,
        'repairable': repairable,
        'repair_method': 'unique-common-delta' if repairable else None,
        'common_delta': delta if repairable else None,
        'common_delta_hex': (f'{delta:+#x}' if repairable and delta is not None else None),
        'image_base': table.image_base,
        'image_base_hex': f'0x{table.image_base:08X}',
        'pointer_file_offset': table.pointer_file_offset,
        'pointer_address': table.pointer_address,
        'pointer_address_hex': f'0x{table.pointer_address:08X}',
        'table_file_offset': table.table_file_offset,
        'entry_count': len(table.entries),
        'header_version': table.header.version,
        'header_checksum_required': table.header.checksum_valid,
        'header_checksum_valid': checksum_state,
        'type1_count': len(table.type1_entries),
        'type1_entries': type1_items,
        'valid_microcodes': [
            {
                'offset': record.offset,
                'address': record.physical_address,
                'address_hex': f'0x{record.physical_address:08X}',
                'signature': record.signature,
                'signature_hex': f'0x{record.signature:08X}',
                'revision': record.revision,
                'revision_hex': f'0x{record.revision:08X}',
                'processor_flags': record.processor_flags,
                'total_size': record.total_size,
                'sha256': record.sha256,
            }
            for record in microcodes
        ],
        'unreferenced_microcodes': [
            {
                'address': record.physical_address,
                'address_hex': f'0x{record.physical_address:08X}',
                'signature_hex': f'0x{record.signature:08X}',
                'revision_hex': f'0x{record.revision:08X}',
                'sha256': record.sha256,
            }
            for record in unreferenced
        ],
        'errors': errors,
        'warnings': warnings,
    }


def inspect_fit(path: Path) -> dict[str, Any]:
    try:
        return inspect_fit_bytes(path.read_bytes())
    except Exception as exc:
        return {
            'supported': False,
            'status': 'INVALID',
            'repairable': False,
            'error': f'{type(exc).__name__}: {exc}',
            'errors': [f'{type(exc).__name__}: {exc}'],
            'warnings': [],
            'type1_entries': [],
        }


def capture_fit_reference(buf: bytes) -> dict[str, Any]:
    """Capture exact Type-1->microcode identities while the FIT is known-good.

    This is used before a firmware-volume rebuild. The later repair can then
    locate the exact same microcode blobs by SHA-256, without assuming any
    displacement such as +0x110.
    """
    report = inspect_fit_bytes(buf)
    if report['status'] != 'PASS':
        raise ValueError(f'cannot capture FIT reference from non-PASS FIT: {report["status"]}')
    identities: list[dict[str, Any]] = []
    for item in report['type1_entries']:
        if item['status'] == 'EMPTY':
            identities.append({'index': item['index'], 'kind': 'empty', 'address': item['address']})
        elif item['status'] == 'PASS':
            identities.append(
                {
                    'index': item['index'],
                    'kind': 'microcode',
                    'sha256': item['microcode_sha256'],
                    'signature': item['signature'],
                    'revision': item['revision'],
                    'processor_flags': next(
                        record['processor_flags']
                        for record in report['valid_microcodes']
                        if record['sha256'] == item['microcode_sha256']
                    ),
                }
            )
        else:
            raise ValueError(f'cannot capture Type-1 entry {item["index"]}: {item["status"]}')
    return {
        'entry_count': report['entry_count'],
        'type1_identities': identities,
        'pointer_address': report['pointer_address'],
        'table_file_offset': report['table_file_offset'],
    }


def _reference_mapping(buf: bytes, table: FitTable, reference: dict[str, Any]) -> dict[int, MicrocodeRecord]:
    if reference.get('entry_count') != len(table.entries):
        raise ValueError('FIT entry count changed since reference capture')
    if reference.get('pointer_address') != table.pointer_address:
        raise ValueError('FIT pointer changed since reference capture')
    current_type1 = {entry.index: entry for entry in table.type1_entries}
    microcodes = scan_intel_microcodes(buf)
    by_hash: dict[str, list[MicrocodeRecord]] = {}
    for record in microcodes:
        by_hash.setdefault(record.sha256, []).append(record)
    mapping: dict[int, MicrocodeRecord] = {}
    for identity in reference.get('type1_identities', []):
        index = identity['index']
        if index not in current_type1:
            raise ValueError(f'Type-1 entry index {index} disappeared after rebuild')
        if identity['kind'] == 'empty':
            # Empty slots are intentionally not rewritten by the repair logic.
            continue
        hits = by_hash.get(identity['sha256'], [])
        if len(hits) != 1:
            raise ValueError(
                f'expected exactly one final microcode matching Type-1 entry {index} SHA-256; found {len(hits)}'
            )
        record = hits[0]
        if (
            record.signature != identity['signature']
            or record.revision != identity['revision']
            or record.processor_flags != identity['processor_flags']
        ):
            raise ValueError(f'microcode identity mismatch for Type-1 entry {index}')
        mapping[index] = record
    return mapping


def _fallback_mapping(buf: bytes, report: dict[str, Any]) -> dict[int, MicrocodeRecord]:
    if report['status'] == 'PASS':
        return {}
    if not report.get('repairable') or report.get('repair_method') != 'unique-common-delta':
        raise ValueError('FIT is not safely repairable without a captured reference')
    microcodes = {record.physical_address: record for record in scan_intel_microcodes(buf)}
    mapping: dict[int, MicrocodeRecord] = {}
    for item in report['type1_entries']:
        if item.get('status') != 'STALE':
            continue
        address = item.get('proposed_address')
        record = microcodes.get(address)
        if record is None:
            raise ValueError(f'proposed FIT target 0x{address:X} vanished during repair planning')
        mapping[item['index']] = record
    if not mapping:
        raise ValueError('FIT repair plan contains no address changes')
    return mapping


def _recompute_fit_checksum(buf: bytearray, table: FitTable) -> int | None:
    if not table.header.checksum_valid:
        return None
    checksum_offset = table.table_file_offset + 15
    buf[checksum_offset] = 0
    start = table.table_file_offset
    end = start + len(table.entries) * FIT_ENTRY_SIZE
    checksum = (-sum(buf[start:end])) & 0xFF
    buf[checksum_offset] = checksum
    return checksum


def repair_fit_file(path: Path, reference: dict[str, Any] | None = None) -> dict[str, Any]:
    before_bytes = path.read_bytes()
    before = inspect_fit_bytes(before_bytes)
    if before['status'] == 'INVALID':
        raise ValueError(f'FIT is invalid and cannot be repaired automatically: {before.get("errors")}')
    table = parse_fit(before_bytes)

    if reference is not None:
        mapping = _reference_mapping(before_bytes, table, reference)
        method = 'captured-microcode-identity'
    else:
        mapping = _fallback_mapping(before_bytes, before) if before['status'] != 'PASS' else {}
        method = 'unique-common-delta' if mapping else 'already-valid'

    changes: list[dict[str, Any]] = []
    work = bytearray(before_bytes)
    entry_by_index = {entry.index: entry for entry in table.type1_entries}
    for index, record in sorted(mapping.items()):
        entry = entry_by_index[index]
        if entry.address == record.physical_address:
            continue
        address_offset = entry.offset
        work[address_offset:address_offset + 8] = record.physical_address.to_bytes(8, 'little')
        changes.append(
            {
                'entry_index': index,
                'old_address': entry.address,
                'old_address_hex': f'0x{entry.address:08X}',
                'new_address': record.physical_address,
                'new_address_hex': f'0x{record.physical_address:08X}',
                'delta': record.physical_address - entry.address,
                'delta_hex': f'{record.physical_address - entry.address:+#x}',
                'signature': record.signature,
                'signature_hex': f'0x{record.signature:08X}',
                'revision': record.revision,
                'revision_hex': f'0x{record.revision:08X}',
                'microcode_sha256': record.sha256,
            }
        )

    checksum = _recompute_fit_checksum(work, table) if changes else None
    if changes:
        path.write_bytes(work)

    after_bytes = path.read_bytes()
    after = inspect_fit_bytes(after_bytes)
    if after['status'] != 'PASS':
        raise RuntimeError(f'FIT post-repair validation failed: {after}')

    # Restrict direct mutations to Type-1 address fields and, only when C_V is
    # set, the FIT header checksum byte. This catches accidental collateral edits.
    allowed: set[int] = set()
    for change in changes:
        entry = entry_by_index[change['entry_index']]
        allowed.update(range(entry.offset, entry.offset + 8))
    if table.header.checksum_valid and changes:
        allowed.add(table.table_file_offset + 15)
    actual_diffs = [i for i, (a, b) in enumerate(zip(before_bytes, after_bytes)) if a != b]
    unexpected = [i for i in actual_diffs if i not in allowed]
    if unexpected:
        raise RuntimeError(f'FIT repair changed unexpected ROM offsets: {unexpected[:16]}')

    # All non-Type-1 FIT entries must remain byte-identical, except the header
    # checksum byte when header C_V requires recomputation.
    after_table = parse_fit(after_bytes)
    for old_entry, new_entry in zip(table.entries, after_table.entries):
        if old_entry.entry_type == FIT_MICROCODE_TYPE:
            if old_entry.raw[8:] != new_entry.raw[8:]:
                raise RuntimeError(f'FIT Type-1 entry {old_entry.index} non-address fields changed')
            continue
        if old_entry.index == 0 and table.header.checksum_valid and changes:
            if old_entry.raw[:15] != new_entry.raw[:15]:
                raise RuntimeError('FIT header changed outside checksum byte')
        elif old_entry.raw != new_entry.raw:
            raise RuntimeError(f'non-Type-1 FIT entry {old_entry.index} changed')

    return {
        'changed': bool(changes),
        'method': method,
        'changes': changes,
        'header_checksum_recomputed': checksum is not None,
        'header_checksum': checksum,
        'before': before,
        'after': after,
        'diff_byte_count': len(actual_diffs),
    }


def capture_fit_rebuild_reference(path: Path) -> dict[str, Any]:
    """Capture only the FIT relationships needed to undo rebuild-induced damage.

    v0.962 deliberately does not "clean up" the input firmware. A PASS input is
    preserved as PASS. A safely-understood STALE input keeps exactly its
    pre-existing displacement to the same microcode identities. An INVALID or
    unparsable input is never made into a general repair project by this app.
    """
    data = path.read_bytes()
    report = inspect_fit(path)
    if not report.get('supported'):
        return {
            'supported': False,
            'status': report.get('status'),
            'error': report.get('error'),
        }

    table = parse_fit(data)
    report_by_index = {item['index']: item for item in report.get('type1_entries', [])}
    entries: list[dict[str, Any]] = []
    for entry in table.type1_entries:
        item = report_by_index.get(entry.index, {})
        captured: dict[str, Any] = {
            'index': entry.index,
            'address': entry.address,
            'status': item.get('status'),
            'non_address_hex': entry.raw[8:].hex(),
            'target_sha256': None,
            'target_signature': None,
            'target_revision': None,
            'target_processor_flags': None,
            'preexisting_delta': None,
        }
        if report.get('status') in ('PASS', 'STALE'):
            if item.get('status') == 'PASS':
                captured.update(
                    target_sha256=item.get('microcode_sha256'),
                    target_signature=item.get('signature'),
                    target_revision=item.get('revision'),
                    target_processor_flags=item.get('processor_flags'),
                    preexisting_delta=0,
                )
            elif (
                item.get('status') == 'STALE'
                and report.get('repairable')
                and item.get('proposed_microcode_sha256')
                and item.get('proposed_address') is not None
            ):
                target = next(
                    (
                        record for record in report.get('valid_microcodes', [])
                        if record.get('sha256') == item.get('proposed_microcode_sha256')
                    ),
                    None,
                )
                if target is None:
                    raise ValueError(
                        f'could not capture proposed microcode identity for Type-1 entry {entry.index}'
                    )
                captured.update(
                    target_sha256=target.get('sha256'),
                    target_signature=target.get('signature'),
                    target_revision=target.get('revision'),
                    target_processor_flags=target.get('processor_flags'),
                    preexisting_delta=item.get('proposed_address') - entry.address,
                )
        entries.append(captured)

    non_type1 = [
        {'index': entry.index, 'raw_hex': entry.raw.hex()}
        for entry in table.entries
        if entry.index != 0 and entry.entry_type != FIT_MICROCODE_TYPE
    ]
    microcodes = [
        {
            'sha256': item.get('sha256'),
            'signature': item.get('signature'),
            'revision': item.get('revision'),
            'processor_flags': item.get('processor_flags'),
            'total_size': item.get('total_size'),
            'address': item.get('address'),
        }
        for item in report.get('valid_microcodes', [])
    ]
    return {
        'supported': True,
        'status': report.get('status'),
        'pointer_address': table.pointer_address,
        'entry_count': len(table.entries),
        'type1_count': len(table.type1_entries),
        'header_prefix_hex': table.header.raw[:15].hex(),
        'header_checksum_valid_bit': table.header.checksum_valid,
        'type1_entries': entries,
        'non_type1_entries': non_type1,
        'valid_microcodes': microcodes,
    }


def _microcode_identity_tuple(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get('sha256'),
        item.get('signature'),
        item.get('revision'),
        item.get('processor_flags'),
        item.get('total_size'),
    )


def repair_fit_rebuild_damage(path: Path, reference: dict[str, Any]) -> dict[str, Any]:
    """Undo only FIT relationship changes caused after *reference* was captured.

    For a PASS baseline, referenced Type-1 entries follow their exact microcode
    blobs if a firmware-volume rebuild moves them. For a safely repairable STALE
    baseline, the original stale displacement is preserved relative to those
    same microcode identities; the old staleness is therefore neither fixed nor
    made worse. INVALID/unparsable baselines are not generalized into repair
    work: they are accepted only when the rebuild does not require a FIT repair.
    """
    if not reference.get('supported'):
        return {
            'checked': False,
            'passed': True,
            'changed': False,
            'method': 'no-reliable-input-baseline',
            'changes': [],
            'reason': 'input structure was not parsable; no automatic repair attempted',
        }

    before_bytes = path.read_bytes()
    try:
        table = parse_fit(before_bytes)
    except Exception as exc:
        raise ValueError('a previously parsable firmware table became unparsable after rebuilding') from exc

    if table.pointer_address != reference.get('pointer_address'):
        raise ValueError('firmware table pointer moved during rebuilding')
    if len(table.entries) != reference.get('entry_count'):
        raise ValueError('firmware table entry count changed during rebuilding')
    if len(table.type1_entries) != reference.get('type1_count'):
        raise ValueError('firmware Type-1 entry count changed during rebuilding')
    if table.header.raw[:15].hex() != reference.get('header_prefix_hex'):
        raise ValueError('firmware table header changed outside its checksum byte')

    current_non_type1 = {
        entry.index: entry.raw.hex()
        for entry in table.entries
        if entry.index != 0 and entry.entry_type != FIT_MICROCODE_TYPE
    }
    expected_non_type1 = {
        item['index']: item['raw_hex'] for item in reference.get('non_type1_entries', [])
    }
    if current_non_type1 != expected_non_type1:
        raise ValueError('non-microcode firmware-table entries changed during rebuilding')

    entry_by_index = {entry.index: entry for entry in table.type1_entries}
    for item in reference.get('type1_entries', []):
        entry = entry_by_index.get(item['index'])
        if entry is None:
            raise ValueError(f'Type-1 entry {item["index"]} disappeared during rebuilding')
        if entry.raw[8:].hex() != item.get('non_address_hex'):
            raise ValueError(f'Type-1 entry {item["index"]} metadata changed during rebuilding')

    current_microcodes = scan_intel_microcodes(before_bytes)
    current_by_hash: dict[str, list[MicrocodeRecord]] = {}
    for record in current_microcodes:
        current_by_hash.setdefault(record.sha256, []).append(record)

    expected_identity_set = {
        _microcode_identity_tuple(item) for item in reference.get('valid_microcodes', [])
    }
    current_identity_set = {
        (r.sha256, r.signature, r.revision, r.processor_flags, r.total_size)
        for r in current_microcodes
    }
    if expected_identity_set != current_identity_set:
        raise ValueError('microcode contents changed during Secure Boot rebuilding')

    # If the input state was INVALID, do not reinterpret it. We can only accept
    # the rebuild unchanged with respect to all microcode addresses and Type-1
    # addresses. This keeps v0.962 permissive about the input while refusing to
    # invent a repair plan for an already-ambiguous structure.
    if reference.get('status') == 'INVALID':
        before_micro_addresses = {
            item['sha256']: item['address'] for item in reference.get('valid_microcodes', [])
        }
        after_micro_addresses = {record.sha256: record.physical_address for record in current_microcodes}
        type1_unchanged = all(
            entry_by_index[item['index']].address == item['address']
            for item in reference.get('type1_entries', [])
        )
        if before_micro_addresses != after_micro_addresses or not type1_unchanged:
            raise ValueError('pre-existing ambiguous firmware state was additionally changed by rebuilding')
        return {
            'checked': True,
            'passed': True,
            'changed': False,
            'method': 'invalid-input-preserved',
            'changes': [],
            'reason': 'pre-existing invalid state was left untouched',
        }

    work = bytearray(before_bytes)
    changes: list[dict[str, Any]] = []
    for item in reference.get('type1_entries', []):
        entry = entry_by_index[item['index']]
        target_sha = item.get('target_sha256')
        relation_delta = item.get('preexisting_delta')
        if target_sha and relation_delta is not None:
            hits = current_by_hash.get(target_sha, [])
            if len(hits) != 1:
                raise ValueError(
                    f'expected exactly one final microcode matching Type-1 entry {item["index"]}; found {len(hits)}'
                )
            record = hits[0]
            if (
                record.signature != item.get('target_signature')
                or record.revision != item.get('target_revision')
                or record.processor_flags != item.get('target_processor_flags')
            ):
                raise ValueError(f'microcode identity mismatch for Type-1 entry {item["index"]}')
            desired_address = record.physical_address - relation_delta
            if desired_address & 0xF:
                raise ValueError(f'computed Type-1 address for entry {item["index"]} is not aligned')
            try:
                physical_to_offset(desired_address, len(work))
            except ValueError as exc:
                raise ValueError(f'computed Type-1 address for entry {item["index"]} is outside the ROM') from exc
            if entry.address != desired_address:
                work[entry.offset:entry.offset + 8] = desired_address.to_bytes(8, 'little')
                changes.append({
                    'entry_index': item['index'],
                    'old_address': entry.address,
                    'new_address': desired_address,
                    'microcode_sha256': record.sha256,
                    'preserved_preexisting_delta': relation_delta,
                })
        elif entry.address != item.get('address'):
            # EMPTY or otherwise unmapped entries have no trustworthy target
            # identity. Their exact input address must therefore remain intact.
            raise ValueError(f'unmapped Type-1 entry {item["index"]} changed during rebuilding')

    checksum = None
    if changes and table.header.checksum_valid:
        checksum = _recompute_fit_checksum(work, table)
    if changes:
        path.write_bytes(work)

    after_bytes = path.read_bytes()
    after_report = inspect_fit_bytes(after_bytes)
    if after_report.get('status') != reference.get('status'):
        raise RuntimeError(
            f'firmware-table status changed from {reference.get("status")} to {after_report.get("status")} after selective repair'
        )

    after_table = parse_fit(after_bytes)
    after_entries = {entry.index: entry for entry in after_table.type1_entries}
    after_micro_by_hash = {record.sha256: record for record in scan_intel_microcodes(after_bytes)}
    for item in reference.get('type1_entries', []):
        entry = after_entries[item['index']]
        target_sha = item.get('target_sha256')
        relation_delta = item.get('preexisting_delta')
        if target_sha and relation_delta is not None:
            record = after_micro_by_hash[target_sha]
            if record.physical_address - entry.address != relation_delta:
                raise RuntimeError(
                    f'pre-existing Type-1 relationship was not preserved for entry {item["index"]}'
                )
        elif entry.address != item.get('address'):
            raise RuntimeError(f'unmapped Type-1 entry {item["index"]} was not preserved')

    # Verify the repair itself changed only Type-1 address fields and, when
    # required by C_V, the header checksum byte.
    allowed: set[int] = set()
    for change in changes:
        entry = entry_by_index[change['entry_index']]
        allowed.update(range(entry.offset, entry.offset + 8))
    if changes and table.header.checksum_valid:
        allowed.add(table.table_file_offset + 15)
    actual_diffs = [i for i, (a, b) in enumerate(zip(before_bytes, after_bytes)) if a != b]
    unexpected = [i for i in actual_diffs if i not in allowed]
    if unexpected:
        raise RuntimeError(f'selective integrity repair changed unexpected ROM offsets: {unexpected[:16]}')

    return {
        'checked': True,
        'passed': True,
        'changed': bool(changes),
        'method': 'preserve-preexisting-fit-relationship',
        'changes': changes,
        'header_checksum_recomputed': checksum is not None,
        'header_checksum': checksum,
        'reason': 'rebuild-induced pointer changes repaired' if changes else 'no rebuild-induced pointer repair needed',
        'status_before_rebuild': reference.get('status'),
        'status_after_repair': after_report.get('status'),
    }
