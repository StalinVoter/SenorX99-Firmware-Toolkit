from __future__ import annotations
from pathlib import Path
from .sessionlog import logs_directory
import json
from .models import InspectionReport, BuildReport


def public_data(value):
    """Remove internal-only preservation bookkeeping from user-facing JSON."""
    if isinstance(value, dict):
        return {
            key: public_data(item)
            for key, item in value.items()
            if key != 'dbxVar'
        }
    if isinstance(value, list):
        return [
            public_data(item)
            for item in value
            if not (isinstance(item, str) and 'dbx' in item.lower())
        ]
    return value

def write_inspection(report: InspectionReport, path: Path) -> tuple[Path,Path]:
    path = logs_directory() / path.name
    text_path = Path(str(path) + '.inspection.txt'); json_path = Path(str(path) + '.inspection.json')
    lines = ['SenorX99 v0.931 inspection','====================================','',f'ROM: {report.rom_path}',f'Size: {report.rom_size}',f'SHA-256: {report.rom_sha256}','','Results:']
    for row in report.rows:
        lines.append(f"- [{row['category']}] {row['check']}: {row['result']} — {row['details']}")
    if report.errors:
        lines += ['','Errors:',*['- ' + error for error in report.errors]]
    text_path.write_text('\n'.join(lines) + '\n',encoding='utf-8')
    json_path.write_text(json.dumps(public_data(report.to_dict()),indent=2,ensure_ascii=False),encoding='utf-8')
    return text_path,json_path

def write_build(report: BuildReport, output: Path) -> tuple[Path,Path]:
    _, text_path, json_path = report_paths(output)
    lines = ['SenorX99 v0.931 build report','======================================','',f'Success: {report.success}',f'Input: {report.input_path}',f'Output: {report.output_path}',f'Live log: {report.build_log_path}',f'Input SHA-256: {report.input_sha256}',f'Output SHA-256: {report.output_sha256}',f'Input size: {report.input_size}',f'Output size: {report.output_size}',f'Options: {report.options}','','Secure Boot plan:']
    if report.secure_boot_plan:
        for item in report.secure_boot_plan:
            lines.append(f"- [{item['action']}] {item['name']}: {item['reason']}")
    else:
        lines.append('- none')
    lines += ['', 'FIT repair:']
    if report.fit_repair:
        lines.append(f"- changed={report.fit_repair.get('changed')} method={report.fit_repair.get('method')} changes={len(report.fit_repair.get('changes', []))}")
    else:
        lines.append('- not requested or not reached')
    lines += ['','Steps:']
    for step in report.steps:
        lines.append(f'- [{step.status}] {step.name}: {step.details}')
    if report.error:
        lines += ['','Error:',report.error]
    for name,text in report.tool_output.items():
        lines += ['',f'--- {name} ---',text]
    text_path.write_text('\n'.join(lines) + '\n',encoding='utf-8')
    json_path.write_text(json.dumps(public_data(report.to_dict()),indent=2,ensure_ascii=False),encoding='utf-8')
    return text_path,json_path


def report_paths(output: Path):
    root = logs_directory()
    return tuple(root / (output.name + suffix) for suffix in ('.build.log', '.report.txt', '.report.json'))
