"""Prototype revision 3 only in a disposable archive export; never the checkout."""
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/relay-r01-plan-') and not (root/'.git').exists()
repo = Path(__file__).resolve().parents[3]
runpy.run_path(str(repo/'evals/results/r01-plan-r2-review-e50fa65/prepare_candidate.py'), run_name='__main__')


def replace(path, old, new):
    p = root/path
    text = p.read_text()
    assert text.count(old) == 1, (path, text.count(old), old[:80])
    p.write_text(text.replace(old, new, 1))


replace('backend/app/compiler.py', "run_id=None,tenant_id='local'):",
    "run_id=None,tenant_id='local',saved_dependencies=None):")
replace('backend/app/compiler.py', "               'evidence':[],'citation_counter':citation_counter,",
    "               'saved_dependencies':dict(saved_dependencies or {}),'reused_owners':set(),\n               'evidence':[],'citation_counter':citation_counter,")
replace('backend/app/compiler.py', '                    if inspect.isawaitable(validation):await validation\n',
    '''                    if inspect.isawaitable(validation):await validation
                    if node.type=='for_each':
                        prefix=node.id+':'
                        run_state['reused_owners'].update(owner for owner in run_state['saved_dependencies']
                            if owner.startswith(prefix) and owner[len(prefix):].isdigit())
''')
replace('backend/app/iteration.py', '        if index in done:\n', '''        if index in done:
            if ctx.run is not None:ctx.run.setdefault('reused_owners',set()).add(item_owner(ctx,index))
''')
replace('backend/app/registry.py', '    await verify_published(ctx,ctx.node_id,sources)\n',
    '''    from .platform_nodes import source_list
    reused=[]
    for owner in sorted((ctx.run or {}).get('reused_owners',set())):
        reused.extend(source_list((ctx.run or {}).get('saved_dependencies',{}).get(owner,[])))
    await verify_published(ctx,ctx.node_id,sources+reused)
''')
replace('backend/app/platform_nodes.py', 'a listed knowledge source is unavailable or was removed.',
    'a knowledge source behind this answer is unavailable or was removed.')
replace('backend/app/worker.py', "run_id=id,tenant_id=tenant_id).graph",
    "run_id=id,tenant_id=tenant_id,saved_dependencies=run.get('vector_dependencies')).graph")
print('Prepared revision-3 audit candidate:',root)
