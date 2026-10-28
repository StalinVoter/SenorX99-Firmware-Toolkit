# Gen2-v2.6.3 — compact XD3/XD4 OC support

Adds `compact-7bffc` alongside `classic-7c084`. Main-AMITSE family recognition,
setup layout and compact control headers must agree before changes are allowed.
The GUI now offers the OC profile for supported compact images.

## Verified byte mapping

Offsets below are in decompressed AMITSESetupData, not the ROM file.

| Item | Classic | Compact | Desired |
|---|---|---|---|
| OverClocking Feature access | 0x133BC | 0x1337C | 05 |
| XE Ratio Limit access | 0x5B0AC | 0x5B024 | 05 |
| Overclocking Lock failsafe | 0x5B1D8 | 0x5B150 | 01 |
| Overclocking Lock optimal | 0x5B1D9 | 0x5B151 | 01 |
| PP0 current-control access | 0x5BF6C | 0x5BEE4 | 05 |
| Current Config optimal | 0x5C6E9 | 0x5C661 | 01 |
| Current Limitation optimal low | 0x5C732 | 0x5C6AA | 00 |
| Current Limitation optimal high | 0x5C733 | 0x5C6AB | 08 |

Menu identities are matched through the existing page/form identifiers and
control headers. Compact IntelRCSetup IFR corroborates the default controls:
Overclocking Lock: prompt 0x1280, help 0x1281, question 0x0D90,
IntelSetup 0x13FD; Current Config: prompt 0x121F, help 0x1220,
question 0x0DCC, IntelSetup 0x162D; Current Limitation: prompt 0x1242,
help 0x1243, question 0x0DCD, IntelSetup 0x13D3.

On BIOS-Dump-20261006-014633x99xd4-novrm.ROM, all three access flags and optimal
OC defaults already match. Only Overclocking Lock failsafe changes, 00 -> 01
(Enable -> Disable). Primary and NV-Internal OC settings require no changes.
Other failsafe defaults are preserved, including the existing Current Config
and Current Limitation failsafe values.

## Validation

72 tests passed; 53 skipped for unavailable environment/fixture dependencies.
Twelve new checks cover both layouts, exact payload changes, idempotence,
unexpected values and altered control identities. Tests use real extracted
AMITSESetupData FFS fixtures. The supplied complete compact dump also passed
primary/NV no-op checks and a Python LZMA rebuild/reinspection: the OC profile
then reports complete, with exactly one decompressed setup byte changed.
This rebuild validates the generated payload and inspection, not Windows
UEFIReplace execution or physical motherboard behavior. No ROM is distributed
from that test. Windows executables must be built on Windows.

## Build

The full tools set is included, including the previously supplied FPT and DLLs.
Run RUN-SETUP-AND-BUILD-Gen2-v2.6.3.cmd. The setup/build logic is unchanged apart
from release naming. GUI and CLI output will be in dist.
