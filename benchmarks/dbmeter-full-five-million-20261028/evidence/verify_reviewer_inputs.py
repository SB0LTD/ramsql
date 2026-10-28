import hashlib,json,pathlib,subprocess
root=pathlib.Path('../dbMeter').resolve()
names=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().strip('\0').split('\0')
requests=''.join('HEAD:'+n+'\n' for n in names).encode()
data=subprocess.check_output(['git','-C',str(root),'cat-file','--batch'],input=requests)
at=0;files=[];changed=[]
for name in names:
 end=data.index(b'\n',at);header=data[at:end].decode().split();size=int(header[2]);at=end+1
 expected=data[at:at+size].replace(b'\r\n',b'\n');at+=size+1
 actual=(root/name).read_bytes().replace(b'\r\n',b'\n')
 if actual!=expected:changed.append(name)
 files.append(dict(path=name,sha256_normalized_lf=hashlib.sha256(actual).hexdigest()))
assert at==len(data)
report=dict(schema=1,revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),normalization='CRLF to LF only, matching the established benchmark harness',tracked_file_count=len(files),changed_files=changed,files=files)
dest=pathlib.Path('benchmarks/dbmeter-full-five-million-20261028/evidence/reviewer-input-integrity.json')
dest.write_text(json.dumps(report,indent=2)+'\n')
assert not changed,changed
print('All',len(files),'tracked reviewer inputs match HEAD after LF normalization')
