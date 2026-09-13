#!/usr/bin/env python3
"""Bounded release smoke: SSE completeness, generation, APC, tool continuation."""
import argparse,json,time,urllib.request,uuid,re,hashlib
from pathlib import Path
from datetime import datetime,timezone
from common import ROOT,read,write,digest
from tempo import config,parallel
MODEL='deepseek-v4.1-flash'
TOOLS=[{'type':'function','function':{'name':'lookup_temperature','description':'Read current temperature for a city.','parameters':{'type':'object','properties':{'city':{'type':'string'}},'required':['city']}}}]

def get(base,path):
 with urllib.request.urlopen(base+path,timeout=20) as f:return f.read().decode()
def metrics(base):
 text=get(base,'/metrics');names=['prefix_cache_hits_total','prefix_cache_queries_total','num_requests_running','num_requests_waiting','num_preemptions_total','request_success_total'];result={n:0 for n in names};seen=set()
 for line in text.splitlines():
  if line.startswith('#'):continue
  for name in names:
   if re.match(r'vllm:'+name+r'(?:\{|\s)',line):result[name]+=float(line.rsplit(' ',1)[1]);seen.add(name)
 if seen!=set(names):raise RuntimeError('Required engine metrics missing: '+str(set(names)-seen))
 return result

def stream(base,messages,**extra):
 body=dict(model=MODEL,messages=messages,max_tokens=192,temperature=0,stream=True,stream_options={'include_usage':True},chat_template_kwargs={'thinking':False},**extra)
 start=time.monotonic();first=None;done=False;finish=None;usage=None;content='';calls={}
 req=urllib.request.Request(base+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as response:
  for raw in response:
   if time.monotonic()-start>180:raise RuntimeError('Stream exceeded bounded wall budget')
   line=raw.decode().strip()
   if not line.startswith('data:'):continue
   data=line[5:].strip()
   if data=='[DONE]':done=True;break
   event=json.loads(data)
   if 'error' in event:raise RuntimeError('Backend SSE error')
   if event.get('usage'):usage=event['usage']
   for choice in event.get('choices',[]):
    delta=choice.get('delta',{})
    if first is None and (delta.get('content') or delta.get('reasoning') or delta.get('reasoning_content') or delta.get('tool_calls')):first=time.monotonic()-start
    content+=delta.get('content') or ''
    for part in delta.get('tool_calls',[]):
     call=calls.setdefault(part['index'],dict(id='',type='function',function=dict(name='',arguments='')))
     if part.get('id'):call['id']+=part['id']
     for key in ['name','arguments']:call['function'][key]+=part.get('function',{}).get(key) or ''
    if choice.get('finish_reason'):finish=choice['finish_reason']
 wall=time.monotonic()-start
 assert done and finish in ['stop','tool_calls'] and first is not None and usage,'Incomplete/error/truncated stream'
 tokens=usage['completion_tokens'];assert tokens>0
 return dict(ttft_s=first,http_s=wall,completion_tokens=tokens,end_to_end_tok_s=tokens/wall,post_first_output_tok_s=(tokens-1)/(wall-first) if wall>first else None,finish_reason=finish,done=done,content=content,tool_calls=[calls[k] for k in sorted(calls)],request=body)

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();c=config(a.config);state=Path(c['state_dir'])/c['deployment'];receipts=json.loads((state/'owned.json').read_text());base=c['endpoint'].rstrip('/')
 report=dict(schema_version=1,version=read('release/identity.json')['identity']['candidate'],at=datetime.now(timezone.utc).isoformat(),manifest_sha256=digest(ROOT/'release/manifest.json'),deployment=receipts,conditions='C1, thinking off, temperature 0, max192 tokens, warmed generation before unique-salt prompt; isolated engine counters required.',requests=[])
 try:
  get(base,'/health');models=json.loads(get(base,'/v1/models'));assert any(x['id']==MODEL for x in models['data']);report['http_ready']=True
  report['resources_before']=parallel(c,'status',receipts);before=metrics(base)
  assert before['num_requests_running']==before['num_requests_waiting']==0,'Drain other clients before smoke'
  generation=stream(base,[dict(role='user',content='List the integers from 1 through 20 in ascending order, separated by spaces. Output only the numbers.')])
  assert [int(n) for n in re.findall(r'\d+',generation['content'])]==list(range(1,21)),'Generation correctness failed';report['requests'].append(dict(name='generation',**generation))
  salt=uuid.uuid4().hex;prefix=' '.join(f'Record {n}: the archive contains a blue triangle.' for n in range(400))
  messages=[dict(role='user',content=prefix+'\nReply with only the word READY.')]
  pre=metrics(base);cold=stream(base,messages,cache_salt=salt);middle=metrics(base);warm=stream(base,messages,cache_salt=salt);post=metrics(base)
  assert cold['content'].strip()=='READY' and warm['content'].strip()=='READY','Repeated-prefix correctness failed'
  assert middle['prefix_cache_hits_total']-pre['prefix_cache_hits_total']==0,'Cold prompt was cached or concurrent traffic interfered'
  hits=post['prefix_cache_hits_total']-middle['prefix_cache_hits_total'];assert hits>0,'No observed prefix reuse'
  report['requests'] += [dict(name='uncached_prefix',**cold),dict(name='repeated_prefix',**warm)]
  report['reuse']=dict(engine_counter_delta=hits,scope='after-minus-before isolated engine counters; not inferred from latency')
  messages=[dict(role='user',content='Use lookup_temperature for Oslo, then state only the integer temperature it returns.')]
  call=stream(base,messages,tools=TOOLS,tool_choice='auto');assert len(call['tool_calls'])==1
  tc=call['tool_calls'][0];assert tc['function']['name']=='lookup_temperature';assert json.loads(tc['function']['arguments'])=={'city':'Oslo'}
  messages += [dict(role='assistant',content=call['content'] or None,tool_calls=call['tool_calls']),dict(role='tool',tool_call_id=tc['id'],content='{"temperature":17}')]
  continuation=stream(base,messages,tools=TOOLS,tool_choice='auto');assert continuation['content'].strip()=='17' and not continuation['tool_calls']
  report['requests'] += [dict(name='tool_call',**call),dict(name='tool_continuation',**continuation)]
  after=metrics(base);assert after['request_success_total']-before['request_success_total']==5,'Concurrent requests make smoke counters ambiguous'
  assert after['num_preemptions_total']==before['num_preemptions_total'],'Preemption during smoke'
  report['resources_after']=parallel(c,'status',receipts)
  for row in report['resources_before']+report['resources_after']:
   assert row['restart_count']==0 and not row['oom'] and not row['trip']
   assert row['resources']['memory.swap.current']==0 and row['resources']['memavailable_bytes']>=5*1024**3,'Memory HOLD/fault during smoke'
  report['status']='PASS'
 except Exception as e:report['status']='FAIL';report['error']=str(e)
 finally:
  # Prompts are public test fixtures; these results are local, never automatically attached/uploaded.
  write(a.output,report)
 print('Tempo smoke',report['status'])
 for r in report['requests']:print(f"{r['name']}: TTFT {r['ttft_s']:.3f}s; full HTTP {r['end_to_end_tok_s']:.2f} tok/s; post-first-output {r['post_first_output_tok_s']:.2f} tok/s")
 print('JSON:',a.output)
 if report['status']!='PASS':raise SystemExit(report.get('error','smoke failed'))
if __name__=='__main__':main()
