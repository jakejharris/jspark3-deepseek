#!/usr/bin/env python3
"""Pinned downloads and derived TP3 model; sparse Engram/packing are separate."""
import argparse,json,os,shutil,urllib.request
from pathlib import Path
from common import ROOT,read,write,verify,run
from fetch_sources import fetch
REPO='bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard'
REV='b60193e0609147553145d1538d935925f2763c1d'
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['download','model','verify-model']);p.add_argument('--source',type=Path,required=True);p.add_argument('--model',type=Path);a=p.parse_args()
 pins=read('release/weights-metadata.json');base=f'https://huggingface.co/{REPO}/resolve/{REV}/'
 if a.action=='download':
  a.source.mkdir(parents=True,exist_ok=True)
  rows=[dict(file=x['path'],sha256=x['sha256']) for x in pins['weights'][:46]]+read('release/metadata-files.json')
  for row in rows:fetch(dict(**row,url=base+row['file']),a.source)
  print('PASS: 46 shards and five metadata files verified');return
 assert a.model,'--model required'
 if a.action=='verify-model':
  for row in read('release/model-files.json'):verify(a.model/row['path'],row['sha256'])
  assert len(list(a.model.glob('model-*.safetensors')))==46
  print('PASS: 52 model files match historical L5-P bytes');return
 for row in pins['weights'][:46]:verify(a.source/row['path'],row['sha256'])
 for row in read('release/metadata-files.json'):verify(a.source/row['file'],row['sha256'])
 assert not a.model.exists(),'Choose a fresh model destination; preserve partial output for inspection'
 a.model.mkdir(parents=True)
 for row in pins['weights'][:46]:
  src=a.source/row['path'];dst=a.model/row['path']
  try:os.link(src,dst)
  except OSError:shutil.copyfile(src,dst)
 for name in ['tokenizer.json','tokenizer_config.json','quantization_config.json']:shutil.copyfile(a.source/name,a.model/name)
 config=json.loads((a.source/'config.json').read_text());tc=config['text_config'];assert(tc['num_attention_heads'],tc['o_groups'])==(64,8)
 original=dict(num_attention_heads=64,o_groups=8);tc.update(num_attention_heads=72,o_groups=9,virtual_heads_from=original);config['virtual_heads_from']=original
 config['kai_tp3_virtual_heads']='64 real heads / 8 o_groups padded to 72 / 9 for tensor-parallel 3 (virtual_heads.py)'
 write(a.model/'config.json',config)
 run(['python3',ROOT/'tools/build_projection_sidecar.py','--http','--allow-anonymous','--metadata',ROOT/'release/weights-metadata.json','--output',a.model/'engram-projections.safetensors','--receipt',a.model/'projection-receipt.json','--commit'])
 run(['python3',ROOT/'tools/filter_index.py','--index',a.source/'model.safetensors.index.json','--sizes',ROOT/'release/weights-metadata.json','--output',a.model/'model.safetensors.index.json','--projections-sidecar','engram-projections.safetensors','--sidecar-bytes',(a.model/'engram-projections.safetensors').stat().st_size,'--receipt',a.model/'index-receipt.json','--commit'])
 for row in read('release/model-files.json'):verify(a.model/row['path'],row['sha256'])
 print('PASS: derived model matches all 52 historical files')
if __name__=='__main__':main()
