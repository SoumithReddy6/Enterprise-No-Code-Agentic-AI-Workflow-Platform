"""Search-owned tables; deliberately independent of management and legacy Base."""
from sqlalchemy import Column, String, Integer, Float, Text, JSON, LargeBinary, Index, Boolean
from sqlalchemy.orm import declarative_base

SQLBase = declarative_base()


class Segment(SQLBase):
    __tablename__ = 'kb_search_segments'
    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    kb_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    state = Column(String(24), nullable=False)
    build_hash = Column(String(64), nullable=False, default='')
    config = Column(JSON, nullable=False, default=dict)
    connection = Column(LargeBinary, nullable=False, default=b'')
    dimensions = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    retain_provenance = Column(Boolean, nullable=False, default=False)
    provenance_revoked = Column(Boolean, nullable=False, default=False)
    cleanup_attempts = Column(Integer, nullable=False, default=0)
    cleanup_error = Column(Text, nullable=False, default='')
    created_at = Column(Float, nullable=False)


class Chunk(SQLBase):
    __tablename__ = 'kb_search_chunks'
    id = Column(String(64), primary_key=True)
    segment_id = Column(String(64), nullable=False, index=True)
    document_id = Column(String(64), nullable=False, index=True)
    filename = Column(String(240), nullable=False)
    content_hash = Column(String(64), nullable=False)
    ordinal = Column(Integer, nullable=False)
    page = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    heading_path = Column(JSON, nullable=False, default=list)
    token_count = Column(Integer, nullable=False)


class Posting(SQLBase):
    __tablename__ = 'kb_search_postings'
    segment_id = Column(String(64), primary_key=True)
    term = Column(String(128), primary_key=True)
    chunk_id = Column(String(64), primary_key=True)
    frequency = Column(Integer, nullable=False)
    __table_args__ = (Index('ix_kb_posting_chunk', 'chunk_id'),)


class ChunkOwner(SQLBase):
    """Permanent ownership prevents delayed remote writes clobbering reused IDs."""
    __tablename__ = 'kb_search_chunk_owners'
    id = Column(String(64), primary_key=True)
    segment_id = Column(String(64), nullable=False, index=True)
