import json
import subprocess
import sys
from types import SimpleNamespace
import pytest
from x99toolkit import cpu
from x99toolkit.cpu import CpuDetection, Processor, model_from_name, parse_processors
from x99toolkit.models import InspectionReport
from x99toolkit.recommendations import recommended_modifications

OC = ('E5-1650 v3','E5-1660 v3','E5-1680 v3','i7-5820K','i7-5930K','i7-5960X','i7-6800K','i7-6850K','i7-6900K','i7-6950X')

def detected(*names): return CpuDetection(tuple(Processor(n) for n in names))
def report(): return InspectionReport('fixture',0,'sha',dict(status='NOT_APPLIED',target_count=1,cstates={'supported':True}),
    dict(all_variables_valid=True,has_complete_2023_set=False,has_ami_test_pk=False),
    optimized={'supported':True},overclock={'supported':True},splash={'supported':True})

@pytest.mark.parametrize('raw,model',[
 ('Intel(R) Xeon(R) CPU E5-2660 v3 @ 2.60GHz','E5-2660 v3'),
 ('Intel(R) Xeon(R) CPU E5-1660 v3 @ 3.00GHz','E5-1660 v3'),
 ('Intel(R) Xeon(R) CPU E5-2667 v3 @ 3.20GHz','E5-2667 v3'),
 ('Intel(R) Xeon(R) CPU E5-2667 v4 @ 3.20GHz','E5-2667 v4'),
 ('Intel(R) Xeon(R) CPU E5-2698B v3 @ 2.00GHz','E5-2698B v3'),
 ('Intel(R) Core(TM) i7-6950X CPU @ 3.00GHz','i7-6950X'),
 ('Genuine Intel(R) CPU 0000 @ 2.00GHz','')])
def test_exact_model_extraction(raw,model): assert model_from_name(raw)==model

@pytest.mark.parametrize('model',OC)
def test_exact_ten_oc_models(model):
 assert set(recommended_modifications(detected(model),report()))=={'overclock','secureboot','optimized','splash'}

@pytest.mark.parametrize('model',['E5-2660 v3','E5-2667 v3','E5-2698B v3','E5-2650L v3','E5-2687W v3'])
def test_26xx_v3_tbu(model):
 assert set(recommended_modifications(detected(model),report()))=={'tbu','secureboot','optimized','splash'}

@pytest.mark.parametrize('model',['E5-2667 v4','E5-1650 v4','E5-1620 v3','E5-1630 v3','i7-4790K','i7-7800X','unknown'])
def test_others_receive_general_recommendations(model):
 assert set(recommended_modifications(detected(model),report()))=={'secureboot','optimized','splash'}

def test_unknown_and_mixed_processors_keep_general_recommendations():
 for result in (CpuDetection(error='failed'),detected('E5-2660 v3','E5-2667 v4')):
  assert set(recommended_modifications(result,report()))=={'secureboot','optimized','splash'}
 assert detected('E5-2660 v3','E5-2660 v3').recommend_tbu

def test_current_keys_and_other_current_modifications_are_not_recommended():
 r=report();r.tbu['status']='PASS';r.secure_boot['has_complete_2023_set']=True
 r.optimized['matches']=r.overclock['matches']=r.splash['already_present']=True
 assert recommended_modifications(detected('E5-2660 v3'),r)==()
 r.secure_boot['has_ami_test_pk']=True
 assert recommended_modifications(detected('E5-1660 v3'),r)==('secureboot',)

def test_single_multiple_and_incomplete_cpu_json():
 row={'Name':'Intel(R) Xeon(R) CPU E5-2698B v3','ProcessorId':'same-family','Manufacturer':'GenuineIntel'}
 assert parse_processors(json.dumps(row)).processors[0].name==row['Name']
 assert len(parse_processors('\ufeff'+json.dumps([row,row])).processors)==2
 for payload in ('null','[]','{}','[{}]','[{"Name":""}]','bad'):
  with pytest.raises(ValueError):parse_processors(payload)

@pytest.mark.parametrize('failure',['timeout','missing','exit','malformed'])
def test_failed_query_is_unknown(monkeypatch,failure):
 monkeypatch.setattr(cpu,'os',SimpleNamespace(name='nt'))
 def run(*a,**kw):
  assert kw['timeout']==8
  assert a[0][:4]==['powershell.exe','-NoLogo','-NoProfile','-NonInteractive']
  if failure=='timeout':raise subprocess.TimeoutExpired('query',8)
  if failure=='missing':raise FileNotFoundError('query')
  return SimpleNamespace(returncode=1 if failure=='exit' else 0,stdout='not-json')
 monkeypatch.setattr(cpu.nativeprocess,'run',run)
 assert cpu.detect_cpu().error

def test_successful_windows_query(monkeypatch):
 monkeypatch.setattr(cpu,'os',SimpleNamespace(name='nt'))
 monkeypatch.setattr(cpu.nativeprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='[{"Name":"Intel Xeon E5-2660 v3"}]'))
 assert cpu.detect_cpu().recommend_tbu

@pytest.mark.skipif(sys.platform!='win32',reason='Requires Windows WMI')
def test_real_windows_query():
 result=cpu.detect_cpu()
 assert result.processors and not result.error
