from __future__ import annotations


from pathlib import Path


import os


import shutil


import subprocess


import tempfile


import pytest


from x99toolkit.constants import SECURE_BOOT_GUIDS, PATCH_TARGETS, BUNDLED_DONOR_SHA256, UEFIREPLACE_SHA256, UEFIREPLACE_DOWNLOAD_URL


from x99toolkit.firmware import find_ffs_by_guid, find_enclosing_fv, sha256_file


from x99toolkit.fit import inspect_fit, parse_fit, capture_fit_rebuild_reference, repair_fit_rebuild_damage


from x99toolkit.pipeline import default_output, inspect_rom


from x99toolkit.secureboot import inspect_secure_boot, inspect_donor_ffs, load_bundled_donors


from x99toolkit.tools import validate_uefireplace


from x99toolkit import replacer as replacer_mod


ROOT = Path(__file__).resolve().parents[1]


DATA = Path(os.environ.get('X99_SB_TEST_DATA', '/nonexistent'))


STOCK_CORPUS = (
    'x99xd3-260810-stock(1).ROM',
    'XD3VRM-260731-Original(1).ROM',
    'v9s-260826.ROM',
    'qiyida-x99-k9s-stock.rom',
    'Jginue ARGB backup.rom',
)


def available(name: str) -> Path | None:
    p = DATA / name
    return p if p.is_file() else None


def test_uefireplace_exact_version_is_pinned():
    assert UEFIREPLACE_SHA256 == 'ab05d53fcac19651818f4ee4505813b10badec7a10d141836fff3bba8964ed8b'
    assert UEFIREPLACE_DOWNLOAD_URL == 'https://github.com/LongSoft/UEFITool/releases/download/0.28.0/UEFIReplace_0.28.0_win32.zip'
    instructions = (ROOT / 'tools' / 'README.txt').read_text(encoding='utf-8')
    assert UEFIREPLACE_DOWNLOAD_URL in instructions
    assert UEFIREPLACE_SHA256 in instructions


def test_bundled_donors_are_exact_and_structurally_valid():
    donors = load_bundled_donors(ROOT)
    assert set(donors) == set(PATCH_TARGETS)
    for name, path in donors.items():
        info = inspect_donor_ffs(path, name)
        assert info['ffs_sha256'] == BUNDLED_DONOR_SHA256[name]
        assert info['ffs_type'] == 0x02
        assert info['signature_lists']
    assert inspect_donor_ffs(donors['PkVar'],'PkVar')['evidence']['ami_test_pk'] is False
    assert inspect_donor_ffs(donors['KekVar'],'KekVar')['evidence']['kek_2023'] is True
    db = inspect_donor_ffs(donors['dbVar'],'dbVar')['evidence']
    assert db['db_windows_2023'] and db['db_microsoft_2023'] and db['db_optionrom_2023']


@pytest.mark.parametrize('name', STOCK_CORPUS)
def test_distinct_x99_board_corpus_uses_generic_patchable_ami_layout(name):
    p = available(name)
    if p is None: pytest.skip('reference ROM not supplied')
    r = inspect_secure_boot(p)
    assert r['patchable'] is True
    assert r['errors'] == []
    assert r['recommended_targets'] == ['PkVar','KekVar','dbVar']
    assert r['has_ami_test_pk'] is True
    assert r['has_complete_2023_set'] is False
    for var in ('PkVar','KekVar','dbVar','dbxVar'):
        assert r['variables'][var]['count'] == 1
        assert r['variables'][var]['parse_ok'] is True
        assert r['variables'][var]['signature_lists']


@pytest.mark.parametrize('name', STOCK_CORPUS)
def test_distinct_x99_board_corpus_has_valid_fv_mutation_boundary(name):
    p = available(name)
    if p is None: pytest.skip('reference ROM not supplied')
    data = p.read_bytes()
    for var in PATCH_TARGETS:
        hit = find_ffs_by_guid(data, SECURE_BOOT_GUIDS[var])
        assert len(hit) == 1
        fv = find_enclosing_fv(data, hit[0].offset)
        assert fv is not None
        assert fv.offset <= hit[0].offset < fv.end
        # All current stock references place these variables in the same 5 MiB FV.
        assert fv.size == 0x500000


@pytest.mark.parametrize('name', STOCK_CORPUS)
def test_distinct_stock_corpus_fit_is_pass_before_rebuild(name):
    p = available(name)
    if p is None: pytest.skip('reference ROM not supplied')
    r = inspect_fit(p)
    assert r['supported'] is True
    assert r['status'] == 'PASS'


def test_dbx_absence_does_not_block_pk_kek_db_patch_planning():
    p = available('v9s-260826.ROM')
    if p is None: pytest.skip('reference ROM not supplied')
    data = bytearray(p.read_bytes())
    hits = find_ffs_by_guid(data, SECURE_BOOT_GUIDS['dbxVar'])
    assert len(hits) == 1
    # Break only the dbx file GUID so the patcher sees it as absent. PK/KEK/db
    # structures remain byte-identical and must remain patchable.
    data[hits[0].offset] ^= 0x01
    with tempfile.TemporaryDirectory() as td:
        q = Path(td)/'no-dbx.rom'; q.write_bytes(data)
        r = inspect_secure_boot(q)
    assert r['patchable'] is True
    assert r['recommended_targets'] == ['PkVar','KekVar','dbVar']
    assert r['variables']['dbxVar']['count'] == 0
    assert r['warnings']


def test_duplicate_required_variable_fails_closed():
    p = available('v9s-260826.ROM')
    if p is None: pytest.skip('reference ROM not supplied')
    data = p.read_bytes()
    pk = find_ffs_by_guid(data, SECURE_BOOT_GUIDS['PkVar'])[0]
    with tempfile.TemporaryDirectory() as td:
        q = Path(td)/'duplicate-pk.rom'; q.write_bytes(data + pk.data)
        r = inspect_secure_boot(q)
    assert r['patchable'] is False
    assert r['variables']['PkVar']['count'] == 2


def test_partially_updated_reference_plans_only_missing_pk():
    p = available('xd3260608-tbu_sb23_logo.rom')
    if p is None: pytest.skip('reference ROM not supplied')
    r = inspect_secure_boot(p)
    assert r['patchable'] is True
    assert r['has_complete_2023_set'] is True
    assert r['has_ami_test_pk'] is True
    assert r['recommended_targets'] == ['PkVar']


def test_completed_v9s_reference_is_recognized_current():
    p = available('v9s-260826_SB2023_OPT_LOGO.ROM')
    if p is None: pytest.skip('reference ROM not supplied')
    r = inspect_secure_boot(p)
    assert r['patchable'] is True
    assert r['recommended_targets'] == []
    assert r['has_complete_2023_set'] is True
    assert r['has_ami_test_pk'] is False
    assert r['variables']['PkVar']['ffs_sha256'] == BUNDLED_DONOR_SHA256['PkVar']
    assert r['variables']['KekVar']['ffs_sha256'] == BUNDLED_DONOR_SHA256['KekVar']
    assert r['variables']['dbVar']['ffs_sha256'] == BUNDLED_DONOR_SHA256['dbVar']


def test_completed_v9s_reference_preserves_original_dbx_exactly():
    stock = available('v9s-260826.ROM')
    final = available('v9s-260826_SB2023_OPT_LOGO.ROM')
    if stock is None or final is None: pytest.skip('V9S stock/final references not supplied')
    a = find_ffs_by_guid(stock.read_bytes(), SECURE_BOOT_GUIDS['dbxVar'])
    b = find_ffs_by_guid(final.read_bytes(), SECURE_BOOT_GUIDS['dbxVar'])
    assert len(a) == len(b) == 1
    assert a[0].data == b[0].data
    assert inspect_fit(final)['status'] == 'PASS'


def _make_stale_copy(stock: Path, target: Path, delta: int = -0x110) -> None:
    data = bytearray(stock.read_bytes())
    table = parse_fit(bytes(data))
    for entry in table.type1_entries:
        data[entry.offset:entry.offset+8] = (entry.address + delta).to_bytes(8, 'little')
    target.write_bytes(data)


def test_v15_no_rebuild_damage_leaves_preexisting_stale_input_byte_identical():
    stock = available('x99xd3-260810-stock(1).ROM')
    if stock is None: pytest.skip('compact reference ROM not supplied')
    with tempfile.TemporaryDirectory() as td:
        stale = Path(td) / 'stale.rom'
        _make_stale_copy(stock, stale)
        before_bytes = stale.read_bytes()
        reference = capture_fit_rebuild_reference(stale)
        assert reference['supported'] is True
        assert reference['status'] == 'STALE'
        result = repair_fit_rebuild_damage(stale, reference)
        assert result['passed'] is True
        assert result['changed'] is False
        assert stale.read_bytes() == before_bytes
        assert inspect_fit(stale)['status'] == 'STALE'


def test_v15_repairs_pass_input_only_back_to_its_original_valid_relationships():
    stock = available('x99xd3-260810-stock(1).ROM')
    if stock is None: pytest.skip('compact reference ROM not supplied')
    with tempfile.TemporaryDirectory() as td:
        damaged = Path(td) / 'damaged.rom'
        _make_stale_copy(stock, damaged, -0x110)
        assert inspect_fit(stock)['status'] == 'PASS'
        assert inspect_fit(damaged)['status'] == 'STALE'
        reference = capture_fit_rebuild_reference(stock)
        result = repair_fit_rebuild_damage(damaged, reference)
        assert result['passed'] is True
        assert result['changed'] is True
        assert result['method'] == 'preserve-preexisting-fit-relationship'
        assert inspect_fit(damaged)['status'] == 'PASS'
        assert damaged.read_bytes() == stock.read_bytes()


def test_v15_repairs_only_additional_damage_on_preexisting_stale_input():
    stock = available('x99xd3-260810-stock(1).ROM')
    if stock is None: pytest.skip('compact reference ROM not supplied')
    with tempfile.TemporaryDirectory() as td:
        stale_before = Path(td) / 'stale-before.rom'
        stale_after_rebuild = Path(td) / 'stale-after-rebuild.rom'
        _make_stale_copy(stock, stale_before, -0x110)
        _make_stale_copy(stock, stale_after_rebuild, -0x220)
        assert inspect_fit(stale_before)['status'] == 'STALE'
        reference = capture_fit_rebuild_reference(stale_before)
        assert all(
            item['preexisting_delta'] == 0x110
            for item in reference['type1_entries']
            if item['status'] == 'STALE'
        )
        result = repair_fit_rebuild_damage(stale_after_rebuild, reference)
        assert result['passed'] is True
        assert result['changed'] is True
        # v0.9 must undo only the extra -0x110 damage, not repair the original
        # stale condition all the way back to the stock PASS image.
        assert stale_after_rebuild.read_bytes() == stale_before.read_bytes()
        assert stale_after_rebuild.read_bytes() != stock.read_bytes()
        assert inspect_fit(stale_after_rebuild)['status'] == 'STALE'


@pytest.mark.parametrize('name', STOCK_CORPUS)
def test_v15_selective_repair_restores_synthetic_rebuild_damage_across_corpus(name):
    stock = available(name)
    if stock is None: pytest.skip('reference ROM not supplied')
    with tempfile.TemporaryDirectory() as td:
        damaged = Path(td) / 'damaged.rom'
        _make_stale_copy(stock, damaged, -0x110)
        # The synthetic damage must remain uniquely understandable before it is
        # used as a regression fixture.
        assert inspect_fit(damaged)['status'] == 'STALE'
        reference = capture_fit_rebuild_reference(stock)
        result = repair_fit_rebuild_damage(damaged, reference)
        assert result['passed'] is True
        assert result['changed'] is True
        assert damaged.read_bytes() == stock.read_bytes()
        assert inspect_fit(damaged)['status'] == 'PASS'


def test_v0962_xd3_problematic_top_pad_is_outside_secureboot_target_fv():
    stock = available('x99xd3-260810-stock(1).ROM')
    if stock is None: pytest.skip('compact XD3 reference ROM not supplied')
    data = stock.read_bytes()
    hit = find_ffs_by_guid(data, SECURE_BOOT_GUIDS['PkVar'])
    assert len(hit) == 1
    fv = find_enclosing_fv(data, hit[0].offset)
    assert fv is not None
    assert (fv.offset, fv.end) == (0x890000, 0xD90000)
    # The real v0.961 hardware failure started at 0xFFD000, inside another FV.
    # This offset must therefore never be accepted as part of a PkVar rebuild.
    assert not (fv.offset <= 0xFFD000 < fv.end)
    assert any(b not in (0x00, 0xFF) for b in data[0xFFD000:0xFFD020])

