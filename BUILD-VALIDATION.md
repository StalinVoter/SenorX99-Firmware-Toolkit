# SenorX99 v0.931 build validation

The public Windows package is built from the release commit by the repository’s Windows release workflow. Source tests and finished-executable checks must pass before publication.

The build runs firmware-engine and UI regression tests, verifies bundled-resource hashes, compiles the GUI and CLI, and audits both finished executables. It then starts the packaged GUI and CLI in isolated folders, checks drag-and-drop routing on the Windows Qt platform, tests resistance to conflicting DLLs, and exercises the harmless elevated-helper message exchange. These tests do not read or write a physical BIOS.

Each isolated packaged test receives the resources listed in resource-manifest.json, including languages/en.json. Resource hashes are checked before copying. External BIOS tools are deliberately excluded from these test folders. The GUI test opens the Load / Dump ROM page before checking its drop area. Failed tests retain their logs and print the underlying exception into the build log.

The release workflow also requires the real medium/elevated file-access regression. A release ZIP is not created from a failed packaged test, a changed executable, or a stale executable report. Source and Windows ZIPs are published with SHA-256 checksum files. Build logs and test reports are retained in the Windows validation artifact for the release run.

For a local Windows build, run RUN-SETUP-AND-BUILD.cmd. To reuse an existing .build-venv, run build_exe.bat from Command Prompt instead. The setup script creates a fresh isolated environment; build_exe.bat reuses it.

CPU recommendations, workflow gates, language loading and custom-logo selection are covered with simulated hardware/tool results. Physical BIOS dumping, flashing and recovery remain motherboard-dependent. See docs/FIRST-HARDWARE-TEST.md for the separate hardware checklist.

The optional scripts/build_portable_test.py route remains a development aid. It records static checks for its separate offline package and does not satisfy the native Windows checks required for a public release.
