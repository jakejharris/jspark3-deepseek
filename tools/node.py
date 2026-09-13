#!/usr/bin/env python3
"""Host operations invoked over SSH. Exact labels/IDs only; never removes data."""
import json,os,sys,time,subprocess,platform,grp,pwd,shlex
from pathlib import Path
from common import ROOT,read,write,digest,verify

def capture(argv):return subprocess.check_output([str(x) for x in argv],text=True,timeout=60).strip()
def docker(*args):return capture(['docker',*args])
def inspect(cid):return json.loads(docker('inspect',cid))[0]
def owned(cid,cfg):
 d=inspect(cid)
 assert d['Id']==cid and d['Config']['Labels'].get('jspark3.deployment')==cfg['deployment'],'Ownership mismatch'
 return d

def resources(cid):
 d=inspect(cid);pid=d['State']['Pid'];assert pid>0,'Container is not running'
 cg=Path('/sys/fs/cgroup')/next(x.split('::',1)[1] for x in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if x.startswith('0::')).lstrip('/')
 vals={name:int((cg/name).read_text()) for name in ['memory.current','memory.swap.current']}
 vals['events']={k:int(v) for k,v in (l.split() for l in (cg/'memory.events').read_text().splitlines())}
 vals['memavailable_bytes']=next(int(l.split()[1])*1024 for l in open('/proc/meminfo') if l.startswith('MemAvailable:'))
 return cg,vals

def argv(cfg,h):
 runtime=read('release/runtime.json'); env=dict(runtime['env']);env['VLLM_HOST_IP']=h['ip'];env['NCCL_IB_HCA']=h['hca']
 for key in ['NCCL_SOCKET_IFNAME','GLOO_SOCKET_IFNAME','TP_SOCKET_IFNAME','MN_IF_NAME']:env[key]=h['interface']
 base=Path(h['work'])/cfg['deployment'];name=cfg['deployment']+'-r'+str(h['rank'])
 args=['docker','run','-d','--pull=never','--name',name,'--label','jspark3.deployment='+cfg['deployment'],'--label','jspark3.version='+read('release/identity.json')['identity']['candidate'],'--gpus','all','--network','host','--init','--memory','112g','--memory-swap','112g','--shm-size','32g','--pids-limit','2048','--oom-score-adj','500','--ulimit','memlock=-1:-1','--cap-add','IPC_LOCK','--device','/dev/infiniband','--entrypoint','/tempo-entrypoint.sh']
 mounts=[(h['model'],'/models/model-tp3',True),(h['engram'],'/engram-local',True),(h['packed'],'/engram-packed',True),(str(base/'cache'),'/cache',False),(str(base/'state'),'/state',False),(str(ROOT/'recipe/entrypoint.sh'),'/tempo-entrypoint.sh',True)]
 for row in read('release/patches.json'):mounts.append((str(ROOT/row['file']),row['target'],True))
 for src,dst,ro in mounts:args+=['--mount',f'type=bind,src={src},dst={dst}'+(',readonly' if ro else '')]
 for key,val in sorted(env.items()):args+=['-e',key+'='+val]
 serve=runtime['argv']+['--node-rank',str(h['rank']),'--master-addr',cfg['hosts'][0]['ip'],'--master-port',str(cfg['master_port'])]
 serve+=['--host','0.0.0.0','--port',str(cfg['api_port'])] if h['rank']==0 else ['--headless']
 return args+[cfg['image_id']]+serve

def docker_group_command(command):
 """Activate existing membership even when the user manager has stale groups."""
 user=pwd.getpwuid(os.getuid());group=grp.getgrnam('docker')
 assert user.pw_uid==0 or user.pw_gid==group.gr_gid or user.pw_name in group.gr_mem, 'Provision this user as a docker group member before starting Tempo'
 return ['sg','docker','-c',shlex.join([str(x) for x in command])]

def guard_command(unit,payload):
 return ['systemd-run','--user','--unit',unit,'--property=Restart=no','--property=MemoryMax=128M','--property=MemorySwapMax=0']+docker_group_command(payload)

def action(req):
 cfg=req['config'];h=req['host'];base=Path(h['work'])/cfg['deployment'];kind=req['action'];cid=req.get('cid')
 if kind=='render':return dict(argv=argv(cfg,h))
 if kind=='preflight':
  assert platform.machine()=='aarch64';assert Path('/sys/fs/cgroup/cgroup.controllers').exists();assert Path('/dev/infiniband').exists()
  assert json.loads(docker('image','inspect',cfg['image_id']))[0]['Id']==cfg['image_id']
  capture(['systemctl','--user','show-environment'])
  capture(['systemd-run','--user','--wait','--pipe','--collect']+docker_group_command(['docker','version','--format','{{.Server.Version}}']))
  seal=json.loads((Path(h['work'])/'verified-inputs.json').read_text())
  assert seal['release_sources_sha256']==digest(ROOT/'release/sources.json')
  for name,stamp in seal['files'].items():
   st=Path(name).stat();assert [st.st_size,st.st_mtime_ns,st.st_ino]==stamp,'Verified input changed: '+name
  img=json.loads(docker('image','inspect',cfg['image_id']))[0]
  if cfg['image_provenance']=='verified-historical-cache':assert cfg['image_id']==read('release/runtime.json')['historical_image_id']
  else:assert img['Config'].get('Labels',{}).get('jspark3.sources')==digest(ROOT/'release/sources.json'),'Image lacks pinned source-build identity'
  assert Path('/sys/class/net',h['interface']).exists()
  for hca in h['hca'].split(','):assert Path('/sys/class/infiniband',hca).exists()
  routes=json.loads(capture(['ip','-j','route','show']))
  for subnet in h['fabric_subnets']:assert any(x.get('dst')==subnet for x in routes),'Missing fabric route '+subnet
  for row in read('release/patches.json'):verify(ROOT/row['file'],row['sha256'])
  for folder in ['model','engram','packed']:assert Path(h[folder]).is_dir(),folder
  assert not any((base/'state'/marker).exists() for marker in ['release','TRACE-CACHE','TRACE-SCHEDULER','TRACE-ENGRAM'])
  mem=next(int(l.split()[1])*1024 for l in open('/proc/meminfo') if l.startswith('MemAvailable:'))
  if req.get('resources'):assert mem>=100*1024**3,'Need >=100 GiB host MemAvailable after outgoing service stops'
  return dict(status='PASS',rank=h['rank'],memavailable_bytes=mem,resource_gate=bool(req.get('resources')))
 if kind=='start':
  action(dict(req,action='preflight',resources=True));assert not base.exists(),'Fresh deployment namespace required'
  (base/'cache').mkdir(parents=True);(base/'state').mkdir();os.chmod(ROOT/'recipe/entrypoint.sh',0o755)
  cid=capture(argv(cfg,h));d=owned(cid,cfg);cg,_=resources(cid)
  unit=cfg['deployment']+'-guard';command=guard_command(unit,['python3','-S',ROOT/'tools/exl3_guard.py','--container-id',cid,'--cgroup',cg,'--expected-started-at',d['State']['StartedAt'],'--telemetry',base/'guard.jsonl','--trip-file',base/'trip','--peer-trip-file',base/'peer-trip'])
  # Receipt is saved before guard launch; a failed guard can still be stopped by exact ID.
  receipt=dict(cid=cid,started_at=d['State']['StartedAt'],image_id=d['Image'],rank=h['rank'],unit=unit)
  write(base/'owned.json',receipt)
  try:capture(command)
  except Exception:docker('stop','-t','15',cid);raise
  return receipt
 if kind in ['status','stop','release','trip','native']:
  d=owned(cid,cfg)
  assert d['State']['StartedAt']==req['started_at'],'Incarnation changed'
  if kind=='stop':
   if d['State']['Running']:docker('stop','-t','30',cid)
   subprocess.run(['systemctl','--user','stop',cfg['deployment']+'-guard'],check=False,capture_output=True)
   return dict(stopped=cid)
  if kind=='trip':(base/'peer-trip').touch();return dict(trip=True)
  if kind=='release':
   assert not (base/'trip').exists() and not (base/'peer-trip').exists();(base/'state/release').touch(exist_ok=False);return dict(released=cid)
  if kind=='native':
   expected={r['target']:r['sha256'] for r in read('release/patches.json')}
   script='import json,hashlib;d=json.loads(__import__("sys").argv[1]);\nfor p,h in d.items():\n assert hashlib.sha256(open(p,"rb").read()).hexdigest()==h,p\nprint("PASS loaded source hashes")'
   docker('exec',cid,'python3','-S','-c',script,json.dumps(expected))
   inventory_script='import pathlib,hashlib,json,sys; names=json.loads(sys.argv[1]); out={};\nfor name in names:\n p=pathlib.Path(name);assert p.is_file(),name;out[name]=hashlib.sha256(p.read_bytes()).hexdigest()\nprint(json.dumps(dict(native=out,pth_hooks={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path("/usr/local/lib/python3.12/dist-packages").glob("*.pth")})))'
   historical=read('release/native-historical.json')
   inv=json.loads(docker('exec',cid,'python3','-S','-c',inventory_script,json.dumps(list(historical['files'])+list(historical['parsers']))))
   assert all(inv['native'][p]==sha for p,sha in historical['parsers'].items()),'Parser source drift'
   if cfg['image_provenance']=='verified-historical-cache':assert all(inv['native'][p]==sha for p,sha in historical['files'].items()),'Historical binary drift'
   return dict(source_hashes='PASS',image_id=d['Image'],**inv)
  cg,res=resources(cid);last=None
  if (base/'guard.jsonl').exists():
   with open(base/'guard.jsonl','rb') as f:
    f.seek(max(0,f.seek(0,2)-131072));lines=f.read().decode().splitlines()[-20:]
   for line in lines:
    row=json.loads(line)
    if row.get('kind')=='sample':last=row
  return dict(cid=cid,started_at=d['State']['StartedAt'],running=d['State']['Running'],restart_count=d['RestartCount'],oom=d['State']['OOMKilled'],resources=res,guard=last,trip=(base/'trip').exists() or (base/'peer-trip').exists())
 raise RuntimeError('Unknown action')
if __name__=='__main__':
 try:print(json.dumps(action(json.load(sys.stdin))))
 except Exception as e:print(json.dumps(dict(error=str(e))),file=sys.stderr);sys.exit(1)
