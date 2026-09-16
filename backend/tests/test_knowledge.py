import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.knowledge import Knowledge

@pytest.fixture
def knowledge(tmp_path):
    return Knowledge(Store(f'sqlite:///{tmp_path}/test.db', Fernet.generate_key()))

def ready(k, base, text, tenant='a'):
    d=k.upload(base,'facts.pdf',b'%PDF-1.4 test',tenant)
    ident, owner=k.claim()
    k.publish(ident,owner,[text])
    return d

def test_tenant_sources_and_removal(knowledge):
    k=knowledge;b=k.create('Facts','a')['id'];d=ready(k,b,'Mercury has a crater named Caloris.')
    assert k.bases('b')==[]
    for call in (lambda:k.documents(b,'b'),lambda:k.download(d['id'],'b'),lambda:k.retrieve(b,'Caloris',4,'b')):
        with pytest.raises(KeyError):call()
    sources=k.retrieve(b,'Caloris',4,'a')
    assert sources[0]['page']==1
    assert k.verify_sources(sources,'a')==sources
    with pytest.raises(ValueError):k.verify_sources([{**sources[0],'text':'forged'}],'a')
    k.remove(d['id'],'a')
    with pytest.raises((KeyError,ValueError)):k.verify_sources(sources,'a')
    assert k.retrieve(b,'Caloris',4,'a')==[]

def test_fenced_atomic_publication_and_ranking(knowledge):
    k=knowledge;b=k.create('Facts','a')['id'];d=k.upload(b,'x.pdf',b'%PDF-x','a');i,o=k.claim()
    assert k.retrieve(b,'Caloris',4,'a')==[]
    assert not k.publish(i,'wrong',['Caloris'])
    assert k.publish(i,o,['Venus is hot.','Mercury contains Caloris basin.'])
    assert k.retrieve(b,'Caloris',4,'a')[0]['page']==2
    assert k.retrieve(b,'nonexistent',4,'a')==[]
    assert k.documents(b,'a')[0]['status']=='ready'

def test_limits_and_removal_fence(knowledge,monkeypatch):
    k=knowledge;b=k.create('Facts','a')['id']
    monkeypatch.setattr(k,'max_documents',1)
    d=k.upload(b,'x.pdf',b'%PDF-x','a')
    with pytest.raises(ValueError):k.upload(b,'y.pdf',b'%PDF-y','a')
    i,o=k.claim();k.remove(i,'a');assert not k.publish(i,o,['text'])
    with pytest.raises(ValueError):k.upload(b,'bad.pdf',b'not pdf','a')

@pytest.mark.asyncio
async def test_extraction_failure_retry(knowledge):
    k=knowledge;b=k.create('Facts','a')['id'];d=k.upload(b,'bad.pdf',b'%PDF-broken','a')
    assert await k.process_once()
    row=k.documents(b,'a')[0];assert row['status']=='failed' and row['error']
    assert k.retrieve(b,'anything',4,'a')==[]
    k.retry(d['id'],'a');assert k.documents(b,'a')[0]['status']=='queued'

def test_expired_lease_is_reclaimed_and_old_owner_cannot_publish(knowledge):
    from sqlalchemy.orm import Session
    from backend.app.knowledge import Document
    k=knowledge;b=k.create('Facts','a')['id'];k.upload(b,'x.pdf',b'%PDF-x','a');i,old=k.claim()
    assert k.claim() is None
    with Session(k.store.engine) as s:
        s.get(Document,i).lease_until=0;s.commit()
    new_id,new_owner=k.claim();assert new_id==i and new_owner!=old
    assert not k.publish(i,old,['stale text'])
    assert k.publish(i,new_owner,['current text'])

def test_chunk_and_page_limits_publish_nothing(knowledge,monkeypatch):
    k=knowledge;b=k.create('Facts','a')['id'];k.upload(b,'x.pdf',b'%PDF-x','a');i,o=k.claim()
    monkeypatch.setattr(k,'max_chunks',1)
    with pytest.raises(ValueError):k.publish(i,o,['a'*2001])
    assert k.retrieve(b,'a',4,'a')==[]
    with pytest.raises(ValueError):k.publish(i,o,['',''])
    with pytest.raises(ValueError):k.publish(i,o,['a']*201)
    assert k.publish(i,o,['one searchable chunk'])
    k.upload(b,'y.pdf',b'%PDF-y','a');i,o=k.claim()
    with pytest.raises(ValueError):k.publish(i,o,['another chunk'])
    assert len(k.retrieve(b,'chunk',4,'a'))==1

def test_concurrent_upload_quota(knowledge,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    k=knowledge;b=k.create('Facts','a')['id'];monkeypatch.setattr(k,'max_documents',1)
    def upload(_):
        try:k.upload(b,'x.pdf',b'%PDF-x','a');return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(upload,range(2)))==[False,True]

def text_pdf():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(612,792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 18 Tf 50 700 Td (Mercury contains the large Caloris basin.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out);return out.getvalue()

@pytest.mark.asyncio
async def test_real_extraction_text_and_encrypted(knowledge):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    k=knowledge;b=k.create('Facts','a')['id'];k.upload(b,'text.pdf',text_pdf(),'a')
    await k.process_once();assert k.documents(b,'a')[0]['status']=='ready'
    assert k.retrieve(b,'Caloris',4,'a')[0]['page']==1
    writer=PdfWriter(clone_from=PdfReader(BytesIO(text_pdf())));writer.encrypt('secret');out=BytesIO();writer.write(out)
    k.upload(b,'encrypted.pdf',out.getvalue(),'a');await k.process_once()
    assert 'Password-protected' in k.documents(b,'a')[1]['error']

def test_api_upload_download_and_tenant(knowledge):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.knowledge_api import install_knowledge_routes
    app=FastAPI();tenant=['a'];install_knowledge_routes(app,knowledge,lambda:tenant[0]);client=TestClient(app)
    b=client.post('/api/knowledge',json={'name':'PDF facts'}).json()['id']
    result=client.post(f'/api/knowledge/{b}/documents?filename=hello.pdf',content=text_pdf(),headers={'content-type':'application/pdf'})
    assert result.status_code==202;d=result.json()['id']
    assert client.get(f'/api/documents/{d}/file').content==text_pdf()
    tenant[0]='b';assert client.get(f'/api/documents/{d}/file').status_code==404
    assert client.get(f'/api/knowledge/{b}/documents').status_code==404
    tenant[0]='a';knowledge.max_bytes=10
    assert client.post(f'/api/knowledge/{b}/documents',content=b'%PDF-'+b'x'*10,headers={'content-type':'application/pdf'}).status_code==413

@pytest.mark.asyncio
async def test_blank_page_preserves_page_number(knowledge):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    writer=PdfWriter();writer.add_blank_page(612,792);writer.add_page(PdfReader(BytesIO(text_pdf())).pages[0]);out=BytesIO();writer.write(out)
    k=knowledge;b=k.create('Facts','a')['id'];k.upload(b,'blank.pdf',out.getvalue(),'a');await k.process_once()
    assert k.documents(b,'a')[0]['status']=='ready'
    assert k.documents(b,'a')[0]['pages']==2
    assert k.retrieve(b,'Caloris',4,'a')[0]['page']==2

@pytest.mark.asyncio
async def test_real_scan_and_mixed_pdf(knowledge,tmp_path):
    import shutil,subprocess
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
    if not shutil.which('pdftoppm') or not shutil.which('tesseract'):pytest.skip('Local OCR tools not installed')
    src=tmp_path/'source.pdf';src.write_bytes(text_pdf());prefix=tmp_path/'scan'
    subprocess.run(['pdftoppm','-r','110','-singlefile',str(src),str(prefix)],check=True,capture_output=True,timeout=30)
    data=(tmp_path/'scan.ppm').read_bytes();header,width_height,maxval,pixels=data.split(b'\n',3)
    assert header==b'P6' and maxval==b'255';width,height=map(int,width_height.split())
    writer=PdfWriter();page=writer.add_blank_page(612,792)
    img=DecodedStreamObject();img.set_data(pixels)
    img.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(width),NameObject('/Height'):NumberObject(height),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Scan'):writer._add_object(img)})})
    draw=DecodedStreamObject();draw.set_data(b'q 612 0 0 792 0 0 cm /Scan Do Q');page[NameObject('/Contents')]=writer._add_object(draw)
    writer.add_page(PdfReader(BytesIO(text_pdf())).pages[0]);out=BytesIO();writer.write(out)
    k=knowledge;b=k.create('Mixed','a')['id'];k.upload(b,'mixed.pdf',out.getvalue(),'a');await k.process_once()
    row=k.documents(b,'a')[0];assert row['status']=='ready',row['error']
    assert {r['page'] for r in k.retrieve(b,'Caloris',4,'a')}=={1,2}

@pytest.mark.asyncio
async def test_timeout_kills_extractor(knowledge,monkeypatch):
    import asyncio,os,sys
    k=knowledge;b=k.create('Timeout','a')['id'];k.upload(b,'x.pdf',text_pdf(),'a')
    original=asyncio.create_subprocess_exec;processes=[]
    async def sleeping(*args,**kwargs):
        proc=await original(sys.executable,'-c','import time;time.sleep(60)',**kwargs);processes.append(proc);return proc
    monkeypatch.setattr(asyncio,'create_subprocess_exec',sleeping);k.extraction_timeout=.02;k.lease_seconds=.09
    await k.process_once()
    assert 'timed out' in k.documents(b,'a')[0]['error']
    assert processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):os.kill(processes[0].pid,0)

@pytest.mark.asyncio
async def test_cancel_kills_extractor_and_leaves_recoverable_lease(knowledge,monkeypatch):
    import asyncio,sys
    k=knowledge;b=k.create('Cancel','a')['id'];k.upload(b,'x.pdf',text_pdf(),'a')
    original=asyncio.create_subprocess_exec;started=asyncio.Event();processes=[]
    async def sleeping(*args,**kwargs):
        proc=await original(sys.executable,'-c','import time;time.sleep(60)',**kwargs);processes.append(proc);started.set();return proc
    monkeypatch.setattr(asyncio,'create_subprocess_exec',sleeping)
    task=asyncio.create_task(k.process_once());await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert processes[0].returncode is not None
    assert k.documents(b,'a')[0]['status']=='processing'
    assert k.retrieve(b,'Caloris',4,'a')==[]

def test_metadata_and_retrieval_do_not_load_pdf_bytes(knowledge):
    from sqlalchemy import event
    k=knowledge;b=k.create('Bytes','a')['id'];ready(k,b,'Caloris basin is on Mercury.')
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(k.store.engine,'before_cursor_execute',capture)
    try:
        k.documents(b,'a');k.retrieve(b,'Caloris',4,'a')
    finally:event.remove(k.store.engine,'before_cursor_execute',capture)
    assert not any('knowledge_documents.content' in query for query in statements)

@pytest.mark.asyncio
async def test_rendered_blank_page_preserves_numbering(knowledge):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(612,792)
    stream=DecodedStreamObject();stream.set_data(b'q 1 0 0 1 0 0 cm Q')
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.add_page(PdfReader(BytesIO(text_pdf())).pages[0]);out=BytesIO();writer.write(out)
    k=knowledge;b=k.create('Rendered blank','a')['id'];k.upload(b,'blank.pdf',out.getvalue(),'a');await k.process_once()
    row=k.documents(b,'a')[0];assert row['status']=='ready',row['error']
    assert row['pages']==2 and k.retrieve(b,'Caloris',4,'a')[0]['page']==2

@pytest.mark.asyncio
@pytest.mark.parametrize('drawing,expected',[(b'q 1 0 0 1 0 0 cm Q','no readable text'),(b'0 g 40 40 300 300 re f','OCR could not read')])
async def test_all_blank_or_unreadable_page_fails(knowledge,drawing,expected):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(612,792);stream=DecodedStreamObject();stream.set_data(drawing)
    page[NameObject('/Contents')]=writer._add_object(stream);out=BytesIO();writer.write(out)
    k=knowledge;b=k.create('Unreadable','a')['id'];k.upload(b,'unreadable.pdf',out.getvalue(),'a');await k.process_once()
    row=k.documents(b,'a')[0];assert row['status']=='failed' and expected in row['error']

def test_blank_pgm_parser_is_conservative(tmp_path):
    from backend.app.pdf_extract import is_blank_pgm
    file=tmp_path/'page.pgm'
    file.write_bytes(b'P5\n# comment\n2 1\n255\n\xff\xff');assert is_blank_pgm(file)
    file.write_bytes(b'P5\n2 1\n255\n\xff\xfe');assert not is_blank_pgm(file)
    file.write_bytes(b'P5\n2 1\n255\n\xff');assert not is_blank_pgm(file)
