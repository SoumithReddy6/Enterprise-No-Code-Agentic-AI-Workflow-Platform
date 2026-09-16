"""Isolated PDF extractor. Invoked only with server-generated temporary paths."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def is_blank_pgm(path):
    """Accept only a valid all-white 8-bit PGM raster as a blank page."""
    data=Path(path).read_bytes();position=0;header=[]
    for _ in range(4):
        while position<len(data):
            if data[position:position+1] in b' \t\r\n':position+=1
            elif data[position:position+1]==b'#':
                end=data.find(b'\n',position)
                if end<0:return False
                position=end+1
            else:break
        start=position
        while position<len(data) and data[position:position+1] not in b' \t\r\n':position+=1
        header.append(data[start:position])
    try:
        magic,width,height,maximum=header
        width,height=int(width),int(height)
    except ValueError:return False
    if magic!=b'P5' or maximum!=b'255' or width<=0 or height<=0:return False
    if data[position:position+2]==b'\r\n':position+=2
    elif data[position:position+1] in (b' ',b'\t',b'\r',b'\n'):position+=1
    else:return False
    pixels=data[position:]
    return len(pixels)==width*height and pixels.count(b'\xff')==len(pixels)


def extract(path):
    from pypdf import PdfReader
    try:reader=PdfReader(path,strict=False)
    except Exception as exc:raise ValueError('PDF is corrupt or unsupported') from exc
    if reader.is_encrypted:raise ValueError('Password-protected PDFs are not supported; upload an unlocked copy')
    if not 0<len(reader.pages)<=200:raise ValueError('PDF must contain 1–200 pages')
    pages=[];total=0
    for number,page in enumerate(reader.pages,1):
        if not page.get('/Contents') and not page.get('/Annots'):
            pages.append('');continue
        try:value=page.extract_text() or ''
        except Exception as exc:raise ValueError(f'Could not extract page {number}') from exc
        if len(''.join(value.split()))<20:
            if not shutil.which('pdftoppm'):raise ValueError(f'Page {number} requires OCR; install Poppler and Tesseract with English language data')
            with tempfile.TemporaryDirectory(prefix='relay-ocr-') as tmp:
                prefix=str(Path(tmp)/'page')
                subprocess.run(['pdftoppm','-f',str(number),'-l',str(number),'-singlefile','-scale-to','2200','-gray',str(path),prefix],check=True,timeout=45,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                raster=prefix+'.pgm'
                if is_blank_pgm(raster):
                    pages.append('');continue
                if not shutil.which('tesseract'):raise ValueError(f'Page {number} requires OCR; install Tesseract with English language data')
                result=subprocess.run(['tesseract',raster,'stdout','-l','eng'],check=True,timeout=45,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                value=result.stdout.decode('utf-8',errors='replace')
        if not value.strip():raise ValueError(f'Page {number} has no readable text; OCR could not read it')
        total+=len(value)
        if total>50_000_000:raise ValueError('Extracted PDF text is too large')
        pages.append(value.strip())
    return pages

if __name__=='__main__':
    # Bound memory/CPU in addition to the supervisor wall-clock/process-group bound.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(280,280))
        if sys.platform!='darwin':resource.setrlimit(resource.RLIMIT_AS,(1536*1024*1024,1536*1024*1024))
    except (ImportError,ValueError,OSError):pass
    try:result={'pages':extract(sys.argv[1])}
    except Exception as exc:result={'error':str(exc)[:500] or 'PDF extraction failed'}
    Path(sys.argv[2]).write_text(json.dumps(result))
