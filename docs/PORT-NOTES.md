# SenorX99 v0.86 integration notes

Baseline: X99 Firmware Toolkit UX Gen2 v2.6.3, SHA-256 `15adbfe1366bbd75e9679fa069d7a37835dc64511ad5975a0650f5734c91a8cf`.

Standalone reference: `StalinVoter/Chinesium-X99-SecureBoot-Patcher` at `ccf52a2cbd9fe7ccad2dbdcb0ac79f3f4f2b31ad` (v0.967). Runtime/bootstrap, native-process isolation, tool registry/downloader, elevation transport, build sealing, finished-EXE checks, packaged tests and licensing support were adapted. The larger Toolkit GUI and stronger FPT/MMTool lifecycle code were retained.

Pillow 12.0.0 Windows CPython 3.13 x64 was added to the verified lock. All six external files now have pinned automatic download sources. Intel FPT is grouped into one UI action. ChangeLogo downloads byte-identically from its pinned mirror. The pinned MMTool source is checked before three fixed byte changes reproduce the exact previously validated v2.6.3 utility; the final original size/SHA-256 remain mandatory. See validation/tool-download-sources.json. The original four download definitions retain their pinned sources and identities.

The known partial-2023 db label issue was fixed separately. Only display classification changed; target completeness and update planning retain the original policy.

The maintainer subsequently requested opt-out logo replacement. GUI inspection now runs the full existing automatic SenorSpace pipeline on a temporary copy when verified ChangeLogo is available. A successful pixel/firmware-integrity-validated result is required for default selection. The source ROM is never supplied as a mutable work/output file; no BIOS dump or flash API is called. Failure leaves the default unchecked and retains manual/custom logo selection with the normal build checks. An explicit checkbox choice survives tool refresh/reinspection of the same ROM hash. Opening a different ROM hash resets the default decision.

Both executables remain asInvoker; the production BIOS helper alone uses runas. Backup location overrides and the prior permanent backup directory remain compatible. GUI/CLI names are SenorX99.exe and SenorX99-CLI.exe; source entry points use the same names with .py extensions.

Old documents under docs/history and the inherited board/fixture evidence describe the original Toolkit work. Current build/use instructions are the root README.md and START-HERE.md. The Windows workflow builds on main and supports explicit workflow-dispatch publication after its gates pass; it does not publish on an ordinary push.

The tools UI now uses one grouped action per tool on both Overview and Required tools. Overview shows only missing/incorrect groups and hides the card when all are ready. Its old FPT website button was removed. The three legacy copy/import scripts and duplicate GitHub release-notes file were removed; hardware-test instructions moved under docs.

A Windows build report showed 203 setup errors caused by denied access to the shared `%TEMP%/pytest-of-<username>` directory. The builder now passes an explicit absolute `--basetemp` under its fresh `build` directory, and stops at the first error with a short traceback. It does not change permissions on the shared TEMP directory. A subprocess regression reproduces the inaccessible-default-directory failure and verifies the actual builder's configured directory succeeds.

All six pinned tool files are now mandatory for GUI ROM input and configuration, dump/build/flash actions and public CLI ROM commands. Operation boundaries recheck exact files; both drop targets reject incomplete setups. The permanent Load / Dump ROM page remains reachable after inspection. The packaged dummy drop-routing self-test explicitly bypasses this gate only inside its recording subclass, whose load_rom implementation never reads firmware.

Performance-default explanations come from the actual detected settings list and describe all 11 target values and purposes. Existing optimized.py bytes and patch algorithms remain unchanged. The UI no longer exposes the internal default-copy mapping as its explanation. Secure Boot's current-key state is labelled Already up to date. TBU selection through either GUI route requires a default-No compatibility warning for supported E5-26xx v3 CPUs and the risk of a v4 system failing to POST; a build-time check also prevents an unacknowledged selection from being used.

Version 0.86 corrects the already-present-logo false failure after final image verification, propagates that state to the GUI, shows actual logo-check reasons, simplifies FIT language, removes the idle tools footer and signals dump completion after worker shutdown. Source archives use standard ZIP_STORED entries and are atomically published only after the central directory is complete. Current release staging/publishing uses RELEASE-NOTES-v0.86.md.
