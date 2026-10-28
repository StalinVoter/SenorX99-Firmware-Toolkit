"""Check automatic SenorSpace replacement using a disposable offline build."""
from pathlib import Path
import shutil
import tempfile

from .firmware import sha256_file
from .pipeline import build_rom
from .tools import discover_tools
from .sessionlog import log


def inspect_splash_replacement(source: Path, expected_sha256: str, image: Path | None = None) -> dict:
    tools = discover_tools()
    result = dict(supported=False, state='unavailable', source_sha256=expected_sha256,
                  mode='automatic SenorSpace', reason='Verified ChangeLogo is missing. Install it in Required tools.')
    if tools.changelogo is None:
        return result
    try:
        if image is not None:
            result['image_sha256'] = sha256_file(image)
            result['image_path'] = str(image.resolve())
            result['mode'] = 'custom BMP'
        result['tool_sha256'] = sha256_file(tools.changelogo)
        with tempfile.TemporaryDirectory(prefix='SenorX99-logo-check-') as temporary:
            folder = Path(temporary)
            snapshot = folder / ('source' + source.suffix)
            shutil.copy2(source, snapshot)
            if sha256_file(snapshot) != expected_sha256:
                raise ValueError('The source ROM changed during inspection. Reopen it before building.')
            # This is the existing full offline logo pipeline: inventory/format
            # selection, fit checks, pixel verification, size/FIT validation and
            # non-target preservation. No dump/flash function is invoked.
            trial = build_rom(snapshot, folder / 'trial.ROM', tools,
                              apply_splash=True, splash_image=image, repair_fit=True)
            if not trial.success:
                raise ValueError(trial.error or 'The trial logo replacement did not pass validation.')
            if sha256_file(source) != expected_sha256:
                raise ValueError('The source ROM changed during the logo check. Reopen it before building.')
            if sha256_file(tools.changelogo) != result['tool_sha256']:
                raise ValueError('ChangeLogo changed during the logo check. Recheck the installed tools.')
            if image is not None and sha256_file(image) != result['image_sha256']:
                raise ValueError('The custom image changed during the logo check. Choose it again.')
            result.update(supported=True, state='verified',
                          reason='Automatic SenorSpace replacement passed a temporary-copy build, including pixel and firmware integrity checks.',
                          trial_output_sha256=trial.output_sha256,
                          steps=[dict(name=s.name, status=s.status, details=s.details) for s in trial.steps])
            result['already_present'] = bool(trial.options.get('splash_already_present'))
            if result['already_present']:
                result['reason'] = 'SenorSpace is already installed. The existing logo images were checked and match the requested images.'
    except Exception as exc:
        result.update(supported=False, state='unverified', reason=f'{type(exc).__name__}: {exc}')
    log('SPLASH PREFLIGHT', f"{source}: {result['state']}; {result['reason']}")
    return result
