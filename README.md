Thank you to [MiyConst](https://www.youtube.com/c/Miyconst) for his tireless work promoting the X99 platform and helping the community get more from these motherboards.

# SenorX99 v0.86

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

SenorX99 inspects, modifies, dumps and flashes supported AMI X99 firmware. It is the renamed continuation of X99 Firmware Toolkit UX Gen2 v2.6.3, with the standalone SecureBoot patcher's runtime, tools and administrator-helper improvements integrated into the full Toolkit.

## Using the Windows release

Extract the complete Windows ZIP to a writable folder, then start **SenorX99.exe normally**. Keep **SenorX99-CLI.exe** beside it; BIOS access uses this companion to request administrator approval. Inspection, patching and tool installation run in the normal GUI.

Overview displays every missing or incorrect tool with a **Download** button. Intel FPT is one download action that installs its executable and both required DLLs. UEFIReplace, MMTool and ChangeLogo each have their own automatic download action. The missing-tools section disappears when all required files are ready. The **Required tools** tab retains the full inventory and **Recheck files**.

Downloads are checked by exact size and SHA-256 before installation. No WinGet, separate Python installation or manual tool import is required for the Windows release. Public packages exclude the six external binaries. All six required tool files must be present and verified before loading or dumping a ROM, choosing changes, building or flashing. This also applies to command-line inspection and patching.

Once all tools are ready, drop one local `.ROM` or `.BIN` file onto the opening panel or main window, or use **Load / Dump ROM** to open a file or dump the installed BIOS. This sidebar tab stays available after a ROM is loaded, so you can select another source. Overview explains the detected features. Create ROM selects changes, Builds shows progress and results, and Technical details contains inspection evidence. Source selection is locked while an operation is active.

## Firmware features

- Secure Boot factory-default PK/KEK/db inspection and validated 2023-capable updates, preserving dbx. ROM defaults are distinct from the keys currently enrolled on the running motherboard.
- Turbo Boost Unlock for supported Xeon E5-26xx v3 CPUs, validated undervolt payloads, microcode handling and mirrored C-state defaults. Selecting TBU opens a CPU-compatibility reminder: applying it with an E5 v4 CPU may prevent POST. Continuing requires an explicit Yes; No is selected by default.
- Performance-oriented BIOS defaults and unlocked-multiplier OC profiles where the specific firmware layout is supported, including classic and compact XD3/XD4 layouts. The defaults' **What does this mean?** explanation lists every setting, its target value and its purpose, including power-use and ECC-scrubbing tradeoffs. Secure Boot shows **Already up to date** when the ROM already contains the required current factory keys.
- SenorSpace selected automatically for each detected 4-bit/8-bit splash format, including the full-resolution 800 × 600 4-bit asset. Custom BMP selection, multiple-logo handling, test-fit and final pixel verification are retained. When a temporary-copy build verifies automatic logo replacement for the loaded ROM, Replace the firmware boot logo is checked by default. Uncheck it to keep the current logo; that choice survives tool rechecks. A missing tool or unsuccessful compatibility check leaves it unchecked.
- FIT validation/repair, mutation containment and final ROM validation.
- BIOS dump and a complete flash workflow with two independent, byte-identical backups, permanent backup retention, target-size validation, final verification and automatic recovery where required.

Support is determined separately for each feature from the actual firmware structures. The existing XD3/XD4, MACHINIST V9S, Qiyida K9S and Jginue-family mappings are retained; a board name alone does not establish support for every operation. See `BOARD-EVIDENCE.json` and `V9S-SUPPORT-MAPPING.txt` for the existing mapping evidence.

## What changed in v0.86

An already-installed SenorSpace logo now passes the full logo check instead of being wrongly rejected because the ROM did not need changing. Both 4-bit and 8-bit formats, default logos and numbered logo entries are still checked. A replacement that was needed but produced no change still fails.

Logo-check failures now show their reason directly in Overview. The CPU startup/FIT explanation uses plain language. Successful BIOS dumps clearly say when the window can be closed, and the redundant download footer has been removed. Source builds use a project-local test directory to avoid a locked shared pytest folder. Source ZIPs are completed before publication and use standard, uncompressed ZIP entries.

See `RELEASE-NOTES-v0.86.md` for this release. The runtime and helper improvements from v0.85 are retained.

## What changed in v0.85

The GUI stays at normal privileges for Explorer drag-and-drop. Only BIOS access requests elevation, and the entire flash/backup/recovery operation runs inside one helper. Caller-created result/event files are updated in place, carrying the standalone v0.967 reply-access fix. Helper launch, cancellation, reply-access, operation and cleanup errors retain distinct diagnostics. Revoked FPT driver certificate error `0x800B010C` receives its own diagnosis.

The app checks and repairs exact external tools, isolates their launch environment from its private Qt runtime, logs before importing Qt, and falls back to another writable log directory when necessary. Packaged launches verify native-file hashes, native module paths and Qt/PySide/Shiboken versions.

After a successful build, the original **ROM created** question is followed by a second flash acknowledgement, both defaulting to No. The approved Secure Boot-specific wording is used only for a Secure Boot-only build from an unchanged BIOS dump made by this running app; arbitrary or combined modifications receive the general target/backup/recovery warning.

A separate follow-up corrects the misleading “2011 only” db label when some 2023 certificates are present. It reports a partial 2023 set without changing the full-set update policy.

## Building from source on Windows x64

Run **RUN-SETUP-AND-BUILD.cmd**. The setup uses the pinned CPython **3.13.14 x64** interpreter, verifies the official installer when installation is needed, creates a clean `.build-venv`, and installs the hash-locked dependency set. It does not require globally installed Python packages or external firmware utilities.

The build runs source tests and validates mandatory bundled resources, builds both executables, inspects the finished EXE contents and execution manifests, stages all Toolkit assets and runtime notices, and runs the packaged GUI, CLI, conflicting-DLL and harmless elevated-helper tests. The helper test may request UAC; it does not access BIOS hardware. Packaging rejects missing/failed/stale reports or changed executables.

Successful output:

```text
SenorX99-v0.86-Windows-x64.zip
SenorX99-v0.86-Windows-x64.zip.sha256
```

The source archive contains no Windows executables. Building and running the actual Windows EXEs is a separate validation step; Linux source tests do not establish Windows DLL isolation, Explorer/UAC behavior or physical flash success. Development evidence and remaining checks are recorded in `VALIDATION.md`.

`TEST-ELEVATED-HELPER.cmd` tests the production administrator handoff without BIOS access. Start it normally. `TEST-WINDOWS-SANDBOX.ps1` optionally runs the packaged tests in an offline Windows Sandbox.

## Command line

```powershell
SenorX99-CLI.exe --version
SenorX99-CLI.exe inspect current.ROM --report
SenorX99-CLI.exe patch current.ROM --secureboot-donor secureboot_donors -o updated.ROM
SenorX99-CLI.exe patch current.ROM --optimized-settings --unlocked-multiplier-oc -o modified.ROM
SenorX99-CLI.exe patch current.ROM --senorspace -o logo.ROM
```

All prior public patch options are retained, including Secure Boot mode, TBU driver selection, custom splash, FIT options and preserved stages. Source entry points are `SenorX99-GUI.py` and `SenorX99-CLI.py`.

Logs normally go to `LOGS` beside the app. Existing `X99_TOOLKIT_HOME` and `X99_TOOLKIT_BACKUP_DIR` overrides and the prior permanent backup location are preserved. Third-party notices ship under `licenses`.
