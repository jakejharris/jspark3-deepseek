#!/usr/bin/env python3
"""Prepare only public, pinned Work inputs in a new directory; no model requests."""
import argparse,hashlib,json,shutil,subprocess,tarfile,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def verify_fixture(destination):
 pin=json.loads((ROOT/'fixture.json').read_text())
 actual={str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file()}
 assert actual==pin['files'],'Fixture contents differ from historical public corpus'

def prepare(destination,archive,endpoint):
 assert not destination.exists(),'Use a fresh directory; existing files are never removed'
 assert endpoint.startswith(('http://','https://')) and not endpoint.endswith('/v1'),'Supply HTTP root without /v1'
 pin=json.loads((ROOT/'fixture.json').read_text())
 assert sha(archive)==pin['public_archive_sha256'],'Public archive hash mismatch'
 destination.mkdir(parents=True);fixtures=destination/'fixtures';fixtures.mkdir()
 with tarfile.open(archive) as tar:
  for member in tar.getmembers():
   parts=Path(member.name).parts[1:]
   if not parts:continue
   assert '..' not in parts and not member.issym() and not member.islnk(),'Unsafe fixture archive member'
   path=fixtures/'jspark3'/Path(*parts)
   if member.isdir():path.mkdir(parents=True,exist_ok=True)
   else:
    assert member.isfile(),'Unsupported archive member';path.parent.mkdir(parents=True,exist_ok=True)
    with tar.extractfile(member) as source,path.open('wb') as target:shutil.copyfileobj(source,target)
    path.chmod(member.mode & 0o777)
 for name in ['threejs','python']:(fixtures/name).mkdir()
 defect=fixtures/'jspark3'/pin['injected_defect']['path'];lines=defect.read_text().splitlines(keepends=True)
 changed=[]
 for i,line in enumerate(lines):
  if line.lstrip().startswith('if ') and 'num_reqs >= 2' in line:lines[i]=line.replace('num_reqs >= 2','num_reqs > 2');changed.append(i+1)
 assert changed==pin['injected_defect']['changed_lines'],'Seeded defect no longer matches'
 defect.write_text(''.join(lines));verify_fixture(fixtures)
 agent=destination/'pi-agent';agent.mkdir()
 model={'id':'deepseek-v4.1-flash','name':'deepseek-v4.1-flash','reasoning':True,'input':['text'],'contextWindow':262144,'maxTokens':65536,'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0},'samplingParams':{'temperature':0,'max_tokens':65536},'thinkingLevelMap':{'minimal':None,'low':'low','medium':'high','high':'xhigh','xhigh':None,'max':'max'},'compat':{'supportsDeveloperRole':False,'thinkingFormat':'chat-template','chatTemplateKwargs':{'thinking':{'$var':'thinking.enabled'},'reasoning_effort':{'$var':'thinking.effort'}}}}
 write(agent/'models.json',{'providers':{'deepseek-v4.1-flash':{'baseUrl':endpoint+'/v1','api':'openai-completions','apiKey':'local-no-secret','models':[model]}}})
 write(agent/'settings.json',{'compaction':{'enabled':False},'retry':{'enabled':False}})
 write(destination/'prepared.json',{'endpoint':endpoint,'fixture_manifest_sha256':sha(ROOT/'fixture.json'),'prompts_sha256':sha(ROOT/'prompts.json'),'observer_sha256':sha(ROOT/'observer.ts'),'adapter_sha256':sha(agent/'models.json'),'settings_sha256':sha(agent/'settings.json'),'pi_version':'0.84.2'})

def main():
 p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True);p.add_argument('--archive',type=Path,required=True);p.add_argument('--endpoint',required=True);a=p.parse_args()
 if not a.archive.exists():
  pin=json.loads((ROOT/'fixture.json').read_text());a.archive.parent.mkdir(parents=True,exist_ok=True)
  with urllib.request.urlopen(pin['public_archive_url'],timeout=60) as source,a.archive.open('xb') as target:shutil.copyfileobj(source,target)
 prepare(a.destination.resolve(),a.archive,a.endpoint.rstrip('/'));print('Work inputs match all historical fixture hashes')
if __name__=='__main__':main()
