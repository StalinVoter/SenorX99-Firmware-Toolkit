# SenorX99 v0.86

**Firmware Toolkit for Chinesium 2011-3 Xeon Boards**

For a Windows release, extract the complete ZIP and run **SenorX99.exe normally**. Keep **SenorX99-CLI.exe** beside it.

For this source package, run **RUN-SETUP-AND-BUILD.cmd** on Windows x64. It sets up the pinned isolated build environment, builds both executables and runs the packaged checks before creating the Windows ZIP. Keep the console open through completion. Setup output is written to `setup-build-SenorX99.log`.

If dependency installation already completed, extract an updated source ZIP over the existing source folder and run **build_exe.bat** from Command Prompt to reuse that build environment. Pre-build tests use `build/pytest-temp`, created inside the fresh build directory, so a locked shared `pytest-of-<username>` folder in Windows TEMP cannot block their temporary-file fixtures. The builder stops at the first failing test and prints its traceback.

Use the **Download** buttons under Missing tools on Overview. Intel FPT installs its executable and both DLLs together. UEFIReplace, MMTool and ChangeLogo also download automatically. The section disappears when all required files are ready. The **Required tools** tab provides the full inventory and recheck action.

All six required tool files must be installed and verified before loading or dumping any ROM or selecting changes. After setup, use **Load / Dump ROM** to open a file or dump the installed BIOS. This sidebar tab remains available when a ROM is already loaded.

Review the supported features in Overview, select the wanted changes in Create ROM, and build. Performance-oriented defaults include a **What does this mean?** explanation of every setting and why it changes. Selecting Turbo Boost Unlock asks you to confirm compatibility with a supported **Xeon E5-26xx v3** CPU; using it with a v4 CPU may prevent POST.

Replace the firmware boot logo is pre-checked only after automatic replacement passes a trial build on a temporary copy. Uncheck it to keep the original logo. Your opt-out is retained during tool rechecks. The original image is preserved. Successful builds offer the finished file directly to the flash workflow after two confirmations.

See `README.md` for feature coverage and commands. See `VALIDATION.md` for checks actually performed and the Windows/hardware checks still to run.
