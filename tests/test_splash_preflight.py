from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import pytest
from x99toolkit import splash_preflight as preflight, fpt


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    source = tmp_path / 'original.ROM'; source.write_bytes(b'original firmware fixture')
    tool = tmp_path / 'ChangeLogo.exe'; tool.write_bytes(b'verified tool fixture')
    monkeypatch.setattr(preflight, 'discover_tools', lambda: SimpleNamespace(changelogo=tool))
    monkeypatch.setattr(fpt, 'dump_bios', lambda *a, **k: pytest.fail('logo preflight must not access BIOS'))
    monkeypatch.setattr(fpt, 'flash_bios_with_recovery', lambda *a, **k: pytest.fail('logo preflight must not flash BIOS'))
    return source, sha256(source.read_bytes()).hexdigest()


def test_no_verified_changelogo_never_starts_trial(monkeypatch, inputs):
    source, digest = inputs
    monkeypatch.setattr(preflight, 'discover_tools', lambda: SimpleNamespace(changelogo=None))
    monkeypatch.setattr(preflight, 'build_rom', lambda *a, **k: pytest.fail('missing tool executed'))
    result = preflight.inspect_splash_replacement(source, digest)
    assert not result['supported'] and result['state'] == 'unavailable'


@pytest.mark.parametrize('success', [False, True])
def test_trial_uses_full_logo_pipeline_on_disposable_snapshot_only(monkeypatch, inputs, success):
    source, digest = inputs
    before = source.read_bytes(); temporary_paths = []
    def build(snapshot, output, tools, **kwargs):
        assert snapshot != source and snapshot.parent == output.parent
        assert snapshot.read_bytes() == before
        assert kwargs == dict(apply_splash=True, splash_image=None, repair_fit=True)
        temporary_paths.extend([snapshot, output])
        snapshot.write_bytes(b'only the temporary copy may change')
        output.write_bytes(b'trial output')
        return SimpleNamespace(success=success, error='fixture verification failure' if not success else '',
                               output_sha256='trial-hash', steps=[], options={})
    monkeypatch.setattr(preflight, 'build_rom', build)
    result = preflight.inspect_splash_replacement(source, digest)
    assert result['supported'] is success
    assert result['state'] == ('verified' if success else 'unverified')
    assert source.read_bytes() == before
    assert all(not p.exists() for p in temporary_paths)


def test_changed_source_is_not_default_eligible(monkeypatch, inputs):
    source, digest = inputs
    source.write_bytes(b'changed after inspection')
    monkeypatch.setattr(preflight, 'build_rom', lambda *a, **k: pytest.fail('stale source must be refused first'))
    result = preflight.inspect_splash_replacement(source, digest)
    assert not result['supported']
    assert 'changed during inspection' in result['reason']


def test_pipeline_exception_leaves_inspection_usable(monkeypatch, inputs):
    source, digest = inputs
    def failed(*a, **k): raise OSError('fixture tool unavailable')
    monkeypatch.setattr(preflight, 'build_rom', failed)
    result = preflight.inspect_splash_replacement(source, digest)
    assert not result['supported'] and 'tool unavailable' in result['reason']


def test_already_installed_senorspace_is_a_successful_check(monkeypatch, inputs):
    source, digest = inputs
    monkeypatch.setattr(preflight, 'build_rom', lambda *a, **k: SimpleNamespace(
        success=True, output_sha256=digest, steps=[],
        options={'splash_already_present': True}))
    result = preflight.inspect_splash_replacement(source, digest)
    assert result['supported'] and result['state'] == 'verified'
    assert result['already_present'] is True
    assert result['reason'].startswith('SenorSpace is already installed.')
