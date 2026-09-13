#!/usr/bin/env python3
"""Full verification of declared reusable host inputs, followed by metadata seal."""
import argparse,json,subprocess
from pathlib import Path
from common import ROOT,read,verify,write,digest
from verify_storage import verify_storage

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--rank',type=int,choices=range(3),required=True);a=p.parse_args();c=json.loads(a.config.read_text());h=c['hosts'][a.rank]
 files=[]
 for row in read('release/model-files.json'):
  path=Path(h['model'])/row['path'];verify(path,row['sha256']);files.append(path)
 verify_storage(a.rank,Path(h['engram']),Path(h['packed']))
 files += [Path(h['engram'])/name for name in ['engram-local.json','model.safetensors.index.json','model-00047-of-00048.safetensors','model-00048-of-00048.safetensors']]
 files += [Path(h['packed'])/f'layer{layer}.packed' for layer in [1,14]]
 stamps={str(p):[p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ino] for p in files}
 dest=Path(h['work'])/'verified-inputs.json';write(dest,dict(release_sources_sha256=digest(ROOT/'release/sources.json'),files=stamps));print('PASS:',dest)
if __name__=='__main__':main()
