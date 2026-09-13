import importlib.util,shlex,subprocess,sys,threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import node

@pytest.mark.parametrize('member',[False,True])
def test_guard_activates_existing_membership_without_manager_restart(monkeypatch,member):
 monkeypatch.setattr(node.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_uid=1000,pw_gid=1000,pw_name='serving'))
 monkeypatch.setattr(node.grp,'getgrnam',lambda name:SimpleNamespace(gr_gid=988,gr_mem=['serving'] if member else []))
 payload=['python3','-S','/a path/guard.py','--container-id','a'*64]
 if not member:
  with pytest.raises(AssertionError,match='member'):node.guard_command('tempo-test',payload)
 else:
  cmd=node.guard_command('tempo-test',payload)
  assert cmd[-4:-1]==['sg','docker','-c'] and shlex.split(cmd[-1])==payload
  assert '--property=Restart=no' in cmd and '--property=MemoryMax=128M' in cmd and '--property=MemorySwapMax=0' in cmd

@pytest.mark.parametrize('result',[0,9])
def test_build_wrapper_streams_compiler_output_and_preserves_failure(tmp_path,result):
 root=Path(__file__).resolve().parents[1]
 stage=tmp_path/'stage.py';stage.write_text(f"from pathlib import Path\nPath({str(tmp_path/'stage1.build.log')!r}).write_text('actual compiler output\\n')\nraise SystemExit({result})\n")
 script=(root/'build/run_stage.sh').read_text().replace('/receipts',str(tmp_path)).replace('/tempo/build/stage.py',str(stage))
 p=subprocess.run(['bash','-c',script,'stage-test','1'],capture_output=True,text=True,timeout=10)
 assert p.returncode==result and 'actual compiler output' in p.stdout

def test_work_cutoff_kills_its_process_and_retains_new_workspace(tmp_path,monkeypatch):
 root=Path(__file__).resolve().parents[1];work=root/'benchmarks/work';monkeypatch.syspath_prepend(str(work))
 spec=importlib.util.spec_from_file_location('tempo_work',work/'run.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 prepared=tmp_path/'prepared';(prepared/'fixtures/python').mkdir(parents=True);(prepared/'pi-agent').mkdir()
 fake=tmp_path/'pi';fake.write_text('#!/usr/bin/env python3\nimport time\nprint(\'{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}\',flush=True)\ntime.sleep(30)\n');fake.chmod(0o755)
 module.PREPARED=prepared;module.PI=str(fake);run=tmp_path/'run';run.mkdir()
 row=module.worker(0,'python',run,.2,threading.Barrier(1),'synthetic test')
 assert row['outcome']=='CUTOFF' and row['elapsed_s']<8 and row['first_output_s'] is not None
 assert (run/'slot0-python/workspace').is_dir()
 assert all(p.poll() is not None for p in module.PROCESSES)
 with pytest.raises(FileExistsError):module.worker(0,'python',run,.2,threading.Barrier(1),'synthetic test')

def test_work_requires_engine_gauges(monkeypatch):
 work=Path(__file__).resolve().parents[1]/'benchmarks/work';monkeypatch.syspath_prepend(str(work))
 spec=importlib.util.spec_from_file_location('tempo_work',work/'run.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with pytest.raises(RuntimeError,match='missing'):module.gauge({},'num_requests_running')
