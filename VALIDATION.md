# SenorX99 v0.91 validation

Source checks cover CPU model matching and recommendations, the six-step UI, tool requirements, recommended/custom controls, configuration invalidation, English text loading, firmware modifications, custom-logo verification, and saving a verified ROM.

The packaged-test regression checks that the external English catalog and bundled resources reach each isolated test folder, rejects missing or altered resources, checks failure diagnostics, and reproduces the original missing-language startup failure using the actual GUI constructor.

The local source-test report is validation/source-tests-v0.91.xml. Tests needing native Windows token/ACL support, PowerShell or unavailable firmware fixtures are skipped on hosts that lack them. Historical validation reports retain their original version numbers.

The Windows release workflow runs its own source suite and requires finished-EXE audits plus actual GUI, CLI, conflicting-DLL and helper-transport tests. Its Windows validation artifact contains the source report, executable audits and runtime reports for the exact release commit. The real medium/elevated file-access regression must run successfully before publication. See BUILD-VALIDATION.md for the build process.

These software checks do not establish that physical BIOS access works on every motherboard. Real dumping, flashing and recovery need hardware testing; see docs/FIRST-HARDWARE-TEST.md.
