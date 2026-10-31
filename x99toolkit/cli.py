from __future__ import annotations
import argparse
from .sessionlog import start_session_log, install_exception_hook
from . import __version__
from pathlib import Path
from .inspection import inspect_rom
from .reporting import write_inspection
from .pipeline import build_rom, default_output
from .tools import discover_tools
from .tool_inventory import scan_tool_groups
from .feature_text import TBU_CPU_WARNING
import sys


def main() -> int:
    start_session_log(__version__, "SenorX99")
    install_exception_hook()
    parser = argparse.ArgumentParser(description="SenorX99 v0.931: inspect and create validated X99 firmware")
    parser.add_argument('--version', action='version', version=f'SenorX99 v{__version__}')
    sub = parser.add_subparsers(dest="cmd", required=True)
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("rom", type=Path)
    inspect_parser.add_argument("--report", action="store_true")

    patch_parser = sub.add_parser("patch")
    patch_parser.add_argument("rom", type=Path)
    patch_parser.add_argument("-o", "--output", type=Path)
    patch_parser.add_argument("--tbu-driver", type=Path)
    patch_parser.add_argument("--secureboot-donor", type=Path)
    patch_parser.add_argument("--secureboot-mode", choices=["auto", "kek_db", "pk_kek_db"], default="auto")
    patch_parser.add_argument("--optimized-settings", action="store_true", help="apply performance-oriented default settings in BIOS")
    patch_parser.add_argument("--unlocked-multiplier-oc", action="store_true", help="apply the validated XD4 multiplier-overclocking defaults and AMIBCP access changes")
    logo_group = patch_parser.add_mutually_exclusive_group()
    logo_group.add_argument("--splash", type=Path, help="Custom BMP to install as the splash; normalized independently to each original format")
    logo_group.add_argument("--senorspace", action="store_true", help="Automatically select the bundled SenorSpace variant from each extracted ROM splash")
    patch_parser.add_argument("--repair-fit", action=argparse.BooleanOptionalAction, default=True, help="advanced CLI override; GUI always validates/repairs FIT automatically (default: enabled)")
    patch_parser.add_argument("--preserve-stages", action="store_true")
    args = parser.parse_args()
    missing = [group['label'] for group in scan_tool_groups() if not group['ready']]
    if missing:
        parser.error('Install all required tools before loading a ROM. Open SenorX99 and use the Download buttons. Missing or incorrect: ' + ', '.join(missing))

    if args.cmd == "inspect":
        report = inspect_rom(args.rom)
        print(f"TBU: {report.tbu.get('status')} {report.tbu.get('undervolt_mv')}")
        print(f"Optimized BIOS Settings: supported={report.optimized.get('supported')} applied={report.optimized.get('matches')} layout={report.optimized.get('layout')}")
        print(f"Unlocked-multiplier OC: supported={report.overclock.get('supported')} applied={report.overclock.get('matches')} layout={report.overclock.get('layout')}")
        print(f"FIT: {report.fit.get('status')} repairable={report.fit.get('repairable')} delta={report.fit.get('common_delta_hex')}")
        print(f"Secure Boot: 2011={report.secure_boot.get('has_2011_certificates')} complete2023={report.secure_boot.get('has_complete_2023_set')} AMI-test-PK={report.secure_boot.get('has_ami_test_pk')}")
        if args.report:
            print(write_inspection(report, args.rom))
        return 0

    apply_tbu = args.tbu_driver is not None
    if apply_tbu:
        print(TBU_CPU_WARNING, file=sys.stderr)
    apply_secureboot = args.secureboot_donor is not None
    apply_splash = args.splash is not None or args.senorspace
    output = args.output or default_output(
        args.rom,
        apply_tbu=apply_tbu,
        apply_secureboot=apply_secureboot,
        apply_splash=apply_splash,
        repair_fit=args.repair_fit,
        apply_optimized=args.optimized_settings,
        apply_overclock=args.unlocked_multiplier_oc,
    )
    report = build_rom(
        source=args.rom,
        output=output,
        tools=discover_tools(),
        apply_tbu=apply_tbu,
        driver=args.tbu_driver,
        apply_secureboot=apply_secureboot,
        donor_source=args.secureboot_donor,
        secureboot_mode=args.secureboot_mode,
        apply_optimized=args.optimized_settings,
        apply_overclock=args.unlocked_multiplier_oc,
        apply_splash=apply_splash,
        splash_image=args.splash,
        repair_fit=args.repair_fit,
        preserve_stages=args.preserve_stages,
        progress=lambda name, status, details: print(f"[{status}] {name}: {details}"),
    )
    print("SUCCESS" if report.success else "FAILED", report.output_path, report.error)
    return 0 if report.success else 1
