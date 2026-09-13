#!/usr/bin/env python3
"""Flatten pinned preimages directly to the final L5-P files."""
import argparse,shutil
from pathlib import Path
from common import read,ROOT,verify,digest

def apply(root):
 rows=read('release/patches.json')
 # Verify the whole set before any write.
 for row in rows:
  src=ROOT/row['file'];verify(src,row['sha256']);dst=root/row['target'].lstrip('/')
  pre=row['preimage_sha256']
  if pre is None:
   if dst.exists():raise RuntimeError('Expected absent preimage: '+str(dst))
  else:verify(dst,pre)
 for row in rows:
  dst=root/row['target'].lstrip('/');dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/row['file'],dst);verify(dst,row['sha256'])
 print('PASS: 15 final L5-P source overlays installed')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/'));a=p.parse_args();apply(a.root)
