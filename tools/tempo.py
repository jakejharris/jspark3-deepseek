#!/usr/bin/env python3
"""Controller lifecycle. A persistent user service couples exact rank guards."""
import argparse,json,re,shlex,subprocess,sys,time,ipaddress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from common import ROOT,read,write,digest

def config(path):
 c=json.loads(Path(path).read_text());assert c['schema_version']==1
 assert re.fullmatch(r'tempo-[a-z0-9-]{1,48}',c['deployment'])
 assert re.fullmatch(r'sha256:[a-f0-9]{64}',c['image_id']),'Set exact image ID'
 assert c['image_provenance'] in ['source-build','verified-historical-cache']
 assert [h['rank'] for h in c['hosts']]==[0,1,2] and len({h['ssh'] for h in c['hosts']})==3
 assert Path(c['state_dir']).is_absolute()
 for h in c['hosts']:
  assert re.fullmatch(r'[A-Za-z0-9_.@-]+',h['ssh']) and not h['ssh'].startswith('-')
  ipaddress.ip_address(h['ip'])
  for subnet in h['fabric_subnets']:ipaddress.ip_network(subnet)
  for key in ['recipe','model','engram','packed','work']:
   assert Path(h[key]).is_absolute() and ',' not in h[key] and '\n' not in h[key]
 return c

def remote(c,h,action,receipt=None,**extra):
 body=dict(config=c,host=h,action=action,**extra)
 if receipt:body.update(cid=receipt['cid'],started_at=receipt['started_at'])
 cmd=shlex.join(['python3',h['recipe']+'/tools/node.py'])
 p=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4',h['ssh'],cmd],input=json.dumps(body),text=True,capture_output=True,timeout=120 if action in ['start','stop'] else 15)
 if p.returncode:raise RuntimeError(f"rank {h['rank']} {action} failed: {p.stderr[-600:]}")
 return json.loads(p.stdout)

def parallel(c,action,receipts=None,**extra):
 with ThreadPoolExecutor(max_workers=3) as pool:
  jobs=[pool.submit(remote,c,h,action,next((x for x in receipts or [] if x['rank']==h['rank']),None),**extra) for h in c['hosts']]
  return [j.result() for j in jobs]

def guard_ok(row):
 g=row.get('guard') or {};age=(datetime.now(timezone.utc)-datetime.fromisoformat(g.get('utc','1970-01-01T00:00:00+00:00'))).total_seconds()
 return (not row['trip'] and row['running'] and row['restart_count']==0 and not row['oom'] and -5<=age<=15 and g.get('cid')==row['cid'] and g.get('state')=='WATCHING' and g.get('docker',{}).get('started_at')==row['started_at'] and row['resources']['memavailable_bytes']>=4*1024**3)

def stop(c,receipts):
 failures=[]
 for h in c['hosts']:
  r=next((r for r in receipts if r['rank']==h['rank']),None)
  if r:
   try:remote(c,h,'stop',r)
   except Exception as e:failures.append(str(e))
 if failures:raise RuntimeError('; '.join(failures))

def relay(c,receipts,state):
 while not (state/'stopped').exists():
  try:
   rows=parallel(c,'status',receipts)
   if not all(guard_ok(r) for r in rows):raise RuntimeError('Guard trip, stale telemetry or identity/resource fault')
   write(state/'observations.json',dict(at=datetime.now(timezone.utc).isoformat(),ranks=rows))
  except Exception as e:
   write(state/'relay-fault.json',dict(error=str(e),at=datetime.now(timezone.utc).isoformat()))
   for h in c['hosts']:
    try:remote(c,h,'trip',next(r for r in receipts if r['rank']==h['rank']))
    except Exception:pass
   (state/'stopped').touch();stop(c,receipts);raise
  time.sleep(2)

def redacted_diagnostics(rows):
 selected=[]
 for i,r in enumerate(rows):
  resources={k:r['resources'][k] for k in ['memory.current','memory.swap.current','memavailable_bytes']}
  resources['events']={k:v for k,v in r['resources']['events'].items() if k in ['low','high','max','oom','oom_kill','oom_group_kill'] and isinstance(v,int)}
  selected.append(dict(rank=i,running=r['running'],restart_count=r['restart_count'],oom=r['oom'],trip=r['trip'],resources=resources))
 return dict(version=read('release/identity.json')['identity']['candidate'],manifest_sha256=digest(ROOT/'release/manifest.json'),ranks=selected)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['render','preflight','start','status','health','stop','native','relay','diagnostics']);p.add_argument('--config',type=Path,required=True);p.add_argument('--resources',action='store_true');a=p.parse_args();c=config(a.config);state=Path(c['state_dir'])/c['deployment']
 if a.action in ['render','preflight']:
  print(json.dumps(parallel(c,a.action,resources=a.resources),indent=2));return
 if a.action=='start':
  assert not state.exists(),'Fresh deployment name required; never restart an archived incarnation'
  subprocess.run(['systemctl','--user','show-environment'],check=True,stdout=subprocess.DEVNULL)
  parallel(c,'preflight',resources=True)
  state.mkdir(parents=True);write(state/'config.json',c);receipts=[]
  try:
   for h in reversed(c['hosts']):
    receipts.append(remote(c,h,'start'));write(state/'owned.json',receipts)
   deadline=time.monotonic()+20
   while time.monotonic()<deadline:
    if all(guard_ok(r) for r in parallel(c,'status',receipts)):break
    time.sleep(2)
   else:raise RuntimeError('Guard readiness timeout')
   subprocess.run(['systemd-run','--user','--unit',c['deployment']+'-relay','--property=Restart=on-failure','--property=RestartSec=2','python3',str(ROOT/'tools/tempo.py'),'relay','--config',str(state/'config.json')],check=True)
   for h in reversed(c['hosts']):
    if not all(guard_ok(r) for r in parallel(c,'status',receipts)):raise RuntimeError('Release gate failed')
    remote(c,h,'release',next(r for r in receipts if r['rank']==h['rank']))
  except Exception:
   if receipts:stop(c,receipts)
   raise
  print('Ranks released. Loading may take up to 60 minutes; inspect status and Docker logs.');return
 receipts=json.loads((state/'owned.json').read_text())
 assert json.loads((state/'config.json').read_text())==c,'Configuration changed; use frozen deployment config'
 if a.action=='relay':relay(c,receipts,state)
 elif a.action=='stop':
  (state/'stopped').touch();stop(c,receipts);subprocess.run(['systemctl','--user','stop',c['deployment']+'-relay'],check=False);print('Owned ranks stopped; containers and data retained')
 elif a.action=='health':
  import urllib.request
  urllib.request.urlopen(c['endpoint']+'/health',timeout=20).read()
  models=json.load(urllib.request.urlopen(c['endpoint']+'/v1/models',timeout=20));assert any(r['id']=='deepseek-v4.1-flash' for r in models['data'])
  print('HTTP/backend ready. Run release smoke for application correctness.')
 elif a.action in ['status','native']:print(json.dumps(parallel(c,a.action,receipts),indent=2))
 elif a.action=='diagnostics':
  rows=parallel(c,'status',receipts)
  # Explicit allowlist: no full logs, environment, config paths, prompts, host IDs, CID, or URLs.
  report=redacted_diagnostics(rows)
  path=state/'diagnostics-redacted.json';write(path,report);print(path);print('Inspect before attaching. Nothing uploaded.')
if __name__=='__main__':main()
