#!/usr/bin/env python3
"""Fetch exact public build inputs; existing complete bytes are rehashed."""
import argparse,urllib.request,os
from pathlib import Path
from common import read,verify,write

def fetch(pin,root):
 dst=root/pin['file']
 if dst.exists():verify(dst,pin['sha256']);return
 tmp=dst.with_suffix(dst.suffix+'.incomplete')
 print('Downloading',pin['file'],flush=True)
 with urllib.request.urlopen(pin['url'],timeout=120) as src,open(tmp,'wb') as out:
  while b:=src.read(8<<20):out.write(b)
  out.flush();os.fsync(out.fileno())
 verify(tmp,pin['sha256']);os.replace(tmp,dst)
def main():
 p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True);a=p.parse_args();a.destination.mkdir(parents=True,exist_ok=True)
 pins=read('release/sources.json')
 for pin in list(pins['archives'].values())+[pins['cmake']]:fetch(pin,a.destination)
 write(a.destination/'pins.json',pins);print('PASS: all public build inputs hash verified')
if __name__=='__main__':main()
