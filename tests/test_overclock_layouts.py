from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from x99toolkit import overclock as oc
from x99toolkit.cstates import parse_guided_lzma_ffs, _locate_amitse_cstate_layout, _semantic_layout
from x99toolkit.firmware import parse_ffs_at

FIXTURES = Path(__file__).parent / "fixtures"

def state(layout):
    parsed = parse_guided_lzma_ffs(parse_ffs_at((FIXTURES / ("oc_" + layout + ".ffs")).read_bytes(), 0), 0x18)
    target, _ = _semantic_layout(parsed.nested_sections, 0x18)
    return _locate_amitse_cstate_layout(parsed, target)

def test_compact_offsets_and_existing_values():
    s = state("compact")
    entries = oc._patches_for_state(s)
    assert [e["offset"] for e in entries] == [0x1337c,0x5b024,0x5b150,0x5b151,0x5bee4,0x5c661,0x5c6aa,0x5c6ab]
    assert [s.parsed.decompressed[e["offset"]] for e in entries] == [5,5,0,1,5,1,0,8]

@pytest.mark.parametrize("layout", ["compact", "classic"])
def test_payload_is_exact_and_idempotent(layout, tmp_path):
    s = state(layout)
    with patch.object(oc, "_require_supported"), patch.object(oc, "_amitse_state", return_value=s):
        output = tmp_path / "body.bin"
        result = oc.build_overclock_amitse_body(b"", output)
    expected = bytearray(s.target.body)
    for item in oc._patches_for_state(s):
        expected[item["offset"] - s.target.offset - s.target.header_size] = item["desired"]
    assert output.read_bytes() == expected
    assert result["changed_decompressed_offsets"] == ([0x5b150] if layout == "compact" else [])
    data = bytearray(s.parsed.decompressed)
    for e in oc._patches_for_state(s): data[e["offset"]] = e["desired"]
    final = replace(s, parsed=replace(s.parsed, decompressed=bytes(data)))
    with patch.object(oc, "_require_supported"), patch.object(oc, "_amitse_state", return_value=final):
        assert not oc.build_overclock_amitse_body(b"", output)["changed"]

@pytest.mark.parametrize("offset", [0x13374,0x5b01c,0x5bedc,0x5b12c,0x5c63c,0x5c684])
def test_wrong_control_identity_rejected(offset):
    s = state("compact"); data = bytearray(s.parsed.decompressed); data[offset] ^= 1
    with pytest.raises(ValueError, match="identity mismatch"):
        oc._patches_for_state(replace(s, parsed=replace(s.parsed, decompressed=bytes(data))))

@pytest.mark.parametrize("layout,family", [("compact","xd3-compact"),("classic","xd3-classic")])
def test_family_and_layout_agree(layout,family):
    with patch.object(oc,"classify_amitse_family",return_value={"board_family":family}), patch.object(oc,"_amitse_state",return_value=state(layout)):
        assert oc._require_supported(b"")["board_family"] == family
    with patch.object(oc,"classify_amitse_family",return_value={"board_family":"unknown"}):
        with pytest.raises(ValueError): oc._require_supported(b"")

def test_unexpected_default_rejected(tmp_path):
    s = state("compact");data=bytearray(s.parsed.decompressed);data[0x5b150]=7
    s=replace(s,parsed=replace(s.parsed,decompressed=bytes(data)))
    with patch.object(oc,"_require_supported"), patch.object(oc,"_amitse_state",return_value=s):
        with pytest.raises(ValueError,match="unexpected value"):
            oc.build_overclock_amitse_body(b"",tmp_path/"bad.bin")
