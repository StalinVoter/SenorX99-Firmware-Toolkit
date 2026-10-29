Thank you to [MiyConst](https://www.youtube.com/c/Miyconst) for his tireless work promoting the X99 platform and helping the community get more from these motherboards. None of this would have been possible without him.

# SenorX99 v0.93

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

SenorX99 helps you back up and modify the BIOS on supported Chinesium X99 motherboards. It guides you through checking tools, loading a BIOS, choosing modifications and building a new ROM to save or flash.

## Features

- **A guided, six-step interface.** Check files, load or dump a BIOS, review its analysis, choose changes, build a new ROM, then save or flash it. Completed steps receive a green checkmark.
- **CPU detection and recommendations.** Recognises the installed CPU model and suggests appropriate modifications. Custom settings remain available when preparing a BIOS for another computer.
- **Turbo Boost Unlock.** Prepares supported E5-26xx v3 BIOS images for sustained all-core turbo under load, using the selected ser8989 undervolt profile. The recommended profile is −60/−50/−50 mV for Core / Cache / System Agent.
- **Overclocking unlock.** Exposes supported BIOS overclocking controls. Recommendations cover E5-1650 v3, 1660 v3, 1680 v3 and i7-5820K, 5930K, 5960X, 6800K, 6850K, 6900K and 6950X.
- **Secure Boot certificates.** Inspects the ROM's factory certificate defaults and updates outdated defaults to the bundled validated 2023-capable set. Preserves dbx and recognises already-current defaults.
- **Performance-oriented BIOS defaults.** Includes question-mark help listing the exact settings and reasons for changing them.
- **Boot-logo replacement.** Uses the included image or a custom BMP. A custom image must pass a replacement and verification trial before it becomes selectable.
- **Inline dump/flash progress.** Progress bars and FPT console output appear inside the relevant page. Flashing retains the existing two-backup comparison, verification and automatic recovery workflow.
- **External language files.** All main-interface text comes from `languages/en.json`. English and Brazilian Portuguese are included. Click the Union Jack or Brazilian flag above any page heading to switch the entire interface immediately; your ROM and selections stay in place. Other translations can use the same identifiers and placeholders.

Each modification still requires the corresponding supported ROM structures. CPU detection guides recommendations and does not block manual choices for a different target CPU.

## How to use the app

Extract the entire Windows ZIP into a folder, then open **SenorX99.exe**. Keep the executable, language files and other extracted folders together. No separately installed Python or WinGet is required.

1. **Verify that all necessary files are installed.** Download each missing tool. Intel FPT's executable and two DLLs are installed together with one button. Continue when all required files are ready.
2. **Load / Dump ROM.** Browse for an existing `.ROM` or `.BIN` file, drag it onto the drop area, or dump this computer's BIOS. Dumping needs administrator permission. Windows security or another FPT blocker may prevent dumping/flashing while loading, modifying and saving existing ROM files remain available.
3. **ROM analysis.** Review what is already present and which modifications are available, then continue. The app tests logo replacement on a temporary copy.
4. **Configure ROM modifications.** Recommended is selected by default and locks the modification choices, undervolt profile and logo controls. Choose Custom settings to edit them or prepare a ROM for another CPU. Click a question mark to read an explanation. Selecting TBU requires acknowledgement of its v3/v4 CPU reminder before proceeding.
5. **Build new ROM file.** Review the changes, choose the output location if needed, then build and verify. The original BIOS file is never overwritten. Continue after a successful build.
6. **Flash or save modified ROM.** Save a verified copy for later, or explicitly choose to flash this computer. Saving is enough to complete the workflow. Flashing requires a separate confirmation and creates two independently read, matching backups before writing.

Wait until dumping, flashing or recovery finishes before closing the app. The final page reports the outcome and retains the FPT output. If automatic recovery fails, follow the displayed instruction not to restart or remove power.

## Language files

See [languages/README.md](languages/README.md). Translate text values, leaving identifiers and placeholders such as `{cpu}` unchanged. A language switch refreshes labels without clearing the loaded ROM, selected options or completed steps. Raw FPT output and detailed tool diagnostics retain their original wording.

## Building from source

On Windows x64, run **RUN-SETUP-AND-BUILD.cmd**. This creates the pinned Python build environment, runs the source checks, builds the PyInstaller executables and requires the existing packaged runtime/helper validation before public-release packaging.

The command-line firmware engine is retained, including inspection, TBU payload selection, Secure Boot donors, performance defaults, OC compatibility, custom BMPs and automatic startup-information checks.

See [BUILD-VALIDATION.md](BUILD-VALIDATION.md) for the Windows build and release checks.
