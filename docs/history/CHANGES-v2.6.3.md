# X99 Firmware Toolkit — Gen2 v2.6.3

## Build and use

1. Extract the complete source ZIP into a new folder.
2. Run `RUN-SETUP-AND-BUILD-Gen2-v2.6.3.cmd`.
3. Open `dist\X99-Firmware-Toolkit-UX-Gen2-v2.6.3.exe` and load your ROM.
4. Enable logo replacement; leave automatic SenorSpace selection enabled.
5. Build and review the report. The larger logo must pass ChangeLogo's real fit test and final extraction/pixel verification before the build is accepted.

The BMP is also included separately as `SenorSpace-800x600-4bit.bmp`.
The bundled copy is named `assets/SenorSpace-XD3-4bit.bmp` for automatic selection.
The old v2.6.1/v2.6.2 EXEs reject this larger file; replacing their assets alone does not update their size check.

## Full-resolution 4-bit SenorSpace

The asset is converted directly from the original bundled 800 x 600, 8-bit SenorSpace.
No cropping, resampling or enlarging of the old small replacement is used.
The character retains its original 353 x 411 pixel bounds on the original black canvas.
The output is an uncompressed bottom-up 4-bit BMP (16 colours), 240,118 bytes.
An optimized palette retains exact black and white plus fourteen colour entries; no extra dithering is introduced.

For a 4-bit original, the toolkit selects the new bundled 4-bit asset and preserves its dimensions.
Custom 4-bit replacements may also retain their own dimensions up to 800 x 600 when their BMP structures validate.
Exact original-format custom replacements remain accepted.
An 8-bit original retains the existing 8-bit selection/normalization path.
Mixed logo entries are handled independently. Choosing a custom image still disables automatic selection.

## Scope and validation

The code change is confined to splash selection/preparation and version labels. TBU, Secure Boot, OC/performance profiles, FIT handling and final ROM integrity logic are unchanged.

Twelve focused tests passed: actual 4-bit asset copying without resizing, malformed input rejection, existing 8-bit copy/conversion, automatic selection of each bit depth, mixed entries, custom choice preservation, unsupported orientation, GUI/CLI wiring and a modeled replacement through final verification.
All Python files passed syntax parsing, and the bundled tools/donor input validator passed.

The actual BMP was independently decoded and its pixels matched the encoded palette/index data. Its artwork bounds exactly match the original source.

These checks do not establish actual ChangeLogo execution or hardware rendering. Windows EXE compilation, real ChangeLogo fit/replacement on the target ROM, and a hardware boot have not been performed here. The bitmap's format is valid; the target machine's visual result remains to be checked.

Run focused tests with:

```
python -m unittest discover -s tests -p "test_splash_*.py" -v
```
