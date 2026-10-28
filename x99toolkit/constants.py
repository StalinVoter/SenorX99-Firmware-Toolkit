from __future__ import annotations

TBU_TARGET_GUID = "271DD6F2-54CB-45E6-8585-8C923C1AC706"
SER8989_MARKER_ASCII = b"ser8989_S3TurboHack"
SER8989_MARKER_UTF16LE = "ser8989_S3TurboHack".encode("utf-16le")
UV_FIELD_OFFSETS = {"Core": 0x1CE8, "Cache": 0x1CDA, "System Agent": 0x1CF6}

SECURE_BOOT_GUIDS = {
    "PkVar": "CC0F8A3F-3DEA-4376-9679-5426BA0A907E",
    "KekVar": "9FE7DE69-0AEA-470A-B50A-139813649189",
    "dbVar": "FBF95065-427F-47B3-8077-D13C60710998",
    "dbxVar": "9D7A05E9-F740-44C3-858B-75586A8F9C8E",
}
LZMA_CUSTOM_DECOMPRESS_GUID = "EE4E5898-3914-4259-9D6E-DC7BD79403CF"

CERT_STRINGS = {
    "ami_test_pk": "DO NOT TRUST - AMI Test PK",
    "asrock_pk": "ASRock Inc.",
    "kek_2011": "Microsoft Corporation KEK CA 2011",
    "kek_2023": "Microsoft Corporation KEK 2K CA 2023",
    "db_uefi_2011": "Microsoft Corporation UEFI CA 2011",
    "db_windows_2011": "Microsoft Windows Production PCA 2011",
    "db_windows_2023": "Windows UEFI CA 2023",
    "db_microsoft_2023": "Microsoft UEFI CA 2023",
    "db_optionrom_2023": "Microsoft Option ROM UEFI CA 2023",
}

INTELSETUP_EXPECTED_PAYLOAD_SIZE = 6124
CSTATE_TARGETS = {0x0069: 1, 0x006C: 1, 0x006D: 1, 0x006E: 0}
NV_INTERNAL_GUID = "9221315B-30BB-46B5-813E-1B1BF4712BD3"
AMITSE_SETUP_DATA_GUID = "FE612B72-203C-47B1-8560-A66D946EB371"
NV_INTERNAL_RAW_SECTION_TYPE = 0x19
AMITSE_RAW_SECTION_TYPE = 0x18

# AMITSESetupData does not use one invariant byte neighborhood across all
# X99-XD3 firmware revisions. UX v1 therefore locates the CPU C-state block
# as a sequence of 0x48-byte AMITSE records and then applies a layout-specific
# Package C State default edit that has been independently diffed against
# AMIBCP output for each supported family.
AMITSE_RECORD_SIZE = 0x48
AMITSE_RECORD_TAIL_MARKER = 0x00000055
AMITSE_RECORD_TRAILER_A = 0x00AF0006
AMITSE_RECORD_TRAILER_B = 0x0000FFFF

# Each descriptor identifies the same six-record C-state control block using
# stable structural fields rather than a full fixed byte signature. The target
# is the third record in the block. patch_offsets are relative to that record.
#
# "classic" matches the 0x7C084 decompressed AMITSE family.
# "compact" matches the 0x7BFFC decompressed AMITSE family seen in later XD3
# dumps. The two required bytes were established by diffing stock vs AMIBCP-
# modified ROMs from the same family.
AMITSE_CSTATE_LAYOUTS = (
    {
        "name": "classic-7c084",
        "first_qid": 0x0DAB,
        "first_tag": 0x0006103F,
        "patch_offsets": (0x25,),
        "desired_values": (0x01,),
    },
    {
        "name": "compact-7bffc",
        "first_qid": 0x0DAA,
        "first_tag": 0x0006103E,
        "patch_offsets": (0x24, 0x25),
        "desired_values": (0x01, 0x01),
    },
)

# MACHINIST X99-V9S reference family (2026-08-26).
# AMITSESetupData stock and both controlled AMIBCP reference ROMs normalize to
# this exact hash when only the validated C-state/optimized-default bytes below
# are restored to their stock values. This lets the toolkit recognize its own
# C-state/OPT combinations without accepting unrelated 0x791A0 layouts.
V9S_AMITSE_DECOMPRESSED_SIZE = 0x791A0
V9S_AMITSE_NORMALIZED_SHA256 = "1404c394363afa2eb0c969678c5fb878c6d4906979cc6c96ae9b70ae5ff864fc"
V9S_AMITSE_CSTATE_PATCHES = (
    (0x58E38, 0, 1),
    (0x58E39, 0, 1),
    (0x58EC8, 1, 0),
    (0x58EC9, 1, 0),
)
V9S_AMITSE_OPT_PATCHES = (
    (0x138F0, 1, 0),
    (0x138F1, 1, 0),
    (0x13938, 1, 0),
    (0x13939, 1, 0),
    (0x14EF0, 1, 0),
    (0x14EF1, 1, 0),
    (0x14F38, 1, 0),
    (0x14F39, 1, 0),
    (0x53468, 5, 0),
    (0x53469, 5, 0),
    (0x53538, 2, 0),
    (0x53539, 2, 0),
    (0x54250, 1, 0),
    (0x54251, 1, 0),
    (0x542E8, 1, 0),
    (0x542E9, 1, 0),
    (0x59020, 1, 0),
    (0x59021, 1, 0),
    (0x59510, 7, 0),
    (0x59511, 7, 0),
)

# Exact validated Secure Boot update payloads bundled with this build.
BUNDLED_DONOR_SHA256 = {
    "PkVar": "6ad2c119c70b4325ad29138c65d0ddafd09cdba299d34fdcb4e3f2aa01b1e7dd",
    "KekVar": "6b8bcfd771d6efcba8459d62d2a8687a252671d66ea0f22ee6aa2140ac8e802a",
    "dbVar": "744662e5b2c92171cf777d36a6b4e3c59d2a6d99ffe4914a41f6a7a2a24f779a",
}
UEFIREPLACE_VERSION = "0.28.0"
UEFIREPLACE_SHA256 = "ab05d53fcac19651818f4ee4505813b10badec7a10d141836fff3bba8964ed8b"
UEFIREPLACE_RELEASE_URL = "https://github.com/LongSoft/UEFITool/releases/tag/0.28.0"
UEFIREPLACE_DOWNLOAD_URL = "https://github.com/LongSoft/UEFITool/releases/download/0.28.0/UEFIReplace_0.28.0_win32.zip"

K9S_AMITSE_NORMALIZED_SHA256 = 'c7a38df0eb53f56433e60a39a613e786a01ffd95d0018df939c368dee475d237'

PATCH_TARGETS = ("PkVar", "KekVar", "dbVar")

EFI_CERT_X509_GUID = "A5C059A1-94E4-4AA7-87B5-AB155C2BF072"

EFI_CERT_SHA256_GUID = "C1C41626-504C-4092-ACA9-41F936934328"

EFI_CERT_TYPE_PKCS7_GUID = "4AAFD29D-68DF-49EE-8AA9-347D375665A7"

UEFIREPLACE_ARCHIVE_SHA256 = '77c2dff86186c9008f94bb0756ffc09d946a0381a2ff7132f11e8e23e94be491'

FPT_VERSION = '9.1.10.1000'
FPT_SHA256 = 'b7e942e903f5f6bba84c3e9294edc8cc097c1173ca249a41a4cca1ab9e15a697'
FPT_FILE_SHA256 = {
    'fptw64.exe': FPT_SHA256,
    'pmxdll32e.DLL': '1882c65036142fee6b89042e2b7bf383aaa8e151b65942f5b74c03ab5cc4e5f9',
    'idrvdll32e.DLL': '65a34921ede066338d9717ddfa134b54294bb81509d40061d2f4bc441fc392c5',
}
FPT_SOURCE_COMMIT = '698d2f69d952e36dbb20ac41d3fa858c2947168e'
FPT_SOURCE_FOLDER_URL = (
    'https://github.com/CE1CECL/IntelCSTools/tree/' + FPT_SOURCE_COMMIT + '/'
    'ME%20System%20Tools%20v9.1%20r7/Flash%20Programming%20Tool/WIN64'
)
