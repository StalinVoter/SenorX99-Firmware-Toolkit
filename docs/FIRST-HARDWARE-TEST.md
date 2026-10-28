# SenorX99 first Windows/hardware checks

1. Build with `RUN-SETUP-AND-BUILD.cmd` and retain the complete output and logs.
2. Start `SenorX99.exe` normally. Verify a real Explorer drop onto both the panel and main window, including a filename with spaces/Unicode. This is distinct from the packaged synthetic drop-event test.
3. Check Required tools. Use the automatic downloads, including the single FPT action for all three files, and confirm all required rows for the operation are Ready.
4. Run `TEST-ELEVATED-HELPER.cmd` normally. Its diagnostic exchange must succeed; it does not access firmware.
5. When deliberately testing BIOS access on suitable hardware, use Dump BIOS and compare the resulting image/size against the known board's working dump. Inspect all per-feature gates before building. With verified ChangeLogo available, confirm a compatible ROM passes its temporary-copy logo check and defaults to a checked logo option. Uncheck it, recheck tools, and confirm your opt-out remains in effect.
6. A physical flash is a separate, explicitly chosen hardware test. Keep the full backup, verification and recovery workflow; retain both confirmations. Do not treat source tests, a packaged runtime check, a helper transport check or a successful dump as proof of a completed physical flash.
