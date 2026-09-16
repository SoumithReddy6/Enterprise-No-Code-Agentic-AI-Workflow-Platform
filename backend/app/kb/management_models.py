"""Service-owned metadata; deliberately independent of Relay's SQL Base."""
from sqlalchemy import Column, JSON, LargeBinary, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class KnowledgeBase(Base):
    __tablename__ = 'kb_management_bases'
    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    state = Column(JSON, nullable=False)

class Original(Base):
    __tablename__ = 'kb_management_originals'
    id = Column(String(64), primary_key=True)
    kb_id = Column(String(64), nullable=False, index=True)
    data = Column(LargeBinary, nullable=False)
