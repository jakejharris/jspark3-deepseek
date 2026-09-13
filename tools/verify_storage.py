#!/usr/bin/env python3
"""Full content verification before reuse, including packed payload after header."""
import argparse,hashlib,json,os
from pathlib import Path
from common import read,digest

def verify_storage(rank,engram,packed):
 rows=[r for r in read('release/engram-ranges.json') if r['rank']==rank]
 local=json.loads((engram/'engram-local.json').read_text())['layers']
 for row in rows:
  assert local[str(row['layer'])]==row['row_range'],'Engram ownership drift'
  for part in row['ranges']:
   h=hashlib.sha256();remaining=part['bytes']
   with open(engram/part['shard'],'rb') as f:
    f.seek(part['file_offset'])
    while remaining:
     data=f.read(min(8<<20,remaining));assert data,'Short sparse range';h.update(data);remaining-=len(data)
   assert h.hexdigest()==part['sha256'],'Sparse range hash mismatch'
 for row in [r for r in read('release/packed.json') if r['rank']==rank]:
  path=packed/f"layer{row['layer']}.packed"
  with open(path,'rb') as f:header=json.loads(f.read(4096).split(b'\0')[0])
  assert header['complete'] and header['format']=='JSPARK_ENGRAM_PACKED_V1'
  assert all(header[k]==row[k] for k in ['layer','row_start','row_end','payload_sha256'])
  assert header['row_bytes']==264 and header['header_bytes']==4096
  assert path.stat().st_size==4096+(row['row_end']-row['row_start'])*264
  assert digest(path,4096)==row['payload_sha256'],'Packed payload hash mismatch'
 print(f'PASS: rank {rank} sparse ranges and both complete packed payloads')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--rank',type=int,choices=range(3),required=True);p.add_argument('--engram',type=Path,required=True);p.add_argument('--packed',type=Path,required=True);a=p.parse_args();verify_storage(a.rank,a.engram,a.packed)
