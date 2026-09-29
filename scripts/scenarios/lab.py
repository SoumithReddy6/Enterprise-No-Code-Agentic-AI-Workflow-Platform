"""TEST ONLY: isolated API/worker process entry points for the scenario campaign.

No application module imports this file. DNS substitution is confined to the single
fixture hostname and port supplied by the parent runner. All other SSRF checks remain.
"""
import argparse
import asyncio
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('role',choices=['api','worker']);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    directory=Path(os.environ['DATA_DIR']).resolve()
    if not (directory/'SCENARIO_LAB_ONLY').is_file():raise SystemExit('Refusing to start without isolated scenario marker')
    from backend.app import tool_service
    original=tool_service.public_addresses
    fixture_port=int(os.environ['SCENARIO_FIXTURE_PORT'])
    def addresses(host,port):
        if host=='scenario-provider.invalid' and port==fixture_port:return ['127.0.0.1']
        return original(host,port)
    tool_service.public_addresses=addresses
    if args.role=='api':
        import uvicorn
        from backend.app.main import create_app
        uvicorn.run(create_app(embedded_worker=False),host='127.0.0.1',port=args.port,access_log=False)
    else:
        from backend.app.worker import Worker
        from backend.app.storage import Store,local_key
        store=Store(os.environ['DATABASE_URL'],local_key(directory))
        original_event=store.worker_event
        def durable_event(*args):
            result=original_event(*args)
            marker=directory/'crash-after-item'
            if result and args[-1].get('kind')=='loop_item' and marker.exists():
                marker.unlink()
                # Deliberate crash after the real SQL commit, before the handler returns.
                os._exit(91)
            return result
        store.worker_event=durable_event
        asyncio.run(Worker(store).serve())

if __name__=='__main__':main()
