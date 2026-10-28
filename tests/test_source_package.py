from hashlib import sha256
from pathlib import Path
import zipfile
import pytest
from scripts.package_source import package_source


def test_source_zip_is_complete_and_has_windows_compatible_entries(tmp_path):
    data = b'fixture source data\n'
    (tmp_path/'source.txt').write_bytes(data)
    (tmp_path/'SHA256SUMS.txt').write_text(sha256(data).hexdigest()+'  source.txt\n')
    archive = package_source(tmp_path)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.infolist()) == 2
        assert all(item.compress_type == zipfile.ZIP_STORED and item.extract_version <= 20 for item in z.infolist())
        assert z.read(archive.stem+'/source.txt') == data
    assert archive.read_bytes()[-22:-18] == b'PK\x05\x06'
    assert archive.with_suffix('.zip.sha256').read_text().startswith(sha256(archive.read_bytes()).hexdigest())
    assert not list(tmp_path.glob('*.tmp'))


def test_bad_source_inventory_does_not_publish_an_archive(tmp_path):
    (tmp_path/'source.txt').write_bytes(b'changed source')
    (tmp_path/'SHA256SUMS.txt').write_text('0'*64+'  source.txt\n')
    with pytest.raises(ValueError, match='Source hash mismatch'):
        package_source(tmp_path)
    assert not list(tmp_path.glob('*.zip'))
