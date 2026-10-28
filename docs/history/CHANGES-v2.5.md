# Gen2-v2.5

Based on the corrected Gen2-v2 source package.

## TBU verification

The TBU path now derives the expected IA32 PE32 image base from the actual output location, confirms the input mapping, and applies only HIGHLOW entries from the selected driver's original relocation table. It compares the entire resulting FFS against this calculated expectation. Headers, section framing, non-relocated code/data and undervolt settings remain checked; unsupported layouts and unexpected differences fail. Other whole-file replacements still require exact equality.

## Diagnostic evidence

Each build creates a unique run directory next to the requested output:

`<output filename>.diagnostics/<timestamp>-<id>/`

Each firmware rebuild records its command, exit status, complete tool output, input/output hashes and verification events in `operation.json`. FFS verification saves `expected.ffs`, `actual.ffs`, `expected-relocated.ffs`, and complete TSV differences before and after accounting for relocation. On a rebuild failure, it automatically retains `PREIMAGE-NOT-FOR-FLASHING.bin` and, when produced, `REJECTED-NOT-FOR-FLASHING.bin`, plus extracted failed targets and a warning file. This does not depend on Preserve stages. Failed images are never promoted to successful build output.

The build reports contain the diagnostic directory and operation records even when verification raises. The Builds page provides Open diagnostics, and report/folder buttons remain available after a failed build.

## Build

Extract the source ZIP. From its folder run:

```bat
COPY-TOOLS-FROM-PREVIOUS.cmd "C:\X99-Firmware-Toolkit-UX-Gen2-v2\dist\tools"
RUN-SETUP-AND-BUILD-Gen2-v2.5.cmd
```

UEFIReplace is bundled and hash-verified. The copy command reuses your already-working Intel FPT executable and DLLs.
