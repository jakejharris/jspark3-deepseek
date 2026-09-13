#!/usr/bin/env python3
import argparse,concurrent.futures,datetime,hashlib,json,os,shutil,signal,subprocess,sys,threading,time,urllib.request
from pathlib import Path
W=Path(__file__).resolve().parent
sys.path.insert(0,str(W.parents[1]/'tools'))
import tempo
from prepare import verify_fixture,sha
BASE=None
PREPARED=None
PI=None
STOP=threading.Event()
PROCESSES=[]
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def metrics():
 raw=urllib.request.urlopen(BASE+'/metrics',timeout=4).read().decode();v={}
 for line in raw.splitlines():
  if not line or line.startswith('#'):continue
  k,val=line.rsplit(' ',1)
  try:v[k]=float(val)
  except ValueError:pass
 return v
def gauge(m,term):
 values=[v for k,v in m.items() if k.split('{')[0].endswith(term)]
 if not values:raise RuntimeError('Required engine metric missing: '+term)
 return sum(values)
def idle():
 for _ in range(60):
  if STOP.is_set():raise RuntimeError('Work observation fault')
  m=metrics();running=gauge(m,'num_requests_running');waiting=gauge(m,'num_requests_waiting')
  if running==0 and waiting==0:return m
  time.sleep(1)
 raise RuntimeError('endpoint did not drain')
def worker(slot,task,run,seconds,barrier,prompt,smoke=False):
 out=run/('slot'+str(slot)+'-'+task);out.mkdir()
 work=out/'workspace';shutil.copytree(PREPARED/'fixtures'/task,work)
 env=dict(os.environ,PI_CODING_AGENT_DIR=str(PREPARED/'pi-agent'),WORK_PAYLOAD_LOG=str(out/'payloads.jsonl'))
 argv=[PI,'--offline','--mode','json','--provider','deepseek-v4.1-flash','--model','deepseek-v4.1-flash','--thinking','high','--no-session','--no-extensions','--no-skills','--no-context-files','--no-prompt-templates','--no-themes','-e',str(W/'observer.ts'),'--tools','read,write,edit,bash','-p',prompt]
 barrier.wait(timeout=30)
 if STOP.is_set():raise RuntimeError('Work observation fault')
 start=time.monotonic();started=now();ended=[]
 with (out/'events.jsonl').open('w') as log,(out/'stderr.txt').open('w') as err:
  p=subprocess.Popen(argv,cwd=work,env=env,stdout=subprocess.PIPE,stderr=err,text=True,start_new_session=True)
  def kill():
   if p.poll() is None:
    ended.append('CUTOFF');terminate(p)
  PROCESSES.append(p)
  timer=threading.Timer(seconds,kill);timer.start()
  try:
   for line in p.stdout:
    try:event=json.loads(line)
    except ValueError:event={'raw':line}
    log.write(json.dumps({'t':time.monotonic()-start,'event':event})+'\n');log.flush()
   rc=p.wait(timeout=10)
  finally:
   timer.cancel()
   if p.poll() is None:terminate(p)
 elapsed=time.monotonic()-start
 events=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()]
 messages=[x['event']['message'] for x in events if x['event'].get('type')=='message_end' and x['event'].get('message',{}).get('role')=='assistant']
 deltas=[x for x in events if x['event'].get('assistantMessageEvent',{}).get('type') in ['text_delta','thinking_delta','toolcall_delta']]
 tools=[x for x in events if x['event'].get('type')=='tool_execution_end']
 payloads=[json.loads(x) for x in (out/'payloads.jsonl').read_text().splitlines()] if (out/'payloads.jsonl').exists() else []
 result={'slot':slot,'task':task,'started_at':started,'elapsed_s':elapsed,'window_s':seconds,'returncode':rc,'outcome':ended[0] if ended else ('EARLY_END' if rc==0 else 'ERROR'),'first_output_s':deltas[0]['t'] if deltas else None,'completed_assistant_messages':len(messages),'completed_output_tokens':sum(m.get('usage',{}).get('output',0) for m in messages),'request_count':len(payloads),'tools_completed':len(tools),'tool_errors':sum(bool(x['event'].get('isError')) for x in tools),'model_errors':[m.get('errorMessage') for m in messages if m.get('stopReason')=='error'],'payload_sha256':[hashlib.sha256(json.dumps(x['payload'],sort_keys=True).encode()).hexdigest() for x in payloads],'usage':[m.get('usage') for m in messages],'caps':[x['payload'].get('max_tokens') for x in payloads],'thinking':[x['payload'].get('chat_template_kwargs') for x in payloads]}
 write(out/'RESULT.json',result);return result

def terminate(proc):
 """Stop only the Pi process group created by this runner; bounded escalation."""
 try:
  os.killpg(proc.pid,signal.SIGTERM)
  proc.wait(timeout=5)
 except subprocess.TimeoutExpired:
  os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
 except ProcessLookupError:pass

def dependencies(pi):
 version=subprocess.check_output([pi,'--offline','--version'],text=True,timeout=15).strip()
 assert version=='0.84.2','Historical adapter requires Pi 0.84.2, found '+version
 # Same namespaces/binds as observer.ts; fail before any model request.
 sandbox=['/usr/bin/bwrap','--die-with-parent','--unshare-user','--unshare-pid','--unshare-net','--new-session','--ro-bind','/usr','/usr','--ro-bind','/bin','/bin','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','/bin/true']
 subprocess.run(sandbox,check=True,timeout=15)
 return version

def main():
 global BASE,PREPARED,PI
 ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True);ap.add_argument('--prepared',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--pi',required=True);ap.add_argument('--smoke',action='store_true');ap.add_argument('--check-only',action='store_true');a=ap.parse_args()
 c=tempo.config(a.config);BASE=c['endpoint'];PREPARED=a.prepared.resolve();PI=str(Path(a.pi).resolve());version=dependencies(PI)
 prepared=json.loads((PREPARED/'prepared.json').read_text());assert prepared['endpoint']==BASE
 for name,key in [('fixture.json','fixture_manifest_sha256'),('prompts.json','prompts_sha256'),('observer.ts','observer_sha256')]:assert sha(W/name)==prepared[key]
 for name,key in [('models.json','adapter_sha256'),('settings.json','settings_sha256')]:assert sha(PREPARED/'pi-agent'/name)==prepared[key]
 assert not (PREPARED/'pi-agent'/'auth.json').exists(),'Use the generated adapter, without private credentials'
 verify_fixture(PREPARED/'fixtures')
 state=Path(c['state_dir'])/c['deployment'];owned=json.loads((state/'owned.json').read_text());assert json.loads((state/'config.json').read_text())==c
 def ranks():
  rows=tempo.parallel(c,'status',owned)
  assert all(tempo.guard_ok(r) for r in rows),'Guard, ownership or resource fault'
  return rows
 initial=ranks();idle()
 if a.check_only:print('Work dependencies, public inputs, ownership and idle endpoint PASS');return
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
 write(out/'IDENTITY.json',{'deployment':c['deployment'],'owned':owned,'ranks':initial,'prepared':prepared,'pi_version':version,'note':'New cohort; safety wrapper adapted from historical runner; same public prompts/fixture and fixed 180-second denominator'})
 prompts=json.loads((W/'prompts.json').read_text());fault=[]
 def observer():
  n=0
  with (out/'METRICS.jsonl').open('w') as f,(out/'RESOURCES.jsonl').open('w') as rf:
   while not STOP.is_set():
    try:
     f.write(json.dumps({'at':now(),'metrics':metrics()})+'\n');f.flush()
     if n%5==0:rf.write(json.dumps({'at':now(),'ranks':ranks()})+'\n');rf.flush()
    except Exception as e:
     fault.append(str(e));STOP.set()
     for proc in PROCESSES:
      if proc.poll() is None:terminate(proc)
    n+=1;STOP.wait(1)
 t=threading.Thread(target=observer);t.start()
 try:
  rounds=[('smoke',1,45)] if a.smoke else [('c3-first',3,180),('c3-repeat',3,180),('c6-first',6,180),('c6-repeat',6,180)]
  for label,concurrency,seconds in rounds:
   if fault:raise RuntimeError(fault)
   before=idle();run=out/label;run.mkdir();write(run/'BEFORE.json',{'at':now(),'metrics':before})
   barrier=threading.Barrier(concurrency);tasks=['threejs','python','jspark3'];start=now();start_clock=time.monotonic()
   with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
    jobs=[ex.submit(worker,i,tasks[i%3],run,seconds,barrier,'Use bash to run python3 -c "print(7*8)" then write the number to check.txt, read it and say done.' if a.smoke else prompts[tasks[i%3]],a.smoke) for i in range(concurrency)]
    rows=[j.result() for j in jobs]
   remaining=seconds-(time.monotonic()-start_clock)
   if remaining>0:STOP.wait(remaining)
   after=idle();delta=gauge(after,'generation_tokens_total')-gauge(before,'generation_tokens_total');assert delta>=0,'Engine counters reset'
   result={'round':label,'offered':concurrency,'started_at':start,'ended_at':now(),'window_s':seconds,'rows':rows,'before':before,'after':after,'counter_deltas':{k:v-before[k] for k,v in after.items() if k in before and ('_total' in k or '_sum' in k)},'engine_generated_tokens':delta,'engine_tokens_per_window_second':delta/seconds,'resource_faults':fault,'quality_score':None}
   write(run/'RESULT.json',result);print(json.dumps({'round':label,'engine_tokens_per_window_second':delta/seconds}),flush=True)
   if fault or any(r['outcome']=='ERROR' or r['model_errors'] or not r['request_count'] for r in rows):raise RuntimeError('Work transport/configuration fault')
   STOP.wait(5)
  write(out/'COMPLETE.json',{'at':now(),'rounds':[r[0] for r in rounds]})
 finally:
  STOP.set();t.join(timeout=20)
  for proc in PROCESSES:
   if proc.poll() is None:terminate(proc)
if __name__=='__main__':main()
