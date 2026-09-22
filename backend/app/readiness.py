"""Cheap liveness is separate; readiness bounds every dependency probe."""
import asyncio
import time
from sqlalchemy import Column,String,Float,select,text,func,delete
from sqlalchemy.orm import Session
from .storage import Base,JobRecord

PROBE_SECONDS=3.0  # Tolerate brief database latency; still fail the first unhealthy probe.
WORKER_MAX_AGE=30.0

class WorkerHeartbeat(Base):
    __tablename__='worker_heartbeats'
    id=Column(String(64),primary_key=True)
    last_seen=Column(Float,nullable=False,index=True)

def worker_seen(store,identifier):
    stamp=time.time()
    with Session(store.engine) as session:
        row=session.get(WorkerHeartbeat,identifier)
        if row:row.last_seen=stamp
        else:session.add(WorkerHeartbeat(id=identifier,last_seen=stamp))
        session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen<stamp-3600))
        session.commit()

def database_probe(store):
    with store.engine.connect() as connection:
        connection.execute(text('SELECT 1'))
        latest=connection.scalar(select(func.max(WorkerHeartbeat.last_seen)))
        live_lease=connection.scalar(select(JobRecord.run_id).where(JobRecord.status=='running',JobRecord.lease_until>time.time()).limit(1))
    return time.time() if live_lease else latest

class Readiness:
    def __init__(self,store,services):self.store=store;self.services=services;self.pending=None
    async def database(self):
        # Never enqueue another synchronous probe while a timed-out driver is still blocked.
        if self.pending is None or self.pending.done():
            self.pending=asyncio.create_task(asyncio.to_thread(database_probe,self.store))
            self.pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        latest=await asyncio.wait_for(asyncio.shield(self.pending),PROBE_SECONDS)
        return latest
    async def check(self):
        async def probe(name,call):
            try:
                result=await asyncio.wait_for(call(),PROBE_SECONDS)
                return name,{'status':'ok'},result
            except Exception:return name,{'status':'unavailable'},None
        results=await asyncio.gather(probe('database',self.database),probe('management',self.services.management.health),probe('search',self.services.index.health))
        checks={name:state for name,state,_ in results}
        latest=results[0][2]
        checks['worker']={'status':'ok' if latest is not None and 0<=time.time()-latest<=WORKER_MAX_AGE else 'unavailable'}
        healthy=all(c['status']=='ok' for c in checks.values())
        return {'status':'ready' if healthy else 'not_ready','checks':checks},200 if healthy else 503
