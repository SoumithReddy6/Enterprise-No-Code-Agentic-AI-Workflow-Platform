"""Explicitly download pinned local reranking assets; inference never downloads files."""
import hashlib
import json
import os
from pathlib import Path
import httpx

REPOSITORY='Xenova/ms-marco-MiniLM-L-6-v2'
REVISION='a09144355adeed5f58c8ed011d209bf8ee5a1fec'

def main():
    root=Path(os.environ.get('KB_RERANKER_DIR',str(Path(os.environ.get('DATA_DIR','.data'))/'reranker')))
    root.mkdir(parents=True,exist_ok=True);files={}
    for name in ('tokenizer.json','onnx/model_quantized.onnx'):
        path=root/Path(name).name;temporary=path.with_suffix(path.suffix+'.download')
        try:
            with httpx.stream('GET',f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}',follow_redirects=True,timeout=120) as response:
                response.raise_for_status()
                with temporary.open('wb') as output:
                    for data in response.iter_bytes():output.write(data)
            files[path.name]=hashlib.sha256(temporary.read_bytes()).hexdigest();temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
    (root/'manifest.json').write_text(json.dumps({'repository':REPOSITORY,'revision':REVISION,'sha256':files},indent=2))
    print(f'Installed {REPOSITORY}@{REVISION} in {root}')

if __name__=='__main__':main()
