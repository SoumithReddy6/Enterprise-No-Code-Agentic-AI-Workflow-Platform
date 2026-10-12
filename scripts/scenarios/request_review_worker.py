"""TEST ONLY: block after real commits; the parent delivers SIGKILL.

These failpoints are outside application modules and require the disposable lab marker.
No production worker imports this entry point.
"""
import asyncio
import os
from pathlib import Path
import time


def main():
    directory=Path(os.environ['DATA_DIR'])
    if not (directory/'SCENARIO_LAB_ONLY').is_file():raise SystemExit('Isolated lab marker required')
    from backend.app import approvals,tool_service
    from backend.app.storage import Store,local_key
    from backend.app.worker import Worker
    store=Store(os.environ['DATABASE_URL'],local_key(directory))
    port=int(os.environ['SCENARIO_FIXTURE_PORT']);original=tool_service.public_addresses
    tool_service.public_addresses=lambda host,p:['127.0.0.1'] if host=='scenario-provider.invalid' and p==port else original(host,p)
    stage=os.environ.get('REVIEW_FAILPOINT','')
    def block(name):
        if stage!=name:return
        (directory/'review-barrier').write_text(name)
        while True:time.sleep(.05)
    old_pause=approvals.pause
    def pause(*args):
        result=old_pause(*args);block('pause');return result
    approvals.pause=pause
    old_consume=approvals.consume_review
    def consume(*args):
        result=old_consume(*args)
        if result is not None:block('confirmation')
        return result
    approvals.consume_review=consume
    old_settle=store.settle_write
    def settle(*args):
        result=old_settle(*args);block('settlement');return result
    store.settle_write=settle
    worker=Worker(store,concurrency=1)
    old_execute=worker.tools.execute
    async def execute(*args):
        result=await old_execute(*args);block('uncertain');return result
    worker.tools.execute=execute
    asyncio.run(worker.serve())


if __name__=='__main__':main()
