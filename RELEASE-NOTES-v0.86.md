# SenorX99 v0.86

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

This update fixes a false logo-replacement failure and makes the main status messages easier to understand.

- A ROM that already contains the requested SenorSpace images now passes the logo check and shows **SenorSpace already installed**. Previously the toolkit correctly skipped an unnecessary rewrite, then incorrectly rejected the unchanged ROM at its final check. The correction covers 4-bit and 8-bit images, the default logo and numbered logo entries. Final image checks remain mandatory; an attempted replacement that produces no change is still rejected.
- A failed logo check shows its reason directly in Overview and Create ROM. The vague **Automatic replacement not verified** status and nested exception prefixes have been removed from the main messages.
- The FIT section now explains the BIOS's internal list of CPU updates in plain language. When it can determine how to correct the list, the status says **Will be corrected in the new BIOS**. The list is corrected and verified during the build.
- Once a successful BIOS dump has finished and its worker has stopped, the dialog says **BIOS dump complete. You can close this window now.**
- Removed the redundant explanation below the tool-download rows. Download progress and errors still appear when needed.
- Windows builds use a fresh test directory inside the project to avoid access-denied errors from a locked shared pytest TEMP directory. The build stops at the first test failure with a short traceback.
- Source ZIPs use standard uncompressed entries and are published only after the complete archive has been written. The delivered archive is checked again after saving.

All v0.85 features are retained, including the complete missing-tool download list, grouped Intel FPT installation, the all-tools requirement before loading or dumping a ROM, the permanent Load / Dump ROM tab, the Turbo Boost Unlock CPU warning and verified opt-out logo selection.

Extract the source package and run **RUN-SETUP-AND-BUILD.cmd** on Windows x64. It builds the GUI and CLI and runs the required packaged checks before creating **SenorX99-v0.86-Windows-x64.zip**. The source package does not contain compiled Windows executables or the six downloadable external utilities. See **VALIDATION.md** for test scope.
