from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import struct
import uuid

@dataclass(frozen=True)
class FfsFile:
    offset: int
    size: int
    guid: str
    ffs_type: int
    attributes: int
    state: int
    data: bytes
    header_size: int = 24
    @property
    def sha256(self) -> str:
        return sha256(self.data).hexdigest()

@dataclass(frozen=True)
class MicrocodeHit:
    offset: int
    revision: int
    date_raw: int
    signature: int
    flags: int
    total_size: int

@dataclass(frozen=True)
class IntelSetupRecord:
    nvar_offset: int
    entry_size: int
    name_offset: int
    data_offset: int
    payload_size: int
    values: dict[int, int]

def guid_bytes_le(text: str) -> bytes:
    return uuid.UUID(text).bytes_le

def sha256_file(path: Path) -> str:
    h = sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def parse_ffs_at(buf: bytes, offset: int, expected_guid: str | None = None) -> FfsFile:
    if offset < 0 or offset + 24 > len(buf):
        raise ValueError('truncated FFS header')
    guid = str(uuid.UUID(bytes_le=bytes(buf[offset:offset + 16]))).upper()
    if expected_guid and guid != expected_guid.upper():
        raise ValueError(f'FFS GUID mismatch: expected {expected_guid}, got {guid}')
    ffs_type = buf[offset + 0x12]
    attrs = buf[offset + 0x13]
    size24 = int.from_bytes(buf[offset + 0x14:offset + 0x17], 'little')
    state = buf[offset + 0x17]
    size = size24
    header_size = 24
    if attrs & 0x01 and size24 == 0xFFFFFF:
        if offset + 32 > len(buf):
            raise ValueError('truncated extended FFS header')
        size = int.from_bytes(buf[offset + 24:offset + 32], 'little')
        header_size = 32
    if size < header_size or size > 8 * 1024 * 1024 or offset + size > len(buf):
        raise ValueError(f'implausible FFS size 0x{size:X}')
    return FfsFile(offset, size, guid, ffs_type, attrs, state, bytes(buf[offset:offset + size]), header_size)

def find_ffs_by_guid(buf: bytes, guid: str) -> list[FfsFile]:
    pattern = guid_bytes_le(guid)
    result: list[FfsFile] = []
    start = 0
    while True:
        pos = buf.find(pattern, start)
        if pos < 0:
            break
        try:
            result.append(parse_ffs_at(buf, pos, guid))
        except ValueError:
            pass
        start = pos + 1
    return result

def scan_06f2_microcode(buf: bytes) -> list[MicrocodeHit]:
    result: list[MicrocodeHit] = []
    needle = b'\x01\x00\x00\x00'
    search = 0
    while True:
        off = buf.find(needle, search)
        if off < 0:
            break
        search = off + 1
        if off & 0x3 or off + 48 > len(buf):
            continue
        try:
            hv, rev, date, sig, checksum, loader, flags, data_size, total_size = struct.unpack_from('<9I', buf, off)
        except struct.error:
            continue
        if hv != 1 or loader != 1 or (sig & 0xFFFF) != 0x06F2:
            continue
        total = 2048 if total_size == 0 else total_size
        payload_size = 2000 if data_size == 0 else data_size
        if total < 48 or total > 0x40000 or total % 4 or off + total > len(buf) or payload_size > total - 48:
            continue
        words = struct.unpack_from(f'<{total // 4}I', buf, off)
        if sum(words) & 0xFFFFFFFF:
            continue
        result.append(MicrocodeHit(off, rev, date, sig, flags, total))
    return result


def _find_all(buf: bytes, needle: bytes):
    start = 0
    while True:
        pos = buf.find(needle, start)
        if pos < 0:
            return
        yield pos
        start = pos + 1

def find_intelsetup_records(buf: bytes) -> list[IntelSetupRecord]:
    from .constants import CSTATE_TARGETS
    result: list[IntelSetupRecord] = []
    name = b'IntelSetup\x00'
    for name_pos in _find_all(buf, name):
        nvar = buf.rfind(b'NVAR', max(0, name_pos - 40), name_pos)
        if nvar < 0 or nvar + 10 > len(buf):
            continue
        entry_size = int.from_bytes(buf[nvar + 4:nvar + 6], 'little')
        if entry_size < 32 or nvar + entry_size > len(buf):
            continue
        data_offset = name_pos + len(name)
        payload_size = nvar + entry_size - data_offset
        if payload_size <= max(CSTATE_TARGETS):
            continue
        values = {offset: buf[data_offset + offset] for offset in CSTATE_TARGETS}
        if values[0x69] not in (0, 1) or values[0x6C] not in (0, 1, 2, 3) or values[0x6D] not in (0, 1) or values[0x6E] not in (0, 1):
            continue
        result.append(IntelSetupRecord(nvar, entry_size, name_pos, data_offset, payload_size, values))
    return sorted(result, key=lambda record: record.nvar_offset)



def intelsetup_guid_index(buf: bytes, record: IntelSetupRecord) -> int | None:
    # AMI NVAR header: signature(4), size(2), next(3), attributes(1),
    # then an 8-bit GUID-table index when the GUID is not inline. All validated
    # IntelSetup records in our XD3/V9S families use this form (attrs 0x82).
    pos = record.nvar_offset + 10
    if pos >= len(buf):
        return None
    return buf[pos]


def select_intelsetup_record(
    buf: bytes,
    expected_payload_size: int,
    *,
    guid_index: int = 0,
) -> IntelSetupRecord:
    records = [
        record for record in find_intelsetup_records(buf)
        if record.payload_size == expected_payload_size
        and intelsetup_guid_index(buf, record) == guid_index
    ]
    if not records:
        raise ValueError(
            f"No IntelSetup NVAR with payload size {expected_payload_size} and GUID index {guid_index} found"
        )
    # Full 16 MiB images contain an active and a backup NVAR store. The active
    # store is earlier in the image; AMIBCP may compact/reorder records inside
    # that store, so select by NVAR GUID index and then the earliest store copy,
    # never by an absolute record offset.
    return min(records, key=lambda record: record.nvar_offset)


def select_primary_intelsetup_record(buf: bytes) -> IntelSetupRecord:
    from .families import classify_amitse_family
    family = classify_amitse_family(buf)
    size = family.get("intelsetup_payload_size")
    if not family.get("known") or not isinstance(size, int):
        raise ValueError(
            "Main AMITSE family is not validated for an IntelSetup schema; refusing to guess"
        )
    return select_intelsetup_record(buf, size, guid_index=0)

def inspect_primary_cstates(path: Path) -> dict:
    from .constants import CSTATE_TARGETS
    data = path.read_bytes()
    try:
        record = select_primary_intelsetup_record(data)
    except Exception as exc:
        return {
            'supported': False,
            'matches': False,
            'error': f'{type(exc).__name__}: {exc}',
            'records': len(find_intelsetup_records(data)),
        }
    values = {offset: record.values[offset] for offset in CSTATE_TARGETS}
    return {
        'supported': True,
        'matches': all(values[offset] == value for offset, value in CSTATE_TARGETS.items()),
        'record_offset': record.nvar_offset,
        'data_offset': record.data_offset,
        'payload_size': record.payload_size,
        'guid_index': intelsetup_guid_index(data, record),
        'values': {f'0x{offset:04X}': value for offset, value in values.items()},
    }

def patch_primary_cstates(path: Path) -> dict:
    from .constants import CSTATE_TARGETS
    buf = bytearray(path.read_bytes())
    record = select_primary_intelsetup_record(bytes(buf))
    before = {f'0x{offset:04X}': buf[record.data_offset + offset] for offset in CSTATE_TARGETS}
    for offset, value in CSTATE_TARGETS.items():
        buf[record.data_offset + offset] = value
    path.write_bytes(buf)
    after = {f'0x{offset:04X}': value for offset, value in CSTATE_TARGETS.items()}
    check = inspect_primary_cstates(path)
    if not check['matches']:
        raise IOError('primary C-state post-write verification failed')
    return {
        'record_offset': record.nvar_offset,
        'data_offset': record.data_offset,
        'payload_size': record.payload_size,
        'guid_index': intelsetup_guid_index(bytes(buf), record),
        'before': before,
        'after': after,
    }


@dataclass(frozen=True)
class FirmwareVolume:
    offset: int
    size: int
    header_length: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def find_enclosing_fv(buf: bytes, target_offset: int) -> FirmwareVolume | None:
    """Return the outermost structurally-valid FV containing target_offset.

    UEFIReplace is allowed to rebuild padding/free-space *inside* the containing
    firmware volume. It must not alter bytes outside the outermost containing FV.
    The standard EFI_FIRMWARE_VOLUME_HEADER has FvLength at +0x20, signature
    '_FVH' at +0x28 and HeaderLength at +0x30.
    """
    candidates: list[FirmwareVolume] = []
    search = 0
    while True:
        sig = buf.find(b'_FVH', search)
        if sig < 0:
            break
        start = sig - 0x28
        search = sig + 1
        if start < 0 or start + 0x38 > len(buf):
            continue
        size = int.from_bytes(buf[start + 0x20:start + 0x28], 'little')
        header_len = int.from_bytes(buf[start + 0x30:start + 0x32], 'little')
        if size < 0x38 or header_len < 0x38 or header_len > size:
            continue
        end = start + size
        if end > len(buf):
            continue
        if start <= target_offset < end:
            candidates.append(FirmwareVolume(start, size, header_len))
    if not candidates:
        return None
    # Outermost FV gives nested-volume rebuilds room while still enforcing a
    # strong mutation boundary around the firmware region UEFIReplace touched.
    return max(candidates, key=lambda fv: fv.size)
