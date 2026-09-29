import json
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from backend.app import schema
from backend.app.iteration import ResultBudget, serialized_bytes


def test_0003_downgrade_preserves_inflight_progress_and_accounting(tmp_path):
    """Rolling the app/schema back must not silently replenish budget or forget items."""
    engine=create_engine(f'sqlite:///{tmp_path}/rollback.db')
    with engine.begin() as connection:
        schema.upgrade(connection)
        document={'status':'failed','workflow':{},'message':'x','loop_progress':{}}
        accounting={'action_budget':{'remaining':1,'counter':2,'ceiling':3,'charged':['each:0:work#0','each:1:work#0']}}
        connection.execute(text("INSERT INTO runs (id,tenant_id,data,accounting,created_at,status,name,citation_counter,grounded,abstained,truncated,truncation_source) VALUES ('r','local',:data,:accounting,'2026-09-29','failed','r',0,0,0,0,'')"),
                           {'data':json.dumps(document),'accounting':json.dumps(accounting)})
        entry={'index':0,'status':'success','value':'already paid','error':''}
        connection.execute(text("INSERT INTO run_loop_items (run_id,node_id,item_index,entry) VALUES ('r','each',0,:entry)"),{'entry':json.dumps(entry)})
    with engine.begin() as connection:
        command.downgrade(schema._config(connection),'0002_truncation_source')
        restored=json.loads(connection.execute(text("SELECT data FROM runs WHERE id='r'")).scalar())
    assert restored.get('accounting')==accounting
    assert restored.get('loop_progress',{}).get('each',{}).get('0',{}).get('value')=='already paid'


def test_result_budget_really_bounds_the_serialized_result(monkeypatch):
    """The stated whole-list bound must also cover required outcome metadata."""
    from backend.app import iteration
    monkeypatch.setattr(iteration,'MAX_RESULT_BYTES',300)
    budget=ResultBudget();results=[]
    for index in range(3):
        results.append(budget.admit({'index':index,'status':'failed','value':'','error':'x'*180}))
    assert serialized_bytes(results)<=iteration.MAX_RESULT_BYTES
