from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from .firmware import scan_06f2_microcode
from .tools import run_mmtool, ExternalToolResult

@dataclass(frozen=True)
class PatchRow:
    number: str
    volume: str
    cpuid: str
    revision: str
    line: str

def parse_rows(text: str) -> list[PatchRow]:
    result: list[PatchRow] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split('|')]
        if len(fields) >= 10 and re.fullmatch(r'[0-9A-Fa-f]{1,4}', fields[1]) and re.fullmatch(r'[0-9A-Fa-f]{1,4}', fields[2]) and fields[6].upper().removeprefix('0X') in {'06F2','6F2','306F2'}:
            result.append(PatchRow(fields[2].upper(), fields[1].upper(), fields[6].upper(), fields[7].upper(), line))
    unique: list[PatchRow] = []; seen: set[tuple[str,str,str]] = set()
    for row in result:
        key = (row.number,row.volume,row.cpuid)
        if key not in seen:
            seen.add(key); unique.append(row)
    return unique

def _format(label: str, result: ExternalToolResult) -> str:
    return f'--- {label} ---\nCommand: {result.args!r}\nWorking directory: {result.cwd}\nElapsed: {result.elapsed_seconds:.3f}s\nCompletion: {result.completion_reason}\nTerminated: {result.process_was_terminated}\nChanged: {result.staged_file_changed}\nSHA before: {result.staged_sha256_before}\nSHA after: {result.staged_sha256_after}\nReturn code: {result.returncode}\n\n{result.stdout}'

def remove_06f2(path: Path, mmtool: Path) -> dict:
    initial = scan_06f2_microcode(path.read_bytes())
    if not initial:
        return {'initial_count':0,'removed_count':0,'final_count':0,'diagnostic':'already absent'}
    diagnostics: list[str] = []; removed = 0
    for iteration in range(16):
        listed = run_mmtool(mmtool, path, ['/p'], 'list', output_markers=('CPU Patch Information','Total Patch Size:'))
        diagnostics.append(_format(f'MMTool /p iteration {iteration + 1}', listed))
        rows = parse_rows(listed.stdout)
        if not rows:
            if scan_06f2_microcode(path.read_bytes()):
                raise RuntimeError('06F2 remains but completed MMTool table contains no parseable row')
            break
        before = len(scan_06f2_microcode(path.read_bytes())); row = rows[0]; expected = before - 1
        def valid(candidate: Path) -> bool:
            return len(scan_06f2_microcode(candidate.read_bytes())) == expected
        deleted = run_mmtool(mmtool, path, ['/d','/p',row.number,row.volume], 'modify', output_markers=('Total Patch Size:',), staged_validator=valid)
        diagnostics.append(_format(f'Delete CPUID={row.cpuid}, Rev={row.revision}, No={row.number}, Vol={row.volume}', deleted))
        after = len(scan_06f2_microcode(path.read_bytes()))
        if after != expected:
            raise RuntimeError(f'expected {expected} 06F2 entries after deletion, found {after}')
        removed += before - after
        if after == 0:
            break
    final = len(scan_06f2_microcode(path.read_bytes()))
    if final:
        raise RuntimeError(f'{final} 06F2 entries remain')
    return {'initial_count':len(initial),'removed_count':removed,'final_count':final,'diagnostic':'\n\n'.join(diagnostics)}
