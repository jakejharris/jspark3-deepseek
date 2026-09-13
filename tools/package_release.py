#!/usr/bin/env python3
"""Deterministic tarball for GitHub/HF; weights and local receipts excluded."""
import argparse,gzip,hashlib,io,tarfile
from pathlib import Path
from common import ROOT,read,write,digest
from release_check import audit,artifacts

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();audit();manifest=read('release/manifest.json')
 for name,sha in manifest['artifacts'].items():assert digest(ROOT/name)==sha,name
 files=sorted(list(manifest['artifacts'])+['release/manifest.json']);a.output.mkdir(parents=True,exist_ok=True);tag=manifest['release']['candidate'];archive=a.output/(tag+'.tar.gz')
 with open(archive,'wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w|') as tf:
  for name in files:
   data=(ROOT/name).read_bytes();info=tarfile.TarInfo('jspark3-tempo/'+name);info.size=len(data);info.mode=0o755 if name.endswith('.sh') else 0o644;info.mtime=0;tf.addfile(info,io.BytesIO(data))
 (a.output/'SHA256SUMS').write_text(f'{digest(archive)}  {archive.name}\n')
 (a.output/'README.md').write_bytes((ROOT/'huggingface/README.md').read_bytes())
 (a.output/'summary.json').write_bytes((ROOT/'release/summary.json').read_bytes())
 print('Packaged',archive,'— inspect and publish through the coordinator; nothing uploaded')
if __name__=='__main__':main()
