# Custom runtime hooks run before PyInstaller's installed Qt runtime hooks.
from x99toolkit.runtime_bootstrap import prepare_runtime, fail_startup
try:
    prepare_runtime()
except BaseException as exc:
    raise SystemExit(fail_startup(exc))
