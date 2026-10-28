Thank you to [MiyConst](https://www.youtube.com/c/Miyconst) for his tireless work promoting the X99 platform and helping the community get more from these motherboards.

# SenorX99 v0.86

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

SenorX99 is a Windows app for supported Chinesium X99 Xeon motherboards. It helps you back up and modify your BIOS, apply Turbo Boost Unlock, update Secure Boot certificates, change BIOS settings and replace the startup logo.

## Features

- Secure Boot factory-default PK/KEK/db inspection and validated 2023-capable updates, preserving dbx. ROM defaults are distinct from the keys currently enrolled on the running motherboard.
- Turbo Boost Unlock for supported Xeon E5-26xx v3 CPUs, validated undervolt payloads, microcode handling and mirrored C-state defaults. Selecting TBU opens a CPU-compatibility reminder: applying it with an E5 v4 CPU may prevent POST. Continuing requires an explicit Yes; No is selected by default.
- Performance-oriented BIOS defaults and unlocked-multiplier OC profiles where the specific firmware layout is supported, including classic and compact XD3/XD4 layouts. The defaults' **What does this mean?** explanation lists every setting, its target value and its purpose, including power-use and ECC-scrubbing tradeoffs. Secure Boot shows **Already up to date** when the ROM already contains the required current factory keys.
- SenorSpace selected automatically for each detected 4-bit/8-bit splash format, including the full-resolution 800 × 600 4-bit asset. Custom BMP selection, multiple-logo handling, test-fit and final pixel verification are retained. When a temporary-copy build verifies automatic logo replacement for the loaded ROM, Replace the firmware boot logo is checked by default. Uncheck it to keep the current logo; that choice survives tool rechecks. A missing tool or unsuccessful compatibility check leaves it unchecked.
- FIT validation/repair, mutation containment and final ROM validation.
- BIOS dump and a complete flash workflow with two independent, byte-identical backups, permanent backup retention, target-size validation, final verification and automatic recovery where required.

Support is determined separately for each feature from the actual firmware structures. The existing XD3/XD4, MACHINIST V9S, Qiyida K9S and Jginue-family mappings are retained; a board name alone does not establish support for every operation. See `BOARD-EVIDENCE.json` and `V9S-SUPPORT-MAPPING.txt` for the existing mapping evidence.

## How to use the app

1. **Download the Windows release.** Get **SenorX99-v0.86-Windows-x64.zip** from the [Releases page](https://github.com/StalinVoter/SenorX99-Firmware-Toolkit/releases/latest).
2. **Extract the whole ZIP**, then open **SenorX99.exe** from the extracted folder. Keep all the extracted files together. Start the app normally; Windows will ask for administrator permission when you use the BIOS dump or flash functions.
3. **Download the missing tools.** On **Overview**, click **Download** beside each missing tool. The app downloads and checks the files for you. Intel FPT's files are installed together with one button. You do not need Python or WinGet. All required tools must be installed before you can load or dump a BIOS file. The missing-tools section disappears when everything is ready. You can check the tools again under **Required tools → Recheck files**.
4. **Open or save a copy of your BIOS.** Use **Load / Dump ROM** to open an existing `.ROM` or `.BIN` BIOS file, or choose **Dump BIOS** to save a copy of the BIOS installed on this computer. You can also drag an existing BIOS file onto Overview. **Load / Dump ROM** stays available when you want to open another file.
5. **Choose your changes.** Review the results on **Overview**, then select the changes you want under **Create ROM**. Use **What does this mean?** for explanations. Boot-logo replacement is selected automatically when its compatibility check passes; uncheck it to keep your existing logo.
6. **Create the modified BIOS file.** Choose where to save it and start the build. **Builds** shows progress and the result. The app creates a new file and leaves your original BIOS file unchanged.
7. **Install the modified BIOS when you are ready.** Use **Flash BIOS** or accept the offer shown after a successful build. Before writing anything, the app saves two copies of your current BIOS and checks that they match. It then checks the written BIOS and can attempt to restore the backup if writing fails.

Wait for an active operation to finish before opening another BIOS file. **Technical details** contains the inspection results and diagnostic information if something needs investigating.

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
