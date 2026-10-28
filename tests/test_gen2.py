from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
import io, os, shutil, subprocess, sys, lzma
import pytest
from x99toolkit import fpt, sessionlog, replacer
from x99toolkit.buildprogress import BuildProgressTracker
from x99toolkit.reporting import report_paths
from x99toolkit.constants import *
from x99toolkit.firmware import find_ffs_by_guid, find_enclosing_fv, select_primary_intelsetup_record
from x99toolkit.cstates import parse_guided_lzma_ffs, build_nv_internal_body, build_amitse_body, validate_nested_body
from x99toolkit.optimized import build_optimized_nv_body, build_optimized_amitse_body
from x99toolkit.inspection import inspect_rom
from x99toolkit.pipeline import build_rom
from x99toolkit.tools import ToolPaths, run_tool
ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.environ.get('X99_TEST_DATA','/nonexistent'))

def fixture(name):
    p=DATA/name
    if not p.is_file(): pytest.skip('reference ROM not supplied: '+name)
    return p


def test_k9s_mapping_evidence_is_exact():
    a=fixture('v9s-260826.ROM').read_bytes();b=fixture('qiyida-x99-k9s-stock.rom').read_bytes()
    ar=select_primary_intelsetup_record(a);br=select_primary_intelsetup_record(b)
    assert a[ar.data_offset:ar.data_offset+ar.payload_size]==b[br.data_offset:br.data_offset+br.payload_size]
    assert find_ffs_by_guid(a,TBU_TARGET_GUID)[0].data==find_ffs_by_guid(b,TBU_TARGET_GUID)[0].data
    from x99toolkit.cstates import _nv_state
    ap,ar,_,_=_nv_state(a);bp,br,_,_=_nv_state(b)
    assert ap.decompressed[ar.data_offset:ar.data_offset+ar.payload_size]==bp.decompressed[br.data_offset:br.data_offset+br.payload_size]
    aa=parse_guided_lzma_ffs(find_ffs_by_guid(a,AMITSE_SETUP_DATA_GUID)[0],AMITSE_RAW_SECTION_TYPE).decompressed
    bb=parse_guided_lzma_ffs(find_ffs_by_guid(b,AMITSE_SETUP_DATA_GUID)[0],AMITSE_RAW_SECTION_TYPE).decompressed
    assert len(aa)==len(bb)==0x791a0
    assert sum(x!=y for x,y in zip(aa,bb))==405
    for off,stock,want in V9S_AMITSE_CSTATE_PATCHES+V9S_AMITSE_OPT_PATCHES:
        assert aa[off-64:off+65]==bb[off-64:off+65]


@pytest.mark.parametrize('name,opt,layout',[('qiyida-x99-k9s-stock.rom',True,'qiyida-k9s-791a0'),('Jginue ARGB backup.rom',False,'compact-7bffc')])
def test_new_board_support_is_feature_specific(name,opt,layout):
    r=inspect_rom(fixture(name))
    assert r.tbu['cstates']['supported']
    assert r.tbu['cstates']['amitse_setup_default']['layout']==layout
    assert r.optimized['supported'] is opt
    assert r.secure_boot['patchable']
    assert r.fit['status']=='PASS'


def test_jginue_optimized_is_refused_before_output(tmp_path,monkeypatch):
    monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
    out=tmp_path/'refused.rom'
    r=build_rom(fixture('Jginue ARGB backup.rom'),out,ToolPaths(None,None,None),apply_optimized=True)
    assert not r.success and not out.exists()
    assert 'AMIBCP reference' in r.error


@pytest.mark.parametrize('options',[{}, {'apply_tbu':True,'repair_fit':True}, {'apply_optimized':True,'apply_secureboot':True}, {'apply_splash':True,'apply_overclock':True,'repair_fit':True}])
def test_progress_tracks_only_completed_plan(options):
    t=BuildProgressTracker(options)
    assert t.update('unplanned','PASS')==0
    for name in t.stages:
        before=t.current_percent
        assert t.update(name,'RUNNING')==before
        value=t.update(name,'PASS')
        assert before<=value<=100
        assert t.update(name,'PASS')==value
    assert t.current_percent==100
    t=BuildProgressTracker(options)
    t.update(t.stages[0],'PASS');before=t.current_percent
    t.update('Build','FAIL')
    for name in t.stages:t.update(name,'PASS')
    assert t.current_percent==before<100


def test_logging_raw_output_once_and_report_routes(tmp_path,monkeypatch):
    sessionlog.finish_session_log('test reset');monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
    path=sessionlog.start_session_log('Gen2-v2.6.3')
    run_tool([sys.executable,'-c','print("unique-external-output-" + "record")'])
    sessionlog.finish_session_log('test done')
    assert path.read_text().count('unique-external-output-record')==1
    assert all(p.parent==tmp_path/'LOGS' for p in report_paths(tmp_path/'elsewhere/rom.ROM'))


@pytest.mark.parametrize('builder,guid,typ',[(build_nv_internal_body,NV_INTERNAL_GUID,NV_INTERNAL_RAW_SECTION_TYPE),(build_amitse_body,AMITSE_SETUP_DATA_GUID,AMITSE_RAW_SECTION_TYPE),(build_optimized_nv_body,NV_INTERNAL_GUID,NV_INTERNAL_RAW_SECTION_TYPE),(build_optimized_amitse_body,AMITSE_SETUP_DATA_GUID,AMITSE_RAW_SECTION_TYPE)])
def test_nested_containment_preserves_current_input_and_validates_body(builder,guid,typ,tmp_path,monkeypatch):
    """Model helper output; actual Windows UEFIReplace execution is separate QA."""
    source=tmp_path/'input.rom';data=fixture('v9s-260826.ROM').read_bytes()
    # Model an earlier legitimate pipeline modification outside this FV.
    data=bytearray(data);data[0x1000]^=0x20;source.write_bytes(data)
    body=tmp_path/'body.bin';plan=builder(bytes(data),body)
    if not plan['changed']:pytest.fail('expected changed reference plan')
    original=find_ffs_by_guid(data,guid)[0]
    fv=find_enclosing_fv(data,original.offset)
    new=_model_section_ffs(original,typ,body.read_bytes())
    raw=_model_replace_in_fv(bytes(data),original,new,fv)
    raw=bytearray(raw);raw[0x1000]=0xff
    outside=fv.end+0x10 if fv.end+0x10<len(raw) else 0x1001
    raw[outside]^=1
    def fake_run(args,cwd=None,timeout=300):
        Path(args[args.index('-o')+1]).write_bytes(raw)
        return subprocess.CompletedProcess(args,0,'File replaced')
    monkeypatch.setattr(replacer,'run_tool',fake_run)
    monkeypatch.setattr(replacer,'validate_uefireplace',lambda p:None)
    out=tmp_path/'out.rom'
    def check(path):return validate_nested_body(path,guid,typ,plan['expected_target_body_sha256'],plan['expected_target_header_extension_sha256'],plan['expected_target_header_extension_size'],plan['expected_sibling_sections'],plan['expected_full_decompressed_sha256'])
    result=replacer.replace_section_body(source,body,out,guid,typ,Path('fixture-helper'),check)
    assert result['fv_boundary']['external_changes_restored']>=1
    assert out.read_bytes()[0x1000]==data[0x1000]
    assert out.read_bytes()[outside]==data[outside]
    assert source.read_bytes()==bytes(data)


def _model_replace_in_fv(data,old,new,fv):
    # Synthetic helper: shift following aligned data, consume/grow only erased
    # tail padding. Never used by production firmware-writing code.
    align=lambda n:(n+7)&~7
    stop=old.offset+align(old.size)
    replacement=new+b'\xff'*(align(len(new))-len(new))
    tail=data[stop:fv.end]
    delta=len(replacement)-(stop-old.offset)
    if delta>0:
        assert tail[-delta:]==b'\xff'*delta
        tail=tail[:-delta]
    elif delta<0:tail+=b'\xff'*(-delta)
    return data[:old.offset]+replacement+tail+data[fv.end:]


@pytest.mark.parametrize('mode',['wrong-donor','changed-boundary','size-change','outside-change'])
def test_whole_ffs_containment_fail_closed(mode,tmp_path,monkeypatch):
    source=fixture('v9s-260826.ROM');data=source.read_bytes();guid=SECURE_BOOT_GUIDS['PkVar'];old=find_ffs_by_guid(data,guid)[0];fv=find_enclosing_fv(data,old.offset)
    donor=ROOT/'secureboot_donors/PkVar.ffs';raw=bytearray(_model_replace_in_fv(data,old,donor.read_bytes(),fv))
    if mode=='wrong-donor':raw[old.offset+30]^=1
    if mode=='changed-boundary':raw[fv.offset+0x20:fv.offset+0x28]=(fv.size-8).to_bytes(8,'little')
    if mode=='size-change':raw+=b'\xff'
    if mode=='outside-change':raw[0xFFD000:0xFFD020]=b'\xff'*32
    def fake_run(args,cwd=None,timeout=300):
        Path(args[args.index('-o')+1]).write_bytes(raw)
        return subprocess.CompletedProcess(args,0,'File replaced')
    monkeypatch.setattr(replacer,'run_tool',fake_run);monkeypatch.setattr(replacer,'validate_uefireplace',lambda p:None)
    output=tmp_path/'out.rom'
    if mode!='outside-change':
        with pytest.raises(RuntimeError):replacer.replace_ffs(source,donor,output,guid,Path('fixture-helper'))
    else:
        result=replacer.replace_ffs(source,donor,output,guid,Path('fixture-helper'))
        accepted=output.read_bytes()
        assert accepted[:fv.offset]==data[:fv.offset] and accepted[fv.end:]==data[fv.end:]
        assert find_ffs_by_guid(accepted,guid)[0].data==donor.read_bytes()
        assert result['fv_boundary']['external_changes_restored']>0


def test_fpt_stream_exception_retains_destructive_state(tmp_path,monkeypatch):
    class BadStream:
        def __init__(self):self.data=io.BytesIO(b'- Erasing Flash Block [0x1000] - 1% complete.\r')
        def read(self,n):
            b=self.data.read(n)
            if not b:raise OSError('fixture read error')
            return b
    proc=SimpleNamespace(stdout=BadStream(),kill=lambda:None,wait=lambda **kw:1)
    monkeypatch.setattr(fpt,'install_authoritative_fparts',lambda **kw:None)
    monkeypatch.setattr(fpt,'discover_fpt',lambda:tmp_path/'fptw64.exe')
    monkeypatch.setattr(fpt.subprocess,'Popen',lambda *a,**kw:proc)
    r=fpt._run_fpt(['-f','fixture.rom'],'test')
    assert not r.success and r.destructive_started


@pytest.mark.parametrize('identical,code,expected',[(False,0,False),(True,1,False),(True,0,True)])
def test_fpt_strict_flash_success_and_cr_capture(identical,code,expected,tmp_path,monkeypatch):
    output=b'- Programming Flash [0x1000] 1KB of 2KB - 50% complete.\r- Programming Flash [0x2000] 2KB of 2KB - 100% complete.\rFPT Operation Passed\n'
    if identical:output+=b'RESULT: The data is identical.\n'
    proc=SimpleNamespace(stdout=io.BytesIO(output),wait=lambda **kw:code,kill=lambda:None)
    monkeypatch.setattr(fpt,'install_authoritative_fparts',lambda **kw:None);monkeypatch.setattr(fpt,'discover_fpt',lambda:tmp_path/'fptw64.exe');monkeypatch.setattr(fpt.subprocess,'Popen',lambda *a,**kw:proc)
    events=[];r=fpt._run_fpt(['-f','fixture.rom'],'test',events.append)
    assert r.success is expected
    assert [e['percent'] for e in events if e['type']=='phase']==[50,100]
    assert {e['key'] for e in events if e['type']=='phase'}=={'program:1'}


def test_recovery_exception_is_not_retried(tmp_path,monkeypatch):
    target=tmp_path/'target.rom';target.write_bytes(b'A'*4096);backup=tmp_path/'backup.rom';backup.write_bytes(b'B'*4096)
    b=fpt.BackupResult(True,str(backup),sha256(backup.read_bytes()).hexdigest(),4096,{}, {},None)
    monkeypatch.setattr(fpt,'create_verified_backup',lambda cb:b);monkeypatch.setattr(fpt,'backup_directory',lambda:tmp_path)
    calls=[]
    def run(*a,**kw):
        calls.append(a)
        if len(calls)==1:return fpt.FptRunResult([],1,[],False,False,True,False)
        raise OSError('recovery start failed')
    monkeypatch.setattr(fpt,'_run_fpt',run)
    result=fpt.flash_bios_with_recovery(target)
    assert len(calls)==2
    assert result.recovery_attempted and result.recovery_success is False


def test_report_failure_does_not_restore_successfully_flashed_rom(tmp_path,monkeypatch):
    target=tmp_path/'target.rom';target.write_bytes(b'A'*4096)
    b=fpt.BackupResult(True,str(target),'abc',4096,{}, {},None)
    monkeypatch.setattr(fpt,'create_verified_backup',lambda cb:b)
    calls=[]
    def run(*a,**kw):calls.append(a);return fpt.FptRunResult([],0,[],True,True,True,True)
    monkeypatch.setattr(fpt,'_run_fpt',run)
    def fail(*a):raise OSError('disk full')
    monkeypatch.setattr(fpt,'_write_flash_report_unchecked',fail)
    result=fpt.flash_bios_with_recovery(target)
    assert result.success and not result.recovery_attempted and len(calls)==1


def _model_section_ffs(original,typ,body):
    parsed=parse_guided_lzma_ffs(original,typ)
    target=next(s for s in parsed.nested_sections if s.section_type==typ)
    assert len(body)==len(target.body)
    dec=bytearray(parsed.decompressed)
    start=target.offset+target.header_size
    dec[start:start+len(body)]=body
    payload=lzma.compress(bytes(dec),format=lzma.FORMAT_ALONE)
    prefix=bytearray(original.data[:24+parsed.data_offset])
    prefix[24:27]=(parsed.data_offset+len(payload)).to_bytes(3,'little')
    new=prefix+payload
    new[20:23]=len(new).to_bytes(3,'little')
    new[16]=0
    new[17]=(-sum(new[24:]))&255 if new[19]&0x40 else 0xaa
    new[16]=(-(sum(new[:24])-new[17]-new[23]))&255
    return bytes(new)


@pytest.mark.parametrize('name,opt',[('x99xd3-260810-stock.ROM',True),('XD3VRM-260731-Original(1).ROM',False),('v9s-260826.ROM',True),('qiyida-x99-k9s-stock.rom',True),('Jginue ARGB backup.rom',False)])
def test_full_secureboot_optimized_pipeline_with_modeled_helper(name,opt,tmp_path,monkeypatch):
    """Exercise production orchestration/validators; model Windows helper only."""
    monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
    source=fixture(name);before=source.read_bytes();calls=[]
    def fake_run(args,cwd=None,timeout=300):
        current=Path(args[1]).read_bytes();guid=args[2];typ=int(args[3],16)
        old=find_ffs_by_guid(current,guid)[0];fv=find_enclosing_fv(current,old.offset)
        replacement=Path(args[4]).read_bytes()
        if '-asis' not in args:replacement=_model_section_ffs(old,typ,replacement)
        raw=bytearray(_model_replace_in_fv(current,old,replacement,fv))
        raw[0x1000]^=1 # unwanted side effect; containment must remove it every time
        Path(args[args.index('-o')+1]).write_bytes(raw);calls.append(guid)
        return subprocess.CompletedProcess(args,0,'File replaced')
    monkeypatch.setattr(replacer,'run_tool',fake_run);monkeypatch.setattr(replacer,'validate_uefireplace',lambda p:None)
    output=tmp_path/'result.rom'
    options=dict(apply_secureboot=True,donor_source=ROOT/'secureboot_donors',apply_optimized=opt,repair_fit=True)
    tracker=BuildProgressTracker(options)
    r=build_rom(source,output,ToolPaths(None,Path('model.exe'),None),**options,progress=tracker.update)
    assert r.success,r.error
    assert tracker.current_percent==100
    final=inspect_rom(output)
    assert final.secure_boot['has_complete_2023_set'] and not final.secure_boot['has_ami_test_pk']
    assert final.fit['status']=='PASS'
    assert not opt or final.optimized['matches']
    assert source.read_bytes()==before
    assert output.read_bytes()[0x1000]==before[0x1000]
    assert find_ffs_by_guid(before,SECURE_BOOT_GUIDS['dbxVar'])[0].data==find_ffs_by_guid(output.read_bytes(),SECURE_BOOT_GUIDS['dbxVar'])[0].data
    assert set(SECURE_BOOT_GUIDS[n] for n in PATCH_TARGETS).issubset(calls)
    # A second build must be idempotent and invoke no helper replacements.
    calls.clear();again=tmp_path/'again.rom'
    r2=build_rom(output,again,ToolPaths(None,Path('model.exe'),None),**options)
    assert r2.success,r2.error
    assert not calls and again.read_bytes()==output.read_bytes()


@pytest.mark.parametrize('same',[True,False])
def test_dual_backup_requires_byte_identical_dumps(same,tmp_path,monkeypatch):
    monkeypatch.setattr(fpt,'backup_directory',lambda:tmp_path)
    calls=[]
    def dump(path,group,cb):
        calls.append(group);path.write_bytes((b'A' if same or len(calls)==1 else b'B')*4096)
        return fpt.FptRunResult([],0,[],True,False,False,True)
    monkeypatch.setattr(fpt,'_dump_once',dump)
    result=fpt.create_verified_backup()
    assert len(calls)==2
    assert result.success is same
    if same:
        p=Path(result.backup_path)
        assert p.is_file() and sha256(p.read_bytes()).hexdigest()==result.sha256
        assert p.with_suffix('.ROM.sha256').read_text().startswith(result.sha256)
    else:
        assert result.backup_path is None
        assert 'not byte-identical' in result.error


@pytest.mark.parametrize('name',['qiyida-x99-k9s-stock.rom','Jginue ARGB backup.rom'])
def test_new_boards_cstate_mutations_reinspect_complete(name,tmp_path,monkeypatch):
    from x99toolkit.firmware import patch_primary_cstates
    from x99toolkit.cstates import inspect_all_cstates
    from x99toolkit.families import AMITSE_DRIVER_GUID
    path=tmp_path/'cstates.rom';path.write_bytes(fixture(name).read_bytes())
    main_before=find_ffs_by_guid(path.read_bytes(),AMITSE_DRIVER_GUID)[0].data
    patch_primary_cstates(path)
    for builder,guid,typ in [(build_nv_internal_body,NV_INTERNAL_GUID,NV_INTERNAL_RAW_SECTION_TYPE),(build_amitse_body,AMITSE_SETUP_DATA_GUID,AMITSE_RAW_SECTION_TYPE)]:
        body=tmp_path/'body.bin';plan=builder(path.read_bytes(),body)
        data=path.read_bytes();old=find_ffs_by_guid(data,guid)[0];fv=find_enclosing_fv(data,old.offset)
        path.write_bytes(_model_replace_in_fv(data,old,_model_section_ffs(old,typ,body.read_bytes()),fv))
    state=inspect_all_cstates(path)
    assert state['supported'] and state['matches']
    assert find_ffs_by_guid(path.read_bytes(),AMITSE_DRIVER_GUID)[0].data==main_before
