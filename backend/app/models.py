"""Portable, versioned workflow representation, independent of the editor/runtime."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Position(StrictModel):
    x: float = 0
    y: float = 0

class Node(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    type: str
    version: Literal[1] = 1
    label: str = Field(default='', max_length=120)
    position: Position = Field(default_factory=Position)
    inputs: dict[str, str] = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)

class Edge(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None
    kind: Literal['flow','tool','agent','store'] = 'flow'

class Workflow(StrictModel):
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    nodes: list[Node] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(default_factory=list, max_length=200)
