#!/usr/bin/env python3
"""ARM64 host only; bounded 32 GiB CPU build, retained checkpoint layers."""
import argparse,os,platform,shutil,subprocess
from pathlib import Path
from common import ROOT,read,verify,run,write,digest

def main():
 p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--tag',default='jspark3-tempo:v2.0.0-rc.1');a=p.parse_args()
 assert platform.machine()=='aarch64','Build on a Spark ARM64 host'
 mem={l.split(':')[0]:int(l.split()[1])*1024 for l in open('/proc/meminfo') if l.startswith(('MemAvailable:','SwapFree:'))}
 assert mem['MemAvailable']>=40*1024**3,'Need >=40 GiB MemAvailable for 32 GiB CPU build; coordinate service window'
 pins=read('release/sources.json')
 for pin in list(pins['archives'].values())+[pins['cmake']]:verify(a.inputs/pin['file'],pin['sha256'])
 a.work.mkdir(parents=True,exist_ok=True);ctx=a.work/'context';ctx.mkdir(exist_ok=True)
 shutil.copytree(a.inputs,ctx/'inputs',dirs_exist_ok=True)
 write(ctx/'inputs/pins.json',pins)
 for directory in ['build','tools','patches','release']:
  shutil.copytree(ROOT/directory,ctx/'recipe'/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
 shutil.copyfile(ROOT/'build/Dockerfile',ctx/'Dockerfile')
 # Legacy builder enforces memory; BuildKit does not honor this resource option consistently.
 env={**os.environ,'DOCKER_BUILDKIT':'0'}
 run(['docker','pull',pins['base']])
 run(['docker','build','--network=none','--memory=32g','--memory-swap=32g','--build-arg','SOURCES_SHA256='+digest(ROOT/'release/sources.json'),'--tag',a.tag,ctx],env=env)
 image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',a.tag],text=True).strip()
 write(a.work/'image.json',dict(image_id=image,tag=a.tag,sources=pins,scope='Fresh source build; native/runtime validation still required'))
 print('PASS image',image)
if __name__=='__main__':main()
