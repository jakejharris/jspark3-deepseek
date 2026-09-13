"""Small shared primitives. No machine-specific inputs."""
import hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(name):return json.loads((ROOT/name).read_text())
def digest(path,offset=0):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  f.seek(offset)
  for b in iter(lambda:f.read(8<<20),b''):h.update(b)
 return h.hexdigest()
def write(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');os.replace(tmp,path)
def run(argv,**kw):return subprocess.run([str(x) for x in argv],check=True,**kw)
def verify(path,want):
 if digest(path)!=want:raise RuntimeError('SHA256 mismatch: '+str(path))
