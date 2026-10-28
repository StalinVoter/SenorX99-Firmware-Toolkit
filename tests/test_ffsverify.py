from pathlib import Path
from types import SimpleNamespace
import json
import struct
from unittest.mock import patch
import unittest
import tempfile
import inspect

def cases(names, values):
    def decorate(fn):
        fn.cases=(names,values)
        return fn
    return decorate
from x99toolkit.ffsverify import relocated_tbu, verify_replacement
from x99toolkit import replacer
from x99toolkit.diagnostics import diagnostic_session
from x99toolkit.models import BuildReport
from x99toolkit.firmware import find_ffs_by_guid
from x99toolkit.constants import TBU_TARGET_GUID

DRIVER=(Path(__file__).parent/'fixtures/ser8989_S3TurboHack-40-40-40.ffs').read_bytes()
OFFSET=0xDB16B8
SIZE=0x1000000
# Independent reference offsets from the supplied driver's relocation directory.
FIXUPS=(0x298,0x29C,0x2A6,0x2FA,0x308,0x325,0x357,0x37B,0x4B7,0x523,0x6A4)


def reference(delta=0x200):
    b=bytearray(DRIVER)
    for offset in (0x128,)+FIXUPS:
        struct.pack_into('<I',b,offset,(struct.unpack_from('<I',b,offset)[0]+delta)&0xFFFFFFFF)
    return bytes(b)


def test_real_driver_exact_relocation_and_no_other_edits():
    actual,info=relocated_tbu(DRIVER,OFFSET,SIZE)
    assert actual==reference()
    assert info['changed_bytes']==12 and len(info['relocations'])==11
    assert verify_replacement(DRIVER,actual,OFFSET,SIZE,True)['delta_u32']==512


@cases('offset',(0,16,17,23,0x129,0x299,0x400,0x1C00,len(DRIVER)-1))
def test_rejects_every_kind_of_unexpected_change(offset):
    bad=bytearray(reference());bad[offset]^=1
    with unittest.TestCase().assertRaises(ValueError): verify_replacement(DRIVER,bytes(bad),OFFSET,SIZE,True)


def test_rejects_wrong_relocation_and_unrelocated_payload():
    for bad in (reference(0x100),DRIVER,reference()+b'\0',reference()[:-1]):
        with unittest.TestCase().assertRaises(ValueError): verify_replacement(DRIVER,bad,OFFSET,SIZE,True)


def test_zero_and_negative_delta():
    for delta in (0,-0x200):
        actual,info=relocated_tbu(DRIVER,OFFSET-0x200+delta,SIZE)
        assert actual==reference(delta)
        verify_replacement(DRIVER,actual,OFFSET-0x200+delta,SIZE,True)


def test_non_tbu_remains_exact():
    verify_replacement(DRIVER,DRIVER,OFFSET,SIZE,False)
    with unittest.TestCase().assertRaises(ValueError): verify_replacement(DRIVER,reference(),OFFSET,SIZE,False)


@cases('offset,value',[(0x1B78,0xF0),(0x1B79,0xF0)])
def test_malformed_relocations_fail_closed(offset,value):
    b=bytearray(DRIVER);b[offset]=value
    with unittest.TestCase().assertRaises((ValueError,struct.error)): relocated_tbu(bytes(b),OFFSET,SIZE)


def test_duplicate_relocation_fails_closed():
    b=bytearray(DRIVER);b[0x1B7E:0x1B80]=b[0x1B7C:0x1B7E]
    with unittest.TestCase().assertRaises(ValueError): relocated_tbu(bytes(b),OFFSET,SIZE)


@cases('mode',('valid','corrupt','helper-error','missing-target'))
def test_rebuild_failure_evidence_survives_temp_cleanup(tmp_path,mode):
    import tempfile
    # Small synthetic FV containing the real driver, used solely to exercise
    # the production wrapper. External helper execution is explicitly mocked.
    romsize=0x20000; fvstart=0x1000; offset=fvstart+0x48
    source=bytearray(b'\xff'*romsize)
    source[fvstart+32:fvstart+40]=(0x10000).to_bytes(8,'little')
    source[fvstart+40:fvstart+44]=b'_FVH'
    source[fvstart+48:fvstart+50]=(0x48).to_bytes(2,'little')
    mapped,_=relocated_tbu(DRIVER,offset,romsize)
    source[offset:offset+len(mapped)]=mapped
    incoming=tmp_path/'driver.ffs';incoming.write_bytes(DRIVER)
    report=BuildReport('input','output')
    def fake_run(command,**kwargs):
        image=bytearray(Path(command[1]).read_bytes())
        result,_=relocated_tbu(DRIVER,offset,romsize)
        image[offset:offset+len(result)]=result
        if mode=='corrupt': image[offset+0x400]^=1
        if mode=='missing-target': image[offset]^=1
        Path(command[command.index('-o')+1]).write_bytes(image)
        return SimpleNamespace(returncode=9 if mode=='helper-error' else 0,stdout='File replaced')
    with diagnostic_session(tmp_path/'result.ROM',report) as evidence, tempfile.TemporaryDirectory() as temp:
        inp=Path(temp)/'input.ROM';out=Path(temp)/'output.ROM';inp.write_bytes(source)
        with patch.object(replacer,'run_tool',side_effect=fake_run), patch.object(replacer,'validate_uefireplace'), patch.object(replacer,'capture_fit_rebuild_reference',return_value={}), patch.object(replacer,'repair_fit_rebuild_damage',return_value={'passed':True}):
            if mode=='valid':
                result=replacer.replace_ffs(inp,incoming,out,TBU_TARGET_GUID,Path('UEFIReplace.exe'),allow_tbu_relocation=True)
                assert result['validation']['mode']=='verified-pe32-relocation'
            else:
                with unittest.TestCase().assertRaises(RuntimeError): replacer.replace_ffs(inp,incoming,out,TBU_TARGET_GUID,Path('UEFIReplace.exe'),allow_tbu_relocation=True)
    operation=next(evidence.iterdir());record=json.loads((operation/'operation.json').read_text())
    assert record['status']==('PASS' if mode=='valid' else 'FAILED')
    assert any(e['event']=='External tool completed' for e in record['events'])
    assert (operation/'expected.ffs').read_bytes()==DRIVER
    if mode!='valid':
        assert (operation/'PREIMAGE-NOT-FOR-FLASHING.bin').read_bytes()==source
        assert (operation/'REJECTED-NOT-FOR-FLASHING.bin').stat().st_size==romsize
        assert (operation/'DO-NOT-FLASH.txt').exists()
    if mode=='corrupt':
        rows=(operation/'relocated-vs-actual.tsv').read_text().splitlines()
        assert len(rows)==2 and rows[1].startswith('00000400\t')
        assert (operation/'actual.ffs').is_file()


class VerifierTests(unittest.TestCase):
    pass


def _install_cases():
    for name,fn in list(globals().items()):
        if not name.startswith('test_') or not callable(fn): continue
        names,values=getattr(fn,'cases',('',[None]))
        parameters=[n.strip() for n in names.split(',') if n]
        for index,value in enumerate(values):
            valueset=(value,) if len(parameters)==1 else value
            arguments=dict(zip(parameters,valueset)) if parameters else {}
            def run(self,fn=fn,arguments=arguments):
                with tempfile.TemporaryDirectory() as temp:
                    args=dict(arguments)
                    if 'tmp_path' in inspect.signature(fn).parameters: args['tmp_path']=Path(temp)
                    fn(**args)
            setattr(VerifierTests,name+'_'+str(index),run)
        del globals()[name]

_install_cases()
if __name__=='__main__': unittest.main()

class FailureUiTests(unittest.TestCase):
    def test_failed_report_and_diagnostic_buttons_are_enabled(self):
        # Execute the actual handler without requiring a Qt installation.
        import ast
        from types import SimpleNamespace
        path=Path(__file__).parents[1]/'x99toolkit/gui.py'
        tree=ast.parse(path.read_text())
        window=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Window')
        handler=next(n for n in window.body if isinstance(n,ast.FunctionDef) and n.name=='_build_finished')
        with tempfile.TemporaryDirectory() as temp:
            txt=Path(temp)/'report.txt';txt.write_text('failed report')
            globals_={'Path':Path,'report_paths':lambda p:(None,txt)}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[handler],type_ignores=[])),str(path),'exec'),globals_)
            class Button:
                def setEnabled(self,value): self.enabled=value
            banner=SimpleNamespace(setText=lambda s:None,setStyleSheet=lambda s:None)
            ui=SimpleNamespace(build_btn=Button(),open_output_btn=Button(),open_report_btn=Button(),open_diagnostics_btn=Button(),build_state_banner=banner)
            report=BuildReport('input','output');report.error='rejected';report.tool_output['Diagnostic evidence']=temp
            globals_['_build_finished'](ui,report,'')
            assert ui.open_report_btn.enabled and ui.open_output_btn.enabled and ui.open_diagnostics_btn.enabled
