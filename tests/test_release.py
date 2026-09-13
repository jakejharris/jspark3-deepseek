import copy,hashlib,json,struct
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
import apply_patches,pack_engram,tempo,smoke,node
from common import ROOT,read

def test_overlay_preimage_failure_writes_nothing(tmp_path,monkeypatch):
 src=tmp_path/'new';src.write_text('new');a=tmp_path/'a';a.write_text('original');b=tmp_path/'b';b.write_text('wrong')
 rows=[dict(file='new',target='/'+name,sha256=hashlib.sha256(b'new').hexdigest(),preimage_sha256=hashlib.sha256(b'original').hexdigest()) for name in ['a','b']]
 monkeypatch.setattr(apply_patches,'ROOT',tmp_path);monkeypatch.setattr(apply_patches,'read',lambda _:rows)
 with pytest.raises(RuntimeError):apply_patches.apply(tmp_path)
 assert a.read_text()=='original' and b.read_text()=='wrong'

def test_pack_preserves_bytes_and_refuses_overwrite(tmp_path):
 src=tmp_path/'source';src.mkdir();names={};weights=bytes(range(256))*5;scales=bytes(range(40));meta={};offset=0
 for name,data,shape in [('layers.1.engram.embed.weight',weights,[5,256]),('layers.1.engram.embed.scale',scales,[5,8])]:
  meta[name]=dict(dtype='U8',shape=shape,data_offsets=[offset,offset+len(data)]);offset+=len(data);names[name]='fake.safetensors'
 raw=json.dumps(meta).encode();(src/'fake.safetensors').write_bytes(struct.pack('<Q',len(raw))+raw+weights+scales);(src/'model.safetensors.index.json').write_text(json.dumps(dict(weight_map=names)))
 out=tmp_path/'packed';pack_engram.pack(src,out,1,1,4,2)
 body=(out/'layer1.packed').read_bytes();h=json.loads(body[:4096].split(b'\0')[0]);payload=body[4096:]
 assert h['complete'] and h['payload_sha256']==hashlib.sha256(payload).hexdigest()
 assert payload==b''.join(weights[i*256:(i+1)*256]+scales[i*8:(i+1)*8] for i in range(1,4))
 with pytest.raises(AssertionError):pack_engram.pack(src,out,1,1,4,2)

def guard_row():
 now=datetime.now(timezone.utc).isoformat();return dict(trip=False,running=True,restart_count=0,oom=False,cid='a'*64,started_at='start',resources={'memory.swap.current':0,'memavailable_bytes':8*1024**3},guard=dict(utc=now,cid='a'*64,state='WATCHING',docker=dict(started_at='start')))
@pytest.mark.parametrize('fault',['stale','future','cid','incarnation','floor','restart','trip'])
def test_relay_refuses_bad_peer(fault):
 row=guard_row();assert tempo.guard_ok(row)
 if fault=='stale':row['guard']['utc']=(datetime.now(timezone.utc)-timedelta(seconds=16)).isoformat()
 elif fault=='future':row['guard']['utc']=(datetime.now(timezone.utc)+timedelta(seconds=8)).isoformat()
 elif fault=='cid':row['guard']['cid']='b'*64
 elif fault=='incarnation':row['guard']['docker']['started_at']='other'
 elif fault=='floor':row['resources']['memavailable_bytes']=3*1024**3
 elif fault=='restart':row['restart_count']=1
 else:row['trip']=True
 assert not tempo.guard_ok(row)

def test_stop_rejects_unowned_container(monkeypatch):
 monkeypatch.setattr(node,'inspect',lambda cid:dict(Id=cid,Config={'Labels':{'jspark3.deployment':'other'}}))
 with pytest.raises(AssertionError):node.owned('a'*64,{'deployment':'tempo-test'})

class Response:
 def __init__(self,events):self.events=events
 def __enter__(self):return self
 def __exit__(self,*_):pass
 def __iter__(self):return iter(self.events)
def event(obj):return ('data: '+json.dumps(obj)+'\n\n').encode()
@pytest.mark.parametrize('broken',['missing_done','error','length','missing_usage'])
def test_smoke_rejects_http200_but_bad_stream(monkeypatch,broken):
 events=[event({'choices':[{'delta':{'content':'1'},'finish_reason':None}]}),event({'choices':[{'delta':{},'finish_reason':'length' if broken=='length' else 'stop'}]})]
 if broken!='missing_usage':events.append(event({'choices':[],'usage':{'completion_tokens':1}}))
 if broken=='error':events.append(event({'error':{'message':'failed'}}))
 if broken!='missing_done':events.append(b'data: [DONE]\n')
 monkeypatch.setattr(smoke.urllib.request,'urlopen',lambda *a,**k:Response(events))
 with pytest.raises((AssertionError,RuntimeError)):smoke.stream('http://example.test',[])

def test_all_final_overlay_hashes_and_graph_settings():
 for row in read('release/patches.json'):assert hashlib.sha256((ROOT/row['file']).read_bytes()).hexdigest()==row['sha256']
 r=read('release/runtime.json');args=r['argv'];graph=json.loads(args[args.index('--compilation-config')+1]);assert graph['cudagraph_capture_sizes']==[4,5,8,10,12,15,16,20,24,25,28,30,32,35,40]
 assert r['env']['JSPARK_MIXED_PREFILL_TOKENS']=='2048'
 assert r['env']['JSPARK_ENGRAM_PACKED']=='buffered'

def test_diagnostics_drops_unknown_fields_and_private_values(monkeypatch):
 row=guard_row();row['resources'].update({'memory.current':5,'events':{'oom':0,'prompt':'SECRET'},'credential':'SECRET'});row['prompt']='SECRET';row['cid']='PRIVATE-CID';row['hostname']='PRIVATE-HOST'
 monkeypatch.setattr(tempo,'digest',lambda _: 'release-hash')
 raw=json.dumps(tempo.redacted_diagnostics([row]));assert 'SECRET' not in raw and 'PRIVATE' not in raw and 'prompt' not in raw and 'credential' not in raw
 assert json.loads(raw)['ranks'][0]['resources']['events']=={'oom':0}

@pytest.mark.parametrize('swap_bytes',[1,3608576,2*1024**3])
def test_relay_accepts_swap_without_other_faults(swap_bytes):
 row=guard_row();row['resources']['memory.swap.current']=swap_bytes
 assert tempo.guard_ok(row)
 row['resources']['memavailable_bytes']=3*1024**3
 assert not tempo.guard_ok(row)
