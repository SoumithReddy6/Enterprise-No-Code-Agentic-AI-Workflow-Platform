"""Paragraph-sized chunks with conservative structural context; no corpus-specific rules."""
from collections import Counter
import re


def heading(line):
    markdown=re.match(r'^(#{1,6})\s+(.+)$',line)
    if markdown:return len(markdown[1]),markdown[2]
    if re.match(r'^§\s*\d+[.\d]*\s+\S',line):return 1,line
    numbered=re.match(r'^(\d+(?:\.\d+){1,5})\.?\s+([A-Z][^.!?]{2,85})$',line)
    if numbered:return numbered[1].count('.')+1,line
    # Plain-text/PDF heading lines: short title case, not a wrapped sentence.
    words=line.split()
    if 1<len(words)<=8 and len(line)<85 and not re.search(r'[.!?:;•()]',line):
        significant=[w for w in words if w.lower() not in {'and','of','the','for','to','a','an','in','or','with'}]
        if significant and all(w[0].isupper() for w in significant) and not any(w in {'SHALL','MUST','NOT','SHOULD','MAY'} for w in words):return 2,line
    return None


def section_chunks(pages,size,overlap):
    # Repeated running headers/footers are not section headings. Keep source page numbers.
    margins=Counter()
    for page in pages:
        lines=[v.strip() for v in page.splitlines() if v.strip()]
        margins.update(set(lines[:4]+lines[-2:]))
    repeated={line for line,count in margins.items() if count>=max(3,len(pages)//2)}
    stack=[];regulatory={};buffer='';buffer_path=[];buffer_page=1
    result=[]
    def flush():
        nonlocal buffer
        if buffer.strip():result.append({'page':buffer_page,'text':buffer.strip(),'heading_path':buffer_path[:]})
        buffer=''
    def path():return [v for _,v in stack]+[regulatory[k] for k in sorted(regulatory)]
    def append(unit,page):
        nonlocal buffer,buffer_path,buffer_page
        unit=re.sub(r'\s+',' ',unit).strip()
        if not unit:return
        current=path()
        regulatory_siblings=bool(stack and stack[0][1].startswith('§') and buffer_path and current and buffer_path[0]==current[0])
        if buffer and (len(buffer)+1+len(unit)>size or (buffer_path!=current and not regulatory_siblings)):flush()
        # Oversized prose alone uses bounded overlap; list items under size stay atomic.
        if len(unit)>size:
            flush()
            for start in range(0,len(unit),size-overlap):
                part=unit[start:start+size].strip()
                if part:result.append({'page':page,'text':part,'heading_path':current[:]})
            return
        if not buffer:buffer_path=current;buffer_page=page
        elif regulatory_siblings:
            common=[]
            for a,b in zip(buffer_path,current):
                if a!=b:break
                common.append(a)
            buffer_path=common
        buffer=(buffer+'\n'+unit).strip()
    for page,value in enumerate(pages,1):
        flush();paragraph=[]
        def emit():
            if paragraph:append(' '.join(paragraph),page);paragraph.clear()
        value=re.sub(r'(?<=\w)-\s*\n\s*(?=[a-z])','',value)
        for raw in value.splitlines():
            line=raw.strip()
            if line in repeated or re.fullmatch(r'\d+',line) or re.match(r'^Page \d+ of \d+\b',line):continue
            if not line:emit();continue
            h=heading(line)
            if h:
                emit();flush();level,title=h
                stack=[(n,t) for n,t in stack if n<level]+[(level,title[:240])]
                stack=stack[-6:];regulatory={}
                continue
            # PDF inline headings are noun phrases ending before a new sentence.
            inline=re.match(r'^([A-Z][A-Za-z /-]{3,70})\.\s+(?=[A-Z])',line)
            if inline and 2<=len(inline[1].split())<=8 and not set(inline[1].lower().split()) & {'is','are','must','shall','may','can','should','will','was','were','has','have','do','does','not','be'}:
                emit();flush()
                stack=[(n,t) for n,t in stack if n<3]+[(3,inline[1])]
                regulatory={};line=line[inline.end():]
            marker=re.match(r'^\(([a-z]|\d+|[ivx]+|[A-Z])\)\s*(.*)',line)
            if marker and stack and stack[0][1].startswith('§'):
                emit();label,body=marker.groups()
                # Flattened text loses indentation. Prefer an alphabetic
                # continuation (h -> i, u -> v, w -> x) over inheriting an
                # uncertain numeric parent's scope. This can omit ancestry,
                # but avoids asserting the preceding sibling's subject.
                outer=re.match(r'^\(([a-z])\)',regulatory.get(1,''))
                alphabetic_continuation=bool(outer and len(label)==1 and ord(label)==ord(outer[1])+1)
                if label.isdigit():level=2
                elif label.isupper():level=4
                elif len(label)>1 or (label in {'i','v','x'} and 2 in regulatory and not alphabetic_continuation):level=3
                else:level=1
                regulatory={k:v for k,v in regulatory.items() if k<level}
                title=body.split('. ',1)[0]
                regulatory[level]=f'({label}) '+(title if len(title)<90 else '')
            elif re.match(r'^(?:[•●▪*-]\s|\d+\.\s)',line):emit()
            paragraph.append(line)
        emit();flush()
    return result


def contextual_text(chunk):
    headings=chunk.get('heading_path') or []
    return (' > '.join(headings)+'\n' if headings else '')+chunk['text']
