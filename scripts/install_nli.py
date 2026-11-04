"""Explicit download of a revision-pinned, official CPU NLI model (never run by evaluator)."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = 'cross-encoder/nli-MiniLM2-L6-H768'
REVISION = 'b95119ce93d3e065de6214e38cd4a97b0f2f2c6d'
FILES = {'model.onnx':'onnx/model_qint8_arm64.onnx','tokenizer.json':'tokenizer.json','config.json':'config.json'}
SHA256 = {'model.onnx':'449024a8711a3e655cad410cb2192158f9897fc2a32b65dbb817922351150db7', 'tokenizer.json':'82139106e603ee4e1d5bc99d056ccbed5a92bc24848b1b5a7137c26e00d0dbf6', 'config.json':'8b0e41caff7567c0f53e6983f35591c3dec59507c9173ab125c5823394fb57f3'}
LABELS = {'0':'contradiction','1':'entailment','2':'neutral'}

def install(destination):
    destination.mkdir(parents=True,exist_ok=True)
    hashes={}
    for local,remote in FILES.items():
        target=destination/local
        temp=destination/(local+'.download')
        print(f'Downloading {MODEL}@{REVISION}/{remote}',flush=True)
        with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{remote}',timeout=120) as response, temp.open('wb') as stream:
            while chunk:=response.read(1024*1024):stream.write(chunk)
        temp.replace(target)
        hashes[local]=hashlib.sha256(target.read_bytes()).hexdigest()
    if hashes!=SHA256:raise ValueError('Downloaded files do not match pinned checksums')
    if json.loads((destination/'config.json').read_text())['id2label']!=LABELS:
        raise ValueError('Unexpected model label mapping')
    manifest={'model':MODEL,'revision':REVISION,'files':FILES,'sha256':hashes,'id2label':LABELS}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir',type=Path,required=True)
    print(json.dumps(install(parser.parse_args().model_dir),indent=2))
