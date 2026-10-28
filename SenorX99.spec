# Build both EXEs with a first runtime hook and an audited native DLL manifest.
from pathlib import Path
import sys
root = Path(SPECPATH)
sys.path.insert(0, str(root))
from scripts.build_support import check_environment, seal_analysis
check_environment()

for kind in ('gui', 'cli'):
    name = 'SenorX99' + ('-CLI' if kind == 'cli' else '')
    hidden = ['x99toolkit.' + kind]
    if kind == 'gui':
        hidden += ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets']
    else:
        hidden += ['x99toolkit.fpt_elevation']
    a = Analysis(
        [str(root / ('SenorX99-' + kind.upper() + '.py'))],
        pathex=[str(root)], binaries=[], datas=[], hiddenimports=hidden,
        hookspath=[], hooksconfig={},
        runtime_hooks=[str(root / 'hooks' / 'runtime_first.py')],
        excludes=['PyQt5', 'PyQt6', 'PySide2'] + (['PySide6', 'shiboken6'] if kind == 'cli' else []),
        noarchive=False,
    )
    seal_analysis(a, kind)
    pyz = PYZ(a.pure)
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name,
              debug=False, strip=False, upx=False, console=(kind == 'cli'),
              uac_admin=False)
