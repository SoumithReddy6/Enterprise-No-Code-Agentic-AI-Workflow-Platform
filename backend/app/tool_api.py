"""Workspace-scoped encrypted connection management."""
from fastapi import Depends, HTTPException
from .tool_service import ConnectionConfig

def install_tool_routes(app,tools,tenant_dependency):
    def invoke(fn,*args):
        try:return fn(*args)
        except KeyError:raise HTTPException(404,'Connection unavailable') from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @app.get('/api/connections')
    def connections(tenant=Depends(tenant_dependency)):return invoke(tools.connections,tenant)
    @app.post('/api/connections')
    def create(body:ConnectionConfig,tenant=Depends(tenant_dependency)):return invoke(tools.save_connection,body.model_dump(),tenant)
    @app.put('/api/connections/{connection_id}')
    def update(connection_id:str,body:ConnectionConfig,tenant=Depends(tenant_dependency)):return invoke(tools.save_connection,body.model_dump(),tenant,connection_id)
