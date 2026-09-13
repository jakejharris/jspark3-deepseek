#!/usr/bin/env python3
"""Verify the frozen explicit artifact inventory and publication data contract."""
import argparse,json,re,sys,hashlib
from pathlib import Path
from common import ROOT,read,write,digest
from export_release import render
PUBLIC_DIRS=['recipe','tools','docs','patches','licenses','evidence','huggingface','tests','benchmarks','build','.github']
PUBLIC_FILES=['README.md','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','.gitignore']
def artifacts():
 paths=[ROOT/n for n in PUBLIC_FILES]
 for directory in PUBLIC_DIRS+['release']:
  paths += [p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name not in ['manifest.json','SHA256SUMS']]
 return sorted({str(p.relative_to(ROOT)) for p in paths})
def audit():
 for name,want in render().items():
  assert (ROOT/name).read_text()==want,'Generated drift: '+name
 identity=read('release/identity.json')
 for key in ['fresh_source_build','fresh_install','fresh_runtime_smoke']:
  row=identity['validation'][key];assert row['status'] in ['PENDING','PASS','FAIL'],key
  if row['status']=='PASS':
   assert row['evidence'] and row['evidence'].startswith('evidence/') and '..' not in Path(row['evidence']).parts,key
   assert digest(ROOT/row['evidence'])==row['evidence_sha256'],key
   receipt=read(row['evidence'])
   assert receipt['validated_commit'] and receipt['validated_manifest_sha256'] and receipt['validated_candidate'],key
   assert receipt['checks'][key]=='PASS',key
 metrics=read('release/benchmarks.json')['metrics'];assert len({m['id'] for m in metrics})==len(metrics)
 for m in metrics:
  for field in ['id','value','unit','estimator','samples','cohort','cache_state','timing_boundary','concurrency','active_cap','source_sha256','caveats']:assert field in m,(m['id'],field)
 for row in read('release/patches.json'):assert digest(ROOT/row['file'])==row['sha256'],row['file']
 # Public-source paths are legitimate; private workspace/hosts/tokens are not.
 forbidden=[r'/home/jake(?:jh)?/',r'/mnt' + r'/c/',r'192\.168\.\d+\.\d+',r'hf_[A-Za-z0-9]{24,}',r'gh[pousr]_[A-Za-z0-9]{24,}']
 for name in artifacts():
  text=(ROOT/name).read_text(errors='replace')
  for pattern in forbidden:assert not re.search(pattern,text),'Private material pattern in '+name

def main():
 p=argparse.ArgumentParser();p.add_argument('--freeze',action='store_true');a=p.parse_args();audit();manifest=ROOT/'release/manifest.json'
 if a.freeze:
  assert not manifest.exists(),'Manifest already frozen; candidate changes require an explicit new freeze/review'
  identity=read('release/identity.json');sources=read('release/sources.json')
  m=dict(schema_version=1,release=identity['identity'],release_commit=None,commit_binding='External candidate/tag receipt binds this manifest checksum to the Git commit after freeze; no self-reference.',status='validated' if all(r['status']=='PASS' for r in identity['validation'].values()) else 'ready-for-installer',upstream=dict(target=dict(repo='deepseek-ai/DeepSeek-V4.1-Flash',revision='2bc89ac599031fa673cab993f1df02fc4a98c673'),experts=dict(repo='bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard',revision='b60193e0609147553145d1538d935925f2763c1d'),draft=dict(kind='bundled DeepSeek DSpark, unchanged components in selected EXL3 snapshot',revision='b60193e0609147553145d1538d935925f2763c1d',separate_download=False),recipe=dict(repo='tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark',revision='c2c1bb7ee7d2f5faee76c07fd9fc9f27e3a20bc2'),build=sources),hardware=dict(hosts=3,gpu='NVIDIA GB10',unified_memory_per_host_gb=128,topology='dual-port RoCE-v2 IPv4 triangle; management LAN'),configuration=read('release/runtime.json'),inputs=dict(patches='release/patches.json',model='release/model-files.json',sparse='release/engram-ranges.json',packed='release/packed.json'),capabilities=dict(openai_api=True,tools=True,prefix_cache=True,native_vision='optional separate validation; not throughput-qualified'),tested_scope=dict(historical='L5-P measured September 13, 2026; experimental daily driver with unresolved code 2/4 and inverse-arrival 40.458 s limits',local='CPU source/preparation/failure checks; public inputs verified',**identity['validation'],independent_hardware_reproduction=False),evidence=dict(benchmarks='release/benchmarks.json',selected='evidence/selected-measurements.json',archived_deployment_manifest_sha256='3556113254de788ee188f1cc1f929438b30b79b58e0d2299f3c92edf7578d1c5'),notices='THIRD_PARTY_NOTICES.md',publication=dict(status=identity['publication_status'],github='jakejharris/jspark3-deepseek',huggingface='jakejharris/jspark3-tempo',weights_uploaded=False),artifacts={name:digest(ROOT/name) for name in artifacts()})
  write(manifest,m)
 else:
  m=read('release/manifest.json');assert set(artifacts())==set(m['artifacts']),'Artifact inventory drift'
  for name,want in m['artifacts'].items():assert digest(ROOT/name)==want,'Artifact hash drift: '+name
 print('PASS: release artifacts, sources, public scan and exports match')
if __name__=='__main__':main()
