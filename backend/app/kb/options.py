"""Retrieval options and knowledge-base settings shared by the gateway, services and nodes."""
from typing import Literal
from pydantic import Field, field_validator
from ..models import StrictModel

class RetrievalOptions(StrictModel):
    mode:Literal['similarity','keyword','hybrid','rrf']='similarity'
    top_k:int=Field(default=4,ge=1,le=20)
    candidate_k:int=Field(default=20,ge=1,le=100)
    score_threshold:float|None=Field(default=None,allow_inf_nan=False)
    rrf_k:int=Field(default=60,ge=1,le=1000)
    vector_weight:float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    filter:dict[str,str]=Field(default_factory=dict)
    @field_validator('filter')
    @classmethod
    def valid_filter(cls,value):
        if set(value)-{'filename','document_id'} or any(len(v)>240 for v in value.values()):raise ValueError('Supported metadata filters: filename, document_id')
        return value

class KBSettings(StrictModel):
    """Immutable indexing settings of one knowledge-base version. Blank embedding_model means keyword-only."""
    backend:Literal['faiss','chroma','elasticsearch','pinecone']='faiss'
    storage_path:str=Field(default='default',min_length=1,max_length=64)
    embedding_model:str=Field(default='',max_length=100)
    chunk_size:int=Field(default=1200,ge=100,le=8000)
    chunk_overlap:int=Field(default=200,ge=0,le=4000)
    chunking:Literal['fixed','paragraph']='fixed'
    index_method:str=Field(default='',max_length=40)
    connection_id:str=Field(default='',max_length=64)
    index_name:str=Field(default='',max_length=101)
    @field_validator('embedding_model')
    @classmethod
    def trimmed(cls,value):return value.strip()
