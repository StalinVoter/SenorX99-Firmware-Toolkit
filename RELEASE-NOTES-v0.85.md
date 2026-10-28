# SenorX99 v0.85

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

The first SenorX99 release continues X99 Firmware Toolkit UX Gen2 v2.6.3 and brings the standalone SecureBoot patcher's startup, download and BIOS-helper improvements into the full application.

- Normal-privilege GUI with file drops on the opening panel and main window; BIOS access alone requests administrator approval through the companion CLI.
- Missing tools and direct download buttons appear on Overview and disappear when setup is complete. Intel FPT installs all three required files through one button. UEFIReplace, MMTool and ChangeLogo also install automatically with exact size/SHA-256 checks.
- Windows source builds use a fresh project-local test temporary directory, avoiding access-denied errors from a locked shared pytest TEMP folder. Test failures stop the build immediately with a short traceback.
- All six tool files must be present and verified before loading or dumping a ROM, selecting changes, building or flashing. File drops and command-line ROM commands enforce the same setup requirement.
- A permanent **Load / Dump ROM** sidebar tab lets you open another ROM or dump the installed BIOS after loading a file.
- Performance-oriented BIOS defaults use plain-language text, with an expandable explanation of every changed setting, target value and purpose. Current Secure Boot factory keys show **Already up to date**.
- Selecting Turbo Boost Unlock requires acknowledgement that it only works on supported Xeon E5-26xx v3 CPUs and may prevent POST with a v4 CPU. The confirmation defaults to No.
- In-place helper reply files, separate helper error diagnoses and clear reporting of revoked FPT driver certificate error `0x800B010C`.
- Private native-runtime validation before GUI startup, improved startup logs and isolated firmware-tool launches.
- Two confirmations before flashing a newly built ROM, both defaulting to No, with the explicit interruption warning and the existing double-backup, verification and recovery protections retained.
- Boot-logo replacement defaults to checked after automatic SenorSpace replacement passes the temporary-copy build and validation checks for that ROM. Uncheck it to retain the original logo; an explicit opt-out survives tool rechecks.
- Existing TBU, undervolt profiles, optimized defaults, unlocked-multiplier OC, Secure Boot, FIT and automatic 4-bit/8-bit SenorSpace features retained.
- Separate db display correction: partial 2023 certificate sets are labelled accurately while the full-set update policy remains unchanged.
- Pinned isolated Windows builder and all Toolkit resources retained in the release package.

Extract the full Windows ZIP and run **SenorX99.exe normally**. Keep **SenorX99-CLI.exe** beside it. Use the **Download** buttons on Overview to install missing utilities. The **Required tools** tab remains available for the full inventory. The six external utility binaries are excluded from the public package.
