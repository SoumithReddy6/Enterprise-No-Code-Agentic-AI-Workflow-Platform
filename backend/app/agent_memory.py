"""Bounded workspace conversation memory selected explicitly by Memory agents."""
import json
from sqlalchemy import Column,String,JSON,select,text
from sqlalchemy.orm import Session
from .storage import Base

class AgentMemory(Base):
    __tablename__='agent_memories'
    tenant_id=Column(String(64),primary_key=True)
    key=Column(String(120),primary_key=True)
    messages=Column(JSON,nullable=False)
TENANT_MODELS=(AgentMemory,)

class MemoryService:
    def __init__(self,store):self.store=store
    def read(self,key,tenant):
        with Session(self.store.engine) as s:
            row=s.get(AgentMemory,(tenant,key));return json.dumps(row.messages if row else [],ensure_ascii=False)
    def write(self,key,input_text,answer,tenant):
        with Session(self.store.engine) as s:
            if self.store.engine.dialect.name=='sqlite':s.execute(text('BEGIN IMMEDIATE'))
            else:s.execute(text('SELECT pg_advisory_xact_lock(19482028)'))
            row=s.get(AgentMemory,(tenant,key))
            if row is None:row=AgentMemory(tenant_id=tenant,key=key,messages=[]);s.add(row)
            entries=[*row.messages,{'input':input_text[:2000],'answer':answer[:2000]}][-10:]
            while len(json.dumps(entries,ensure_ascii=False))>16000:entries=entries[1:]
            row.messages=entries;s.commit()
