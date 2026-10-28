"""Persistent rebuild evidence, including failures, independent of preserve_stages."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from hashlib import sha256
import json
import shutil
import traceback
import uuid

_build = ContextVar('diagnostic_build', default=None)
_operation = ContextVar('diagnostic_operation', default=None)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


@contextmanager
def diagnostic_session(output, report):
    directory=output.parent/(output.name+'.diagnostics')/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    report.tool_output['Diagnostic evidence']=str(directory)
    token=_build.set({'directory':directory,'report':report,'count':0})
    try: yield directory
    finally: _build.reset(token)


@contextmanager
def rebuild_evidence(input_path, output_path, guid, command):
    state=_build.get()
    if state is None:
        directory=output_path.parent/(output_path.name+'.diagnostics')/uuid.uuid4().hex[:8]
        report=None
    else:
        state['count']+=1
        directory=state['directory']/(f"{state['count']:02d}-"+output_path.stem)
        report=state['report']
    directory.mkdir(parents=True)
    record={'guid':guid,'command':command,'input_path':str(input_path),
            'output_path':str(output_path),'status':'RUNNING','events':[]}
    if input_path.is_file(): record['input_sha256']=sha256(input_path.read_bytes()).hexdigest()
    token=_operation.set((directory,record))
    write_json(directory/'operation.json',record)
    if len(command)>4 and Path(command[4]).is_file():
        shutil.copy2(command[4],directory/('expected.ffs' if '-asis' in command else 'expected-section-body.bin'))
    if report is not None: report.tool_output['Rebuild '+directory.name]=str(directory)
    try:
        yield
        record['status']='PASS'
    except Exception as exc:
        record['status']='FAILED';record['error']=str(exc);record['traceback']=traceback.format_exc()
        # Retain the complete immediate preimage and rejected output, even when
        # failure precedes parsing, verification or a normal return to pipeline.
        for src,name in ((input_path,'PREIMAGE-NOT-FOR-FLASHING.bin'),(output_path,'REJECTED-NOT-FOR-FLASHING.bin')):
            if src.is_file():
                shutil.copy2(src,directory/name)
                record[name]={'size':src.stat().st_size,'sha256':sha256(src.read_bytes()).hexdigest()}
        if output_path.is_file():
            from .firmware import find_ffs_by_guid
            try:
                for index, hit in enumerate(find_ffs_by_guid(output_path.read_bytes(), guid)):
                    (directory/f'failed-target-{index}.ffs').write_bytes(hit.data)
            except Exception as capture_error:
                record['extraction_error']=str(capture_error)
        (directory/'DO-NOT-FLASH.txt').write_text('FAILED BUILD EVIDENCE ONLY. Do not flash these intermediate images.\n',encoding='utf-8')
        if report is not None: report.tool_output['Rebuild '+directory.name]=str(directory)+'\nFAILED: '+str(exc)
        raise
    finally:
        try:
            if output_path.is_file(): record['output_sha256']=sha256(output_path.read_bytes()).hexdigest()
            write_json(directory/'operation.json',record)
            if report is not None:
                report.tool_output['Rebuild '+directory.name]=str(directory)+'\n'+json.dumps(record,indent=2)
        finally: _operation.reset(token)


def event(name, **details):
    current=_operation.get()
    if current:
        directory,record=current
        record['events'].append({'event':name,**details})
        write_json(directory/'operation.json',record)


def differences(path, expected, actual):
    with path.open('w',encoding='utf-8') as f:
        f.write('offset_hex\texpected_hex\tactual_hex\n')
        for i in range(max(len(expected),len(actual))):
            a=expected[i] if i<len(expected) else None
            b=actual[i] if i<len(actual) else None
            if a!=b: f.write(f'{i:08X}\t'+('--' if a is None else f'{a:02X}')+'\t'+('--' if b is None else f'{b:02X}')+'\n')


def ffs_evidence(expected, actual, canonical=None, verification=None):
    current=_operation.get()
    if not current: return
    directory,record=current
    (directory/'expected.ffs').write_bytes(expected)
    if actual is not None:
        (directory/'actual.ffs').write_bytes(actual)
        differences(directory/'expected-vs-actual.tsv',expected,actual)
    if canonical is not None:
        (directory/'expected-relocated.ffs').write_bytes(canonical)
        if actual is not None: differences(directory/'relocated-vs-actual.tsv',canonical,actual)
    event('FFS comparison',expected_size=len(expected),actual_size=len(actual) if actual is not None else None,
          expected_sha256=sha256(expected).hexdigest(),actual_sha256=sha256(actual).hexdigest() if actual is not None else None,
          verification=verification)
