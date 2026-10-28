# SenorX99 v0.86 validation

These results were obtained on Linux on 28 October 2026. They do not establish that the compiled Windows application, Explorer/UAC interaction or physical BIOS access works on a particular motherboard.

## Checks performed for v0.86

- Full source suite: **271 passed, 52 skipped, four logo subtests passed**. The supplied two XD3 stock ROMs and the available Windows native binary corpus were used where applicable. Skips require missing board-reference ROMs, Windows token/ACL support or PowerShell. One expected duplicate-ZIP-member warning comes from a deliberately malformed installer fixture. See `validation/source-tests-linux-v0.86.xml`.
- The exact already-present-logo failure was reproduced before the correction. Tests now verify unchanged ROMs are accepted only after the requested 4-bit/8-bit default and numbered images match. A required replacement that does not change the ROM still fails. Pixel, image-format and final-entry checks remain active.
- GUI tests verify an already-installed SenorSpace status, directly visible logo-check failure reasons, plain FIT correction status, the hidden download footer and working download-progress messages. Successful dump completion text appears only after the worker stops; a failed dump does not show the success message.
- Tests verify a denied shared pytest temporary directory reproduces the reported setup error and the builder's project-local `--basetemp` succeeds. The user's earlier Windows report contained 75 passed, 34 skipped and 203 setup errors from that single denied directory, with zero assertion failures; those blocked tests had not run.
- Source-packaging tests verify a closed, complete standard ZIP, source checksums and rejection of a bad inventory before an archive is published. The delivered ZIP is also independently checked with `unzip`, then downloaded back after saving and checked for byte identity and every source checksum.
- Changed screens were rendered and visually inspected with Qt offscreen: the FIT card, installed-logo state, visible failure reason, tools page without its idle footer and completed BIOS dump dialog. These views used pure inspection or simulated worker results. No firmware utilities or BIOS hardware functions were executed.
- The CLI reports **SenorX99 v0.86**. Bundled build inputs pass the resource manifest.
- All 14 bundled resources, 13 unchanged firmware modules and the protected FPT backup/report/recovery function ASTs were rechecked against the original Toolkit baseline. `splash.py` has the documented v0.86 already-present-logo correction. See `validation/port-preservation.json`.

## Retained v0.85 evidence

The pinned sources, exact download identities for all six utilities, successful prior HTTPS download/install checks, wheel-lock checks and static Windows PE/import/export checks are retained in `validation/tool-download-sources.json`, `validation/windows-native-static.json` and the other existing validation records. They are not Windows execution tests. The original source-suite report remains at `validation/source-tests-linux.xml`.

## Windows checks still required

Run **RUN-SETUP-AND-BUILD.cmd** on Windows x64. It creates the pinned build environment, builds both executables and requires the finished-EXE audits and packaged GUI, CLI, conflicting-DLL and harmless elevated-helper checks before creating **SenorX99-v0.86-Windows-x64.zip**. CI additionally requires the real medium/elevated file-access regression.

If retaining the already-installed build environment, copy it into the new source folder or replace the old source files with v0.86, then run **build_exe.bat** from Command Prompt. That script reuses `.build-venv`; the setup script recreates it.

Test real Windows logo replacement and repeat inspection of a ROM that already contains SenorSpace. Physical BIOS dump/flash tests were not performed here. See `docs/FIRST-HARDWARE-TEST.md` for the separate hardware checklist.

This deliverable is the source package. It contains no compiled Windows executables or external utility binaries.
