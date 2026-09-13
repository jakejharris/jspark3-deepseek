"""Bounded exact byte-layout conversion of existing L4 hash-head partitions.

No model dequantization or requantization. Run in a CPU-only constrained process.
"""
import argparse,hashlib,json,os,struct,time
from pathlib import Path
import numpy as np
HEADER=4096
FORMAT='JSPARK_ENGRAM_PACKED_V1'

def tensor_info(root,index,name):
 p=root/index['weight_map'][name]
 with p.open('rb') as src:
  size=struct.unpack('<Q',src.read(8))[0];meta=json.loads(src.read(size))[name]
 return p,8+size+meta['data_offsets'][0],meta

def exact_read(fd,n,offset):
 data=os.pread(fd,n,offset)
 if len(data)!=n:raise IOError(f'Short source read {len(data)} != {n}')
 return data

def pack(root,dest,layer,lo,hi,chunk_rows):
 index=json.loads((root/'model.safetensors.index.json').read_text())
 wp,woff,wm=tensor_info(root,index,f'layers.{layer}.engram.embed.weight')
 sp,soff,sm=tensor_info(root,index,f'layers.{layer}.engram.embed.scale')
 assert wm['shape'][1]==256 and sm['shape'][1]==8 and wm['shape'][0]==sm['shape'][0]
 assert 0<=lo<hi<=wm['shape'][0]
 dest.mkdir(parents=True,exist_ok=True);final=dest/f'layer{layer}.packed';temp=dest/f'layer{layer}.incomplete'
 assert not final.exists() and not temp.exists(), 'Never overwrite a completed/partial packing receipt'
 header={'format':FORMAT,'complete':False,'layer':layer,'row_start':lo,'row_end':hi,
         'weight_bytes':256,'scale_bytes':8,'row_bytes':264,'header_bytes':HEADER,
         'source_weight':str(wp),'source_scale':str(sp),'source_weight_meta':wm,'source_scale_meta':sm,
         'source_weight_offset':woff,'source_scale_offset':soff}
 def header_bytes(obj):
  raw=json.dumps(obj,sort_keys=True).encode();assert len(raw)<HEADER;return raw+b'\0'*(HEADER-len(raw))
 wfd=os.open(wp,os.O_RDONLY);sfd=os.open(sp,os.O_RDONLY)
 wh=hashlib.sha256();sh=hashlib.sha256();ph=hashlib.sha256();start=time.monotonic()
 try:
  with temp.open('xb') as out:
   out.write(header_bytes(header))
   for first in range(lo,hi,chunk_rows):
    count=min(chunk_rows,hi-first)
    w=exact_read(wfd,count*256,woff+first*256);s=exact_read(sfd,count*8,soff+first*8)
    wh.update(w);sh.update(s)
    packed=np.empty((count,264),dtype=np.uint8)
    packed[:,:256]=np.frombuffer(w,dtype=np.uint8).reshape(count,256)
    packed[:,256:]=np.frombuffer(s,dtype=np.uint8).reshape(count,8)
    raw=packed.tobytes();out.write(raw);ph.update(raw)
    # Packing is outside scoring. Evict only completed sequential source ranges.
    os.posix_fadvise(wfd,woff+first*256,count*256,os.POSIX_FADV_DONTNEED)
    os.posix_fadvise(sfd,soff+first*8,count*8,os.POSIX_FADV_DONTNEED)
   out.flush();os.fsync(out.fileno())
  # Independently read/unpack every written byte, verifying both source streams.
  verify_w=hashlib.sha256();verify_s=hashlib.sha256();verify_p=hashlib.sha256()
  with temp.open('rb') as src:
   src.seek(HEADER)
   for first in range(lo,hi,chunk_rows):
    count=min(chunk_rows,hi-first);raw=src.read(count*264);assert len(raw)==count*264
    block=np.frombuffer(raw,dtype=np.uint8).reshape(count,264)
    verify_w.update(block[:,:256].tobytes());verify_s.update(block[:,256:].tobytes());verify_p.update(raw)
   assert src.read(1)==b''
  assert verify_w.digest()==wh.digest() and verify_s.digest()==sh.digest() and verify_p.digest()==ph.digest()
  header.update(complete=True,weight_sha256=wh.hexdigest(),scale_sha256=sh.hexdigest(),payload_sha256=ph.hexdigest(),elapsed_s=time.monotonic()-start)
  with temp.open('r+b') as out:out.write(header_bytes(header));out.flush();os.fsync(out.fileno())
  os.rename(temp,final)
  (dest/f'layer{layer}.json').write_text(json.dumps(header,indent=2)+'\n')
  print(json.dumps({'layer':layer,'file':str(final),'elapsed_s':header['elapsed_s'],'row_start':lo,'row_end':hi,'payload_sha256':ph.hexdigest()}),flush=True)
 finally:os.close(wfd);os.close(sfd)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--chunk-rows',type=int,default=32768);p.add_argument('--layer',type=int,choices=[1,14],help='Only pack this missing layer during explicit recovery');a=p.parse_args()
 assert 1024<=a.chunk_rows<=65536
 ranges=json.loads((a.source/'engram-local.json').read_text())['layers'];assert set(ranges)=={'1','14'}
 if a.layer is not None:ranges={str(a.layer):ranges[str(a.layer)]}
 needed=sum((hi-lo)*264+HEADER for lo,hi in ranges.values())
 stat=os.statvfs(a.destination if a.destination.exists() else a.destination.parent);assert stat.f_bavail*stat.f_frsize>needed+100*1024**3
 for key,(lo,hi) in sorted(ranges.items(),key=lambda x:int(x[0])):pack(a.source,a.destination,int(key),lo,hi,a.chunk_rows)
