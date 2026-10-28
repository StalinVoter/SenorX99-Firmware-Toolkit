"""Execute the real startup statements without launching Windows executables."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = (os.environ.get('X99_POWERSHELL') or
              shutil.which('powershell.exe') or shutil.which('pwsh'))
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='PowerShell is not installed')


def run_script(path, cwd, *arguments):
    result = subprocess.run(
        [POWERSHELL, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-File', str(path), *map(str, arguments)],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize('arguments', ['omitted', 'blank', 'relative', 'absolute'])
@pytest.mark.parametrize('empty_script_root', [False, True])
def test_runtime_paths_before_windows_operations(tmp_path, arguments, empty_script_root):
    folder = tmp_path / 'source folder with spaces'
    folder.mkdir()
    cwd = tmp_path / 'different working folder'
    cwd.mkdir()
    source = (ROOT / 'TEST-PACKAGED-RUNTIME.ps1').read_text(encoding='utf-8')
    prefix = source.split('# Windows-only packaged tests begin here.', 1)[0]
    assert prefix != source  # Exercise production statements up to the OS boundary.
    if empty_script_root:
        prefix = prefix.replace('$Root = $PSScriptRoot', "$Root = ''", 1)
    script = folder / 'TEST-PACKAGED-RUNTIME.ps1'
    script.write_text(prefix + "\n@{Distribution=$Distribution; Reports=$Reports} | ConvertTo-Json -Compress\n")
    if arguments in ('omitted', 'blank'):
        params = [] if arguments == 'omitted' else ['-Distribution', ' ', '-Reports', ' ']
        expected = {'Distribution': folder / 'dist', 'Reports': folder / 'build'}
    elif arguments == 'relative':
        params = ['-Distribution', 'custom dist', '-Reports', 'custom reports']
        expected = {'Distribution': cwd / 'custom dist', 'Reports': cwd / 'custom reports'}
    else:
        expected = {'Distribution': tmp_path / 'explicit dist', 'Reports': tmp_path / 'explicit reports'}
        params = ['-Distribution', expected['Distribution'], '-Reports', expected['Reports']]
    actual = run_script(script, cwd, *params)
    assert {key: Path(value) for key, value in actual.items()} == expected


def test_powershell_syntax_and_script_parameter_defaults(tmp_path):
    script = tmp_path / 'parse.ps1'
    script.write_text(r'''
$ErrorActionPreference = 'Stop'
$Count = 0
foreach ($File in Get-ChildItem -LiteralPath $args[0] -Filter '*.ps1') {
    $Tokens = $null; $Errors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile($File.FullName, [ref]$Tokens, [ref]$Errors)
    if ($Errors.Count) { throw ($Errors | Out-String) }
    if ($null -ne $Ast.ParamBlock) {
        $Unsafe = $Ast.ParamBlock.FindAll({ param($Node)
            $Node -is [Management.Automation.Language.VariableExpressionAst] -and
            $Node.VariablePath.UserPath -in @('PSScriptRoot', 'PSCommandPath', 'MyInvocation')
        }, $true)
        if ($Unsafe.Count) { throw "Script automatic variable in parameter defaults: $File" }
    }
    $Count++
}
@{count=$Count} | ConvertTo-Json -Compress
''', encoding='utf-8')
    result = run_script(script, tmp_path, ROOT)
    assert result['count'] == len(list(ROOT.glob('*.ps1')))
