from __future__ import annotations
from datetime import datetime
from pathlib import Path
import shutil, tempfile, platform, sys, traceback

from .models import BuildReport, ProgressCallback
from .firmware import find_ffs_by_guid, sha256_file, scan_06f2_microcode, patch_primary_cstates
from .ser8989 import inspect_driver, inspect_tbu, decode_fields
from .secureboot import inspect_secure_boot, load_donors, plan_secure_boot, inspect_donor_ffs, inspect_var
from .inspection import inspect_rom
from .microcode import remove_06f2
from .diagnostics import diagnostic_session
from .replacer import replace_ffs, replace_section_body
from .constants import TBU_TARGET_GUID, SECURE_BOOT_GUIDS, SER8989_MARKER_ASCII, SER8989_MARKER_UTF16LE
from .tools import ToolPaths
from .reporting import write_build, report_paths
from .sessionlog import log as session_log, log_json, log_exception
from .cstates import build_nv_internal_body, build_amitse_body, validate_nested_body, inspect_all_cstates
from .splash import replace_splash
from .fit import inspect_fit, capture_fit_reference, repair_fit_file, capture_fit_rebuild_reference, repair_fit_rebuild_damage
from .optimized import (
    inspect_optimized_settings,
    patch_primary_optimized,
    build_optimized_nv_body,
    build_optimized_amitse_body,
)
from .overclock import (
    inspect_overclock_compatibility,
    patch_primary_overclock,
    build_overclock_nv_body,
    build_overclock_amitse_body,
)


def default_output(source: Path, apply_tbu: bool = False, apply_secureboot: bool = False, apply_splash: bool = False, repair_fit: bool = False, apply_optimized: bool = False, apply_overclock: bool = False) -> Path:
    tags: list[str] = []
    if apply_tbu:
        tags.append("TBU")
    if apply_secureboot:
        tags.append("SB2023")
    if apply_optimized:
        tags.append("OPT")
    if apply_overclock:
        tags.append("OC")
    if apply_splash:
        tags.append("LOGO")
    return source.with_name(f"{source.stem}_{'_'.join(tags) or 'modified'}{source.suffix}")


def _tbu_validator(selected_uv):
    def validate(output_path, replacement_path, ffs):
        marker = SER8989_MARKER_ASCII in ffs.data or SER8989_MARKER_UTF16LE in ffs.data
        if not marker or decode_fields(ffs.data) != selected_uv:
            raise RuntimeError("injected ser8989 payload validation failed")
    return validate


def _secureboot_validator(name):
    def validate(output_path, replacement_path, ffs):
        donor = inspect_donor_ffs(replacement_path, name)
        variable = inspect_var(name, ffs)
        if variable.payload_sha256 != donor["payload_sha256"]:
            raise RuntimeError(f"{name} output payload does not match donor")
    return validate


def _metadata_note(validation: dict) -> str:
    if validation.get("metadata_normalized"):
        return "; UEFIReplace normalized section framing/padding; all section bodies verified exact"
    return "; full decompressed container also matched exactly"


def build_rom(source: Path, output: Path, tools: ToolPaths, apply_tbu: bool = False, driver: Path | None = None, apply_secureboot: bool = False, donor_source: Path | None = None, secureboot_mode: str = "auto", apply_optimized: bool = False, apply_overclock: bool = False, apply_splash: bool = False, splash_image: Path | None = None, repair_fit: bool = True, preserve_stages: bool = False, progress: ProgressCallback | None = None) -> BuildReport:
    source = source.resolve()
    output = output.resolve()
    report = BuildReport(str(source), str(output))
    log = None
    def emit(name: str, status: str, details: str) -> None:
        report.add(name, status, details)
        session_log("BUILD", f"[{status}] {name}: {details}")
        if progress:
            progress(name, status, details)
        if log:
            log.write(f"{datetime.now().isoformat(timespec='seconds')} [{status}] {name}: {details}\n")
            log.flush()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        log_path = report_paths(output)[0]
        report.build_log_path = str(log_path)
        log = log_path.open("w", encoding="utf-8", buffering=1)
        log.write(f"SenorX99 v0.931 live log\nStarted: {datetime.now().isoformat()}\nInput: {source}\nOutput: {output}\nPlatform: {platform.platform()}\nPython: {sys.version}\nMMTool: {tools.mmtool}\nUEFIReplace: {tools.uefireplace}\nChangeLogo: {tools.changelogo}\n")
        if not source.is_file() or source.suffix.lower() not in (".rom", ".bin"):
            raise ValueError("input must be an existing .ROM or .BIN")
        if source == output or (output.exists() and source.samefile(output)):
            raise ValueError("output must differ from input")
        if not apply_tbu and not apply_secureboot and not apply_optimized and not apply_overclock and not apply_splash and not repair_fit:
            raise ValueError("no patch operation selected")
        report.input_size = source.stat().st_size
        report.input_sha256 = sha256_file(source)
        report.options = {"apply_tbu": apply_tbu, "driver": str(driver) if driver else None, "apply_secureboot": apply_secureboot, "donor_source": str(donor_source) if donor_source else None, "secureboot_mode": secureboot_mode, "apply_optimized": apply_optimized, "apply_overclock": apply_overclock, "apply_splash": apply_splash, "splash_image": str(splash_image) if splash_image else None, "repair_fit": repair_fit, "preserve_stages": preserve_stages}
        log_json("BUILD", "Build options", report.options)
        emit("Input preflight", "PASS", f"{report.input_size} bytes; SHA-256 {report.input_sha256}")
        dbx_initial = sorted(x.sha256 for x in find_ffs_by_guid(source.read_bytes(), SECURE_BOOT_GUIDS["dbxVar"]))
        initial = inspect_rom(source)
        report.initial_inspection = initial.to_dict()
        emit("Initial inspection", "PASS", f"TBU={initial.tbu.get('status')}; OPT={initial.optimized.get('matches')}; OC={initial.overclock.get('matches')}; FIT={initial.fit.get('status')}; SB2023={initial.secure_boot.get('has_complete_2023_set')}; AMI test PK={initial.secure_boot.get('has_ami_test_pk')}")
        if apply_optimized and not initial.optimized.get("supported"):
            raise ValueError("Optimized BIOS Settings preflight failed: " + initial.optimized.get("error", "unsupported ROM/settings layout"))
        if apply_overclock and not initial.overclock.get("supported"):
            raise ValueError("Unlocked-multiplier OC preflight failed: " + initial.overclock.get("error", "unsupported ROM/settings layout"))
        with diagnostic_session(output, report), tempfile.TemporaryDirectory(prefix="X99-Firmware-Toolkit-") as temp_name:
            temp = Path(temp_name)
            work = temp / "work.rom"
            shutil.copy2(source, work)
            fit_reference = None
            if apply_tbu:
                if driver is None:
                    raise ValueError("TBU selected but no ser8989 driver provided")
                driver_info = inspect_driver(driver)
                if scan_06f2_microcode(work.read_bytes()):
                    if not tools.mmtool:
                        raise FileNotFoundError("MMTool not found")
                    emit("Remove 06F2 microcode", "RUNNING", "validated entry present")
                    microcode = remove_06f2(work, tools.mmtool)
                    report.tool_output["MMTool"] = microcode["diagnostic"]
                    emit("Remove 06F2 microcode", "PASS", f"removed {microcode['removed_count']}; final {microcode['final_count']}")
                else:
                    emit("Remove 06F2 microcode", "PASS", "already absent")
                try:
                    fit_reference = capture_fit_reference(work.read_bytes())
                    emit("Capture FIT microcode identities", "PASS", f"{len(fit_reference['type1_identities'])} Type-1 entries captured before PEI rebuild")
                except Exception as exc:
                    fit_reference = None
                    current_fit = inspect_fit(work)
                    if current_fit.get('status') == 'STALE' and current_fit.get('repairable'):
                        emit("Capture FIT microcode identities", "SKIP", f"input FIT already stale; will use unique structural repair plan ({current_fit.get('common_delta_hex')})")
                    else:
                        raise RuntimeError(f"cannot establish a safe FIT repair reference before TBU rebuild: {exc}") from exc
                primary = patch_primary_cstates(work)
                emit("Set primary IntelSetup C-states", "PASS", f"before {primary['before']}; after {primary['after']}")
                if not tools.uefireplace:
                    raise FileNotFoundError("UEFIReplace not found")

                nv_body = temp / "nv-internal-body.bin"
                nv_plan = build_nv_internal_body(work.read_bytes(), nv_body)
                if nv_plan["changed"]:
                    emit("Set NV-Internal C-state defaults", "RUNNING", f"decompressed offsets {nv_plan['changed_decompressed_offsets']}")
                    next_rom = temp / "cstate-nv.rom"
                    failed_copy = output.with_name(output.stem + ".failed-nv-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work,
                        nv_body,
                        next_rom,
                        nv_plan["guid"],
                        nv_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            nv_plan["guid"],
                            nv_plan["section_type"],
                            nv_plan["expected_target_body_sha256"],
                            nv_plan["expected_target_header_extension_sha256"],
                            nv_plan["expected_target_header_extension_size"],
                            nv_plan["expected_sibling_sections"],
                            nv_plan["expected_full_decompressed_sha256"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-NV-Internal"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    if preserve_stages:
                        shutil.copy2(work, output.with_name(output.stem + ".stage-cstate-nv" + output.suffix))
                    emit("Set NV-Internal C-state defaults", "PASS", f"before {nv_plan['before']}; after {nv_plan['after']}" + _metadata_note(result["validation"]))
                else:
                    emit("Set NV-Internal C-state defaults", "SKIP", "already current")

                am_body = temp / "amitse-body.bin"
                am_plan = build_amitse_body(work.read_bytes(), am_body)
                if am_plan["changed"]:
                    offsets = ", ".join(f"0x{x:X}" for x in am_plan["decompressed_offsets"])
                    emit(
                        "Set AMITSESetupData C-state default",
                        "RUNNING",
                        f"layout={am_plan['layout']}; decompressed offsets [{offsets}]",
                    )
                    next_rom = temp / "cstate-amitse.rom"
                    failed_copy = output.with_name(output.stem + ".failed-amitse-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work,
                        am_body,
                        next_rom,
                        am_plan["guid"],
                        am_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            am_plan["guid"],
                            am_plan["section_type"],
                            am_plan["expected_target_body_sha256"],
                            am_plan["expected_target_header_extension_sha256"],
                            am_plan["expected_target_header_extension_size"],
                            am_plan["expected_sibling_sections"],
                            am_plan["expected_full_decompressed_sha256"],
                            am_plan["target_body_offsets"],
                            am_plan["expected_values"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-AMITSESetupData"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    if preserve_stages:
                        shutil.copy2(work, output.with_name(output.stem + ".stage-cstate-amitse" + output.suffix))
                    emit(
                        "Set AMITSESetupData C-state default",
                        "PASS",
                        f"layout={am_plan['layout']}; {am_plan['before']} -> {am_plan['after']}"
                        + _metadata_note(result["validation"]),
                    )
                else:
                    emit(
                        "Set AMITSESetupData C-state default",
                        "SKIP",
                        f"layout={am_plan['layout']}; already current {am_plan['after']}",
                    )

                cstate_check = inspect_all_cstates(work)
                if not cstate_check.get("matches"):
                    raise RuntimeError(f"complete three-copy C-state validation failed: {cstate_check}")
                emit("Validate complete MiyConst C-states", "PASS", "primary IntelSetup + NV-Internal + AMITSESetupData")

                next_rom = temp / "tbu.rom"
                replacement = replace_ffs(work, driver, next_rom, TBU_TARGET_GUID, tools.uefireplace, _tbu_validator(driver_info.undervolt_mv), allow_tbu_relocation=True)
                report.tool_output["UEFIReplace-TBU"] = replacement["diagnostic"]
                if replacement.get("skipped"):
                    emit("Apply TBU payload", "SKIP", replacement["reason"])
                else:
                    shutil.copy2(next_rom, work)
                    emit("Apply TBU payload", "PASS", f"undervolt {driver_info.undervolt_mv}")
                tbu = inspect_tbu(work)
                if not tbu.get("applied"):
                    raise RuntimeError(f"TBU final validation failed: {tbu}")
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + ".stage-tbu" + output.suffix))

            if apply_optimized:
                primary_opt = patch_primary_optimized(work)
                changed_names = [
                    name for name, before_value in primary_opt["before"].items()
                    if primary_opt["after"][name] != before_value
                ]
                emit(
                    "Apply Optimized BIOS Settings: primary IntelSetup",
                    "PASS",
                    f"changed={changed_names if changed_names else 'none'}; 11 named defaults verified",
                )
                if not tools.uefireplace:
                    raise FileNotFoundError("UEFIReplace not found")

                opt_nv_body = temp / "optimized-nv-body.bin"
                opt_nv_plan = build_optimized_nv_body(work.read_bytes(), opt_nv_body)
                if opt_nv_plan["changed"]:
                    emit(
                        "Apply Optimized BIOS Settings: NV-Internal",
                        "RUNNING",
                        f"decompressed offsets {opt_nv_plan['changed_decompressed_offsets']}",
                    )
                    next_rom = temp / "optimized-nv.rom"
                    failed_copy = output.with_name(output.stem + ".failed-opt-nv-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work, opt_nv_body, next_rom, opt_nv_plan["guid"], opt_nv_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            opt_nv_plan["guid"],
                            opt_nv_plan["section_type"],
                            opt_nv_plan["expected_target_body_sha256"],
                            opt_nv_plan["expected_target_header_extension_sha256"],
                            opt_nv_plan["expected_target_header_extension_size"],
                            opt_nv_plan["expected_sibling_sections"],
                            opt_nv_plan["expected_full_decompressed_sha256"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-Optimized-NV-Internal"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    emit(
                        "Apply Optimized BIOS Settings: NV-Internal",
                        "PASS",
                        "11 named defaults mirrored" + _metadata_note(result["validation"]),
                    )
                else:
                    emit("Apply Optimized BIOS Settings: NV-Internal", "SKIP", "already current")

                opt_am_body = temp / "optimized-amitse-body.bin"
                opt_am_plan = build_optimized_amitse_body(work.read_bytes(), opt_am_body)
                if opt_am_plan["changed"]:
                    emit(
                        "Apply Optimized BIOS Settings: AMITSESetupData",
                        "RUNNING",
                        f"layout={opt_am_plan['layout']}; {len(opt_am_plan['changed_decompressed_offsets'])} validated AMIBCP default-table byte(s)",
                    )
                    next_rom = temp / "optimized-amitse.rom"
                    failed_copy = output.with_name(output.stem + ".failed-opt-amitse-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work, opt_am_body, next_rom, opt_am_plan["guid"], opt_am_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            opt_am_plan["guid"],
                            opt_am_plan["section_type"],
                            opt_am_plan["expected_target_body_sha256"],
                            opt_am_plan["expected_target_header_extension_sha256"],
                            opt_am_plan["expected_target_header_extension_size"],
                            opt_am_plan["expected_sibling_sections"],
                            opt_am_plan["expected_full_decompressed_sha256"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-Optimized-AMITSESetupData"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    emit(
                        "Apply Optimized BIOS Settings: AMITSESetupData",
                        "PASS",
                        f"layout={opt_am_plan['layout']}; AMIBCP-derived defaults reproduced" + _metadata_note(result["validation"]),
                    )
                else:
                    emit(
                        "Apply Optimized BIOS Settings: AMITSESetupData",
                        "SKIP",
                        f"layout={opt_am_plan['layout']}; already current",
                    )

                optimized_check = inspect_optimized_settings(work)
                if not optimized_check.get("supported") or not optimized_check.get("matches"):
                    raise RuntimeError(f"optimized BIOS settings final validation failed: {optimized_check}")
                emit(
                    "Validate Optimized BIOS Settings",
                    "PASS",
                    "11 named defaults + NV-Internal mirror + AMITSE default table",
                )
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + ".stage-optimized" + output.suffix))

            if apply_overclock:
                primary_oc = patch_primary_overclock(work)
                changed_names = [
                    name for name, before_value in primary_oc["before"].items()
                    if primary_oc["after"][name] != before_value
                ]
                emit(
                    "Apply multiplier OC: primary IntelSetup",
                    "PASS",
                    f"changed={changed_names if changed_names else 'none'}; three validated defaults verified",
                )
                if not tools.uefireplace:
                    raise FileNotFoundError("UEFIReplace not found")

                oc_nv_body = temp / "overclock-nv-body.bin"
                oc_nv_plan = build_overclock_nv_body(work.read_bytes(), oc_nv_body)
                if oc_nv_plan["changed"]:
                    emit(
                        "Apply multiplier OC: NV-Internal",
                        "RUNNING",
                        f"decompressed offsets {oc_nv_plan['changed_decompressed_offsets']}",
                    )
                    next_rom = temp / "overclock-nv.rom"
                    failed_copy = output.with_name(output.stem + ".failed-oc-nv-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work, oc_nv_body, next_rom, oc_nv_plan["guid"], oc_nv_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            oc_nv_plan["guid"],
                            oc_nv_plan["section_type"],
                            oc_nv_plan["expected_target_body_sha256"],
                            oc_nv_plan["expected_target_header_extension_sha256"],
                            oc_nv_plan["expected_target_header_extension_size"],
                            oc_nv_plan["expected_sibling_sections"],
                            oc_nv_plan["expected_full_decompressed_sha256"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-OC-NV-Internal"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    emit(
                        "Apply multiplier OC: NV-Internal",
                        "PASS",
                        "three validated defaults mirrored" + _metadata_note(result["validation"]),
                    )
                else:
                    emit("Apply multiplier OC: NV-Internal", "SKIP", "already current")

                oc_am_body = temp / "overclock-amitse-body.bin"
                oc_am_plan = build_overclock_amitse_body(work.read_bytes(), oc_am_body)
                if oc_am_plan["changed"]:
                    emit(
                        "Apply multiplier OC: AMITSESetupData",
                        "RUNNING",
                        f"layout={oc_am_plan['layout']}; {len(oc_am_plan['changed_decompressed_offsets'])} validated AMIBCP byte(s)",
                    )
                    next_rom = temp / "overclock-amitse.rom"
                    failed_copy = output.with_name(output.stem + ".failed-oc-amitse-rebuild" + output.suffix) if preserve_stages else None
                    result = replace_section_body(
                        work, oc_am_body, next_rom, oc_am_plan["guid"], oc_am_plan["section_type"],
                        tools.uefireplace,
                        lambda path: validate_nested_body(
                            path,
                            oc_am_plan["guid"],
                            oc_am_plan["section_type"],
                            oc_am_plan["expected_target_body_sha256"],
                            oc_am_plan["expected_target_header_extension_sha256"],
                            oc_am_plan["expected_target_header_extension_size"],
                            oc_am_plan["expected_sibling_sections"],
                            oc_am_plan["expected_full_decompressed_sha256"],
                        ),
                        failed_copy,
                    )
                    report.tool_output["UEFIReplace-OC-AMITSESetupData"] = result["diagnostic"]
                    shutil.copy2(next_rom, work)
                    emit(
                        "Apply multiplier OC: AMITSESetupData",
                        "PASS",
                        f"layout={oc_am_plan['layout']}; access flags and defaults reproduced" + _metadata_note(result["validation"]),
                    )
                else:
                    emit(
                        "Apply multiplier OC: AMITSESetupData",
                        "SKIP",
                        f"layout={oc_am_plan['layout']}; already current",
                    )

                overclock_check = inspect_overclock_compatibility(work)
                if not overclock_check.get("supported") or not overclock_check.get("matches"):
                    raise RuntimeError(f"unlocked-multiplier OC final validation failed: {overclock_check}")
                emit(
                    "Validate multiplier OC compatibility",
                    "PASS",
                    "three setup defaults + NV-Internal mirror + eight AMIBCP default/access bytes",
                )
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + ".stage-overclock" + output.suffix))

            if apply_secureboot:
                if not tools.uefireplace:
                    raise FileNotFoundError("UEFIReplace not found")
                if donor_source is None:
                    raise ValueError("Secure Boot patch selected but no donor source provided")
                donors = load_donors(donor_source, temp / "donors")
                current = inspect_secure_boot(work)
                plan = plan_secure_boot(current, donors, secureboot_mode)
                report.secure_boot_plan = plan
                log_json("BUILD", "Secure Boot patch plan", plan)
                dbx_before_payload = current["variables"]["dbxVar"].get("payload_sha256")
                dbx_before_ffs = current["variables"]["dbxVar"].get("ffs_sha256")
                if not plan:
                    emit("Secure Boot Auto plan", "SKIP", "no Secure Boot variable requires replacement")
                for index, item in enumerate(plan, 1):
                    name = item["name"]
                    if item["action"] == "SKIP":
                        emit(f"Replace {name}", "SKIP", item["reason"])
                        continue
                    emit(f"Replace {name}", "RUNNING", item["donor"])
                    next_rom = temp / f"sb-{index}-{name}.rom"
                    replacement = replace_ffs(work, Path(item["donor"]), next_rom, SECURE_BOOT_GUIDS[name], tools.uefireplace, _secureboot_validator(name))
                    report.tool_output[f"UEFIReplace-{name}"] = replacement["diagnostic"]
                    if replacement.get("skipped"):
                        emit(f"Replace {name}", "SKIP", replacement["reason"])
                    else:
                        shutil.copy2(next_rom, work)
                        emit(f"Replace {name}", "PASS", f"size 0x{replacement['old_size']:X}->0x{replacement['new_size']:X}")
                secure_boot = inspect_secure_boot(work)
                if not secure_boot["has_complete_2023_set"]:
                    raise RuntimeError("Secure Boot final validation lacks complete 2023 KEK/db set")
                if current.get("has_ami_test_pk") and (secure_boot["has_ami_test_pk"] or not secure_boot["has_asrock_pk"]):
                    raise RuntimeError("PkVar final validation failed to replace AMI test PK with ASRock production PK")
                if secure_boot["variables"]["dbxVar"].get("payload_sha256") != dbx_before_payload:
                    raise RuntimeError("Non-target Secure Boot data changed unexpectedly")
                if secure_boot["variables"]["dbxVar"].get("ffs_sha256") != dbx_before_ffs:
                    raise RuntimeError("Non-target Secure Boot firmware file changed unexpectedly")
                emit("Apply Secure Boot 2023", "PASS", f"patched={[item['name'] for item in plan if item['action']=='PATCH']}; skipped={[item['name'] for item in plan if item['action']=='SKIP']}; complete2023={secure_boot['has_complete_2023_set']}; testPK={secure_boot['has_ami_test_pk']}")
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + ".stage-secureboot" + output.suffix))

            if apply_splash:
                if not tools.changelogo:
                    raise FileNotFoundError("ChangeLogo.exe not found")
                if splash_image is not None and not splash_image.is_file():
                    raise ValueError("Splash replacement selected but the replacement BMP is missing")
                splash_mode = "automatic SenorSpace" if splash_image is None else "custom image"
                report.options["splash_mode"] = splash_mode
                splash_label = splash_image.name if splash_image is not None else "SenorSpace (automatic format selection)"
                emit("Replace splash logo", "RUNNING", f"source={splash_label}; inventorying default + numbered multi-splash logos and matching each BMP format")
                next_rom = temp / "splash.rom"
                splash_fit_reference = capture_fit_rebuild_reference(work)
                splash_result = replace_splash(work, next_rom, splash_image.resolve() if splash_image is not None else None, tools.changelogo, temp / "logo-work")
                report.options['splash_already_present'] = splash_result.get('already_present', False)
                splash_safeguard = repair_fit_rebuild_damage(next_rom, splash_fit_reference)
                log_json("FIT SAFEGUARD", "ChangeLogo rebuild safeguard", splash_safeguard)
                report.tool_output["ChangeLogo-Splash"] = splash_result["diagnostic"]
                shutil.copy2(next_rom, work)
                norm = splash_result["normalization"]
                report.options["resolved_splash_image"] = norm["selected_source"]
                orig_spec = norm["original_spec"]
                new_spec = norm["normalized_spec"]
                multi_count = splash_result.get("multiple_logo_count", 0)
                shared_count = sum(
                    1 for item in splash_result.get("multiple_logos", [])
                    if item.get("already_current_after_default")
                )
                emit(
                    "Replace splash logo",
                    "PASS",
                    f"mode={norm['selection_mode']}; selected={Path(norm['selected_source']).name}; "
                    f"default={orig_spec['width']}x{orig_spec['height']} {orig_spec['bits_per_pixel']}bpp BI_RGB -> "
                    f"{new_spec['width']}x{new_spec['height']} {new_spec['bits_per_pixel']}bpp BI_RGB; "
                    f"numbered multi-splash logos={multi_count}; shared/already-updated={shared_count}; "
                    f"{norm['operation']}; embedded basename={norm['replacement_basename']}; default and every numbered splash re-extracted and pixel-verified",
                )
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + ".stage-logo" + output.suffix))

            if repair_fit:
                fit_before = inspect_fit(work)
                if fit_before.get('status') == 'PASS':
                    emit('Repair / validate FIT', 'RUNNING', f"FIT already resolves all {fit_before.get('type1_count', 0)} Type-1 entries; validating and preserving")
                elif fit_before.get('repairable'):
                    emit('Repair / validate FIT', 'RUNNING', f"stale Type-1 pointers detected; structural candidate delta {fit_before.get('common_delta_hex')}")
                else:
                    emit('Repair / validate FIT', 'RUNNING', f"FIT status={fit_before.get('status')}; attempting captured-identity repair if available")
                fit_result = repair_fit_file(work, fit_reference if apply_tbu and fit_reference is not None else None)
                report.fit_repair = fit_result
                if fit_result['changed']:
                    changes = ', '.join(
                        f"#{item['entry_index']} {item['old_address_hex']}->{item['new_address_hex']} ({item['signature_hex']} rev {item['revision_hex']})"
                        for item in fit_result['changes']
                    )
                    emit('Repair / validate FIT', 'PASS', f"method={fit_result['method']}; repaired {len(fit_result['changes'])} Type-1 address(es): {changes}; post-validation PASS")
                else:
                    emit('Repair / validate FIT', 'PASS', f"method={fit_result['method']}; no address changes required; post-validation PASS")
                if preserve_stages:
                    shutil.copy2(work, output.with_name(output.stem + '.stage-fit' + output.suffix))

            if work.stat().st_size != source.stat().st_size:
                raise RuntimeError("final ROM size changed")
            final = inspect_rom(work)
            report.final_inspection = final.to_dict()
            if apply_tbu and not final.tbu.get("applied"):
                raise RuntimeError("combined final inspection says TBU is not fully applied")
            if apply_secureboot and not final.secure_boot.get("has_complete_2023_set"):
                raise RuntimeError("combined final inspection says 2023 certificates are incomplete")
            if apply_optimized and not final.optimized.get("matches"):
                raise RuntimeError(f"combined final inspection says Optimized BIOS Settings are incomplete: {final.optimized}")
            if apply_overclock and not final.overclock.get("matches"):
                raise RuntimeError(f"combined final inspection says unlocked-multiplier OC compatibility is incomplete: {final.overclock}")
            if repair_fit and final.fit.get('status') != 'PASS':
                raise RuntimeError(f"combined final inspection says FIT is not valid: {final.fit}")
            if sorted(x.sha256 for x in find_ffs_by_guid(work.read_bytes(), SECURE_BOOT_GUIDS["dbxVar"])) != dbx_initial:
                raise RuntimeError("Final non-target Secure Boot preservation check failed")
            shutil.copy2(work, output)
            report.output_size = output.stat().st_size
            report.output_sha256 = sha256_file(output)
            report.success = True
            emit("Final validation", "PASS", f"SHA-256 {report.output_sha256}; size preserved")
        write_build(report, output)
        emit("Reports", "PASS", f"{report_paths(output)[1]}; {report_paths(output)[2]}")
        return report
    except Exception as exc:
        log_exception("BUILD", exc, "Build failed")
        report.error = f"{type(exc).__name__}: {exc}"
        report.success = False
        emit("Build", "FAIL", report.error)
        if log:
            log.write("\n===== Python traceback =====\n")
            log.write(traceback.format_exc())
            log.write("===== End traceback =====\n")
        try:
            write_build(report, output)
        except Exception:
            pass
        return report
    finally:
        if log:
            log.write(f"Finished: {datetime.now().isoformat()}\n")
            log.close()
