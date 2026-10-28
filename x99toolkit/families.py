from __future__ import annotations

from hashlib import sha256
import lzma
import uuid

from .constants import LZMA_CUSTOM_DECOMPRESS_GUID
from .firmware import find_ffs_by_guid

AMITSE_DRIVER_GUID = "B1DA0ADF-4F77-4070-A88E-BFFE1C60529A"

# Exact decompressed main-AMITSE fingerprints. Main AMITSE is not modified by
# the toolkit's C-state/default-key/optimized/OC/logo/FIT operations, which makes
# it a useful fail-closed family discriminator for selecting board-specific
# Setup/NVAR schemas.
AMITSE_FAMILIES = {
    # Exact supplied Jginue ARGB executable.
    (0x4CBB6, "d6951b5e61ee0315aa6a1f15f20de15542c6e22ee9f4327bf4825ef3cb73874d"): {
        "family": "jginue-argb", "board_family": "jginue-argb",
        "intelsetup_payload_size": 6124,
    },
    (0x4C996, "00114c110551b6b0f13fc0b2f4c5f36a80143bc369039511edfe5a3b0bd6f868"): {
        "family": "xd3-compact",
        "board_family": "xd3-compact",
        "intelsetup_payload_size": 6124,
    },
    (0x4CBB6, "613cecf5f6ca4ffb54426cb7623c4b2d68dd4607f4cecd56003f03f2c78959b8"): {
        "family": "xd3-xd4-classic",
        "board_family": "xd3-classic",
        "intelsetup_payload_size": 6124,
    },
    # MACHINIST X99-V9S reference supplied 2026-08-26.
    (0x4BB56, "c335358b38c9710abad345c7d06cb25be357e9341198a2e2ef005aaeb02eed67"): {
        "family": "x99-v9s",
        "board_family": "x99-v9s",
        "intelsetup_payload_size": 6090,
    },
}


def _decompress_main_amitse(rom: bytes) -> dict:
    hits = find_ffs_by_guid(rom, AMITSE_DRIVER_GUID)
    if len(hits) != 1:
        raise ValueError(
            f"expected exactly one main AMITSE FFS {AMITSE_DRIVER_GUID}, found {len(hits)}"
        )
    ffs = hits[0]
    data = ffs.data
    section = 24
    if len(data) < section + 24:
        raise ValueError("main AMITSE FFS is too small for its GUID-defined LZMA section")
    section_size = int.from_bytes(data[section:section + 3], "little")
    section_type = data[section + 3]
    section_guid = str(uuid.UUID(bytes_le=data[section + 4:section + 20])).upper()
    data_offset = int.from_bytes(data[section + 20:section + 22], "little")
    if section_type != 0x02:
        raise ValueError(
            f"main AMITSE outer section type is 0x{section_type:02X}, expected GUID-defined 0x02"
        )
    if section_guid != LZMA_CUSTOM_DECOMPRESS_GUID:
        raise ValueError(
            f"main AMITSE compression GUID is {section_guid}, expected {LZMA_CUSTOM_DECOMPRESS_GUID}"
        )
    if data_offset < 24 or section_size > len(data) - section or section + data_offset >= len(data):
        raise ValueError("main AMITSE GUID-defined section bounds are invalid")
    payload = data[section + data_offset:section + section_size]
    decompressed = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
    digest = sha256(decompressed).hexdigest()
    return {
        "ffs_offset": ffs.offset,
        "ffs_size": ffs.size,
        "ffs_sha256": ffs.sha256,
        "decompressed_size": len(decompressed),
        "decompressed_sha256": digest,
    }


def classify_amitse_family(rom: bytes) -> dict:
    info = _decompress_main_amitse(rom)
    family = AMITSE_FAMILIES.get((info["decompressed_size"], info["decompressed_sha256"]))
    if family is None:
        return {
            **info,
            "family": "unknown",
            "board_family": "unknown",
            "intelsetup_payload_size": None,
            "known": False,
        }
    return {**info, **family, "known": True}
