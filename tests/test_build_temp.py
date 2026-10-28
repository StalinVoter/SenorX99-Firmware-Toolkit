"""Regression for an inaccessible shared pytest temporary directory on Windows."""
from pathlib import Path, PureWindowsPath
import os
import re
import subprocess
import sys


def test_build_temp_works_when_shared_pytest_directory_is_denied(tmp_path):
    builder = (Path(__file__).resolve().parents[1] / 'build_exe.bat').read_text()
    command = next(line for line in builder.splitlines() if ' -m pytest ' in line)
    configured = re.search(r'--basetemp "([^"]+)"', command).group(1)
    assert configured.startswith('%~dp0')
    project = tmp_path / 'source folder with spaces'
    build = project / 'build'
    build.mkdir(parents=True)
    base = project.joinpath(*PureWindowsPath(configured.removeprefix('%~dp0')).parts)
    assert base.parent == build and not base.exists()
    probe = tmp_path / 'test_probe.py'
    probe.write_text('def test_temp(tmp_path):\n    (tmp_path / "probe").write_text("ok")\n')
    shared_parent = tmp_path / 'shared-temp'
    shared_parent.mkdir()
    script = '''
import os, pathlib, sys
import pytest
original = os.scandir
def deny_shared(path):
    if pathlib.Path(path).name.startswith('pytest-of-'):
        raise PermissionError('Simulated denied shared pytest directory')
    return original(path)
os.scandir = deny_shared
sys.exit(pytest.main(sys.argv[1:]))
'''
    env = dict(os.environ, PYTEST_DEBUG_TEMPROOT=str(shared_parent),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    args = [sys.executable, '-c', script, str(probe), '-q', '--tb=short']
    control = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert control.returncode == 1 and 'Simulated denied shared pytest directory' in control.stdout
    fixed = subprocess.run(args + ['--basetemp', str(base)], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=30)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert '1 passed' in fixed.stdout
    assert any(path.read_text() == 'ok' for path in base.rglob('probe'))
