# SenorX99 v0.93 validation

Source checks cover CPU model matching and recommendations, the six-step UI, tool requirements, recommended/custom controls, configuration invalidation, English text loading, firmware modifications, custom-logo verification, and saving a verified ROM.

The packaged-test regression checks that the external English catalog and bundled resources reach each isolated test folder, rejects missing or altered resources, checks failure diagnostics, and reproduces the original missing-language startup failure using the actual GUI constructor.

The local source-test report is validation/source-tests-v0.93.xml. Tests needing native Windows token/ACL support, PowerShell or unavailable firmware fixtures are skipped on hosts that lack them. Historical validation reports retain their original version numbers.

The Windows release workflow runs its own source suite and requires finished-EXE audits plus actual GUI, CLI, conflicting-DLL and helper-transport tests. Its Windows validation artifact contains the source report, executable audits and runtime reports for the exact release commit. The real medium/elevated file-access regression must run successfully before publication. See BUILD-VALIDATION.md for the build process.

These software checks do not establish that physical BIOS access works on every motherboard. Real dumping, flashing and recovery need hardware testing; see docs/FIRST-HARDWARE-TEST.md.

For v0.93, the local Linux suite passed 262 tests and 4 subtests, with 73 skips for unavailable platforms or fixtures. The complete Portuguese catalog and placeholders were checked. UI tests click both flags on every page and verify unchanged ROM, modification plan, step completion and built-file digest; language persistence and flag access on locked pages are also covered. The Portuguese layout was rendered and visually inspected. Native Windows packaged tests have not been run for v0.93 in this environment.

The subsequent download-confirmation update passed 279 tests and 4 subtests, with 73 skipped. Tests exercise all four tool groups in both languages, verifying actual URLs, copyright notices, destination folder, default No button, Yes-only worker launch, and cancellation by No or window closure. No real tool downloads occur in these regression tests.
