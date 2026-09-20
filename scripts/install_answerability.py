"""Explicit download for the pinned offline SQuAD2 extractive reader."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from backend.app.kb.answerability import MODEL,REVISION,FILES,SHA256

def install(root):
    root.mkdir(parents=True,exist_ok=True)
    for local,remote in FILES.items():
        target=root/local
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==SHA256[local]:continue
        temp=root/(local+'.download')
        print(f'Downloading {MODEL}@{REVISION}/{remote}',flush=True)
        with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{remote}',timeout=120) as source,temp.open('wb') as output:
            while chunk:=source.read(1024*1024):output.write(chunk)
        if hashlib.sha256(temp.read_bytes()).hexdigest()!=SHA256[local]:raise ValueError(f'Checksum mismatch: {local}')
        temp.replace(target)
    manifest={'model':MODEL,'revision':REVISION,'files':FILES,'sha256':SHA256,'output_mapping':{'start_logits':'start token logits','end_logits':'end token logits','cls_token_id':0}}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2));return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--model-dir',type=Path,required=True)
    print(json.dumps(install(parser.parse_args().model_dir),indent=2))
