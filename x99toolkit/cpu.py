"""Host processor information for recommendations, never firmware eligibility."""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
import re
import subprocess
from . import nativeprocess

OC_MODELS = frozenset(('E5-1650 v3', 'E5-1660 v3', 'E5-1680 v3',
    'i7-5820K', 'i7-5930K', 'i7-5960X', 'i7-6800K', 'i7-6850K', 'i7-6900K', 'i7-6950X'))


def model_from_name(name: str) -> str:
    text = re.sub(r'\((?:R|TM)\)|[®™]', '', name.upper())
    xeon = re.search(r'\bE5[\s-]*(\d{4}[A-Z]?)\s*[- ]?\s*V\s*([34])\b', text)
    if xeon:
        return f'E5-{xeon[1]} v{xeon[2]}'
    core = re.search(r'\bI7[\s-]*(\d{4,5}[A-Z]{0,2})\b', text)
    return 'i7-' + core[1] if core else ''


@dataclass(frozen=True)
class Processor:
    name: str
    manufacturer: str = ''
    processor_id: str = ''

    @property
    def model(self):
        return model_from_name(self.name)


@dataclass(frozen=True)
class CpuDetection:
    processors: tuple[Processor, ...] = ()
    error: str = ''

    @property
    def display_name(self):
        return ' / '.join(dict.fromkeys(p.model or p.name for p in self.processors))

    @property
    def recommend_tbu(self):
        return bool(self.processors) and not self.error and all(
            re.fullmatch(r'E5-26\d{2}[A-Z]? v3', p.model) for p in self.processors)

    @property
    def recommend_oc(self):
        return bool(self.processors) and not self.error and all(p.model in OC_MODELS for p in self.processors)


def parse_processors(payload: str) -> CpuDetection:
    rows = json.loads(payload.lstrip('\ufeff'))
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise ValueError('Empty processor result')
    processors = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('Name'), str) or not row['Name'].strip():
            raise ValueError('Incomplete processor result')
        processors.append(Processor(' '.join(row['Name'].split()), str(row.get('Manufacturer') or ''), str(row.get('ProcessorId') or '')))
    return CpuDetection(tuple(processors))


def detect_cpu() -> CpuDetection:
    if os.name != 'nt':
        return CpuDetection(error='not-windows')
    command = ("$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "ConvertTo-Json -Compress -InputObject @(Get-CimInstance -ClassName Win32_Processor "
        "-ErrorAction Stop | Select-Object Name,Manufacturer,ProcessorId)")
    try:
        result = nativeprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8-sig', errors='replace',
            timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return parse_processors(result.stdout) if result.returncode == 0 else CpuDetection(error='query-failed')
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return CpuDetection(error='query-failed')
