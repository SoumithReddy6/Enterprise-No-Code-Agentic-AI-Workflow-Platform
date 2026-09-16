"""Bounded document text extraction in a separate process."""
import json,sys,zipfile,io
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree

EXTENSIONS={'.pdf','.txt','.md','.markdown','.csv','.json','.html','.htm','.docx'}
class HTMLText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag in ('p','br','div','h1','h2','li'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)

def extract(path):
    extension=path.suffix.lower()
    if extension=='.pdf':
        from .pdf_extract import extract as pdf_extract
        return pdf_extract(str(path))
    if extension=='.docx':
        with zipfile.ZipFile(path) as archive:
            item=archive.getinfo('word/document.xml')
            if item.file_size>16*1024*1024:raise ValueError('DOCX text exceeds 16 MB limit')
            root=ElementTree.fromstring(archive.read(item))
            text='\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
    else:
        text=path.read_text(encoding='utf-8-sig')
        if '\x00' in text:raise ValueError('Binary content is not supported')
        if extension=='.json':text=json.dumps(json.loads(text),ensure_ascii=False,indent=2)
        if extension in ('.html','.htm'):
            parser=HTMLText();parser.feed(text);text=''.join(parser.parts)
    if len(text)>16*1024*1024:raise ValueError('Extracted text exceeds 16 MB limit')
    return [text]
if __name__=='__main__':
    try:result={'pages':extract(Path(sys.argv[1]))}
    except Exception as exc:result={'error':str(exc)[:300]}
    Path(sys.argv[2]).write_text(json.dumps(result))
