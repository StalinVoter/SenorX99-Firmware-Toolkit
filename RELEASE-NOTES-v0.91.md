# SenorX99 v0.91

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

- Six numbered steps guide you through installing tools, loading or dumping a BIOS, reviewing its analysis, choosing changes, building a ROM, and saving or flashing it.
- Installed CPU detection provides recommendations. Custom settings remain available when preparing a BIOS for another CPU or computer.
- Recommended mode is selected by default and locks modification choices, including the undervolt profile and boot-logo controls.
- Turbo Boost Unlock is recommended for E5-26xx v3 CPUs. The default ser8989 profile is −60/−50/−50 mV for Core / Cache / System Agent; other bundled profiles are available in Custom settings.
- Overclocking unlock recommendations cover E5-1650 v3, 1660 v3, 1680 v3 and i7-5820K, 5930K, 5960X, 6800K, 6850K, 6900K and 6950X.
- Secure Boot certificate updates, performance-oriented BIOS defaults and boot-logo replacement are recommended where the loaded ROM supports them. Already-current certificates are left unchanged.
- Dumping/flashing progress and FPT output appear within the main window. Existing-ROM loading, building and saving remain available when Windows security or another FPT blocker prevents hardware access.
- Custom BMPs must pass a replacement and verification trial before they can be selected.
- English interface text is stored in `languages/en.json`, ready for manually translated language files later.
- The packaged-app test now includes the external language file and other required resources, opens the correct page for its drag-and-drop check, and prints the underlying error if a test fails.

Download **SenorX99-v0.91-Windows-x64.zip**, extract the entire ZIP and open **SenorX99.exe**. You do not need Python or WinGet installed. Download missing firmware tools from step 1.

The original ROM is never overwritten. Flashing requires separate confirmation and two independently read, matching backups. CPU detection guides recommendations; it does not prevent preparing a ROM for a different target CPU.
