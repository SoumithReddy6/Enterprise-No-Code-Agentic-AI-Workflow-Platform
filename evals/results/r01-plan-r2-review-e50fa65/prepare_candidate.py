"""Revision-2 audit prototype in a disposable git-archive export, never the checkout."""
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/relay-r01-plan-') and not (root/'.git').exists()
repo = Path(__file__).resolve().parents[3]
runpy.run_path(str(repo/'evals/results/r01-plan-review-65a7774/prepare_candidate.py'), run_name='__main__')


def replace(path, old, new):
    p = root/path
    text = p.read_text()
    assert text.count(old) == 1, (path, text.count(old), old[:80])
    p.write_text(text.replace(old, new, 1))


replace('backend/app/platform_nodes.py', 'def published_batches(sources):\n', '''def source_list(value):
    if isinstance(value,str):value=json.loads(value)
    if not isinstance(value,list) or any(not isinstance(s,dict) or not isinstance(s.get('id'),str) or not s['id'] for s in value):
        raise ValueError('Saved knowledge sources are malformed.')
    return value


def published_batches(sources):
''')
replace('backend/app/platform_nodes.py', '    for source in canonical(sources):\n', '    for source in canonical(source_list(sources)):\n')
p = root/'backend/app/platform_nodes.py'
text = p.read_text()
start = text.index('async def verify_published(ctx,node_id,sources):\n')
text = text[:start] + '''async def verify_published(ctx,node_id,sources):
    from .execution_policy import CONTROL_FLOW
    from .request_safety import UnsafeExecutionError
    try:
        sources=source_list(sources)
        if not sources:return
        if ctx.platform is None:
            raise UnsafeExecutionError(f'{node_id}: knowledge source verification is unavailable. No answer was published.')
        for batch in published_batches(sources):
            await ctx.platform('verify_vector_sources',batch)
    except CONTROL_FLOW:raise
    except (KeyError,ValueError,TypeError):
        raise UnsafeExecutionError(f'{node_id}: a listed knowledge source is unavailable or was removed. No answer was published. Start a new run with current evidence.') from None
'''
p.write_text(text)
replace('backend/app/platform_validation.py',
    'async def kb_errors(services,workflow,tenant,checkpoints=None,dependencies=None):',
    'async def kb_errors(services,workflow,tenant,checkpoints=None,dependencies=None,progress=None):')
replace('backend/app/platform_validation.py', '    errors=[]\n    for node in workflow.nodes:', '''    errors=[]
    from .platform_nodes import source_list
    def reused_loop(owner):
        for node in workflow.nodes:
            prefix=node.id+':'
            if node.type=='for_each' and owner.startswith(prefix):
                index=owner[len(prefix):]
                return index.isdigit() and (node.id in (checkpoints or {}) or str(int(index)) in (progress or {}).get(node.id,{}))
        return False
    for node in workflow.nodes:''')
replace('backend/app/platform_validation.py',
    'published_batches(json.loads(checkpoints[node.id][\'sources\']))',
    'published_batches(source_list(checkpoints[node.id][\'sources\']))')
replace('backend/app/platform_validation.py', "sources=json.loads(cached['sources'])", "sources=source_list(cached['sources'])")
replace('backend/app/platform_validation.py', '        if owner not in (checkpoints or {}):continue',
    '        if owner not in (checkpoints or {}) and not reused_loop(owner):continue')
replace('backend/app/platform_validation.py',
    "            if not isinstance(sources,list) or len(sources)>120:raise ValueError('Invalid saved source dependencies.')",
    "            sources=source_list(sources)\n            if len(sources)>120:raise ValueError('Invalid saved source dependencies.')")
for path in ('backend/app/worker.py','backend/app/main.py'):
    p = root/path
    text = p.read_text()
    for name in ('run', 'previous'):
        text = text.replace(f"{name}.get('vector_dependencies'))", f"{name}.get('vector_dependencies'),{name}.get('loop_progress'))")
    p.write_text(text)
replace('backend/app/worker.py',
    "            return {'output':'\\n\\n'.join(result['values'][n.id]['text'] for n in workflow.nodes if n.type=='response' and n.id in result['values']),",
    "            for n in workflow.nodes:\n                if n.type=='response' and n.id in result['values'] and 'text' not in result['values'][n.id]:raise ValueError(f'{n.id}: the Response produced no answer.')\n            return {'output':'\\n\\n'.join(result['values'][n.id]['text'] for n in workflow.nodes if n.type=='response' and n.id in result['values']),")
print('Prepared revision-2 audit candidate:', root)
