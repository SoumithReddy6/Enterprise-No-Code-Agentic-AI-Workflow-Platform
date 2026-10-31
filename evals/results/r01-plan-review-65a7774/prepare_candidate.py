"""Apply only the plan's three-file changes to a disposable archive export.

This is an audit prototype, not product implementation. Refuses a .git checkout.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/relay-r01-plan-') and not (root / '.git').exists()

helper = '''

def published_batches(sources):
    seen=set(); batch=[]; ids=set()
    for source in canonical(sources):
        key=json.dumps(source,sort_keys=True,separators=(',',':'))
        if key in seen:continue
        seen.add(key)
        if len(batch)==120 or source.get('id') in ids:
            yield batch;batch=[];ids=set()
        batch.append(source);ids.add(source.get('id'))
    if batch:yield batch


async def verify_published(ctx,node_id,sources):
    from .execution_policy import CONTROL_FLOW
    if not sources or ctx.platform is None:return
    try:
        for batch in published_batches(sources):
            await ctx.platform('verify_vector_sources',batch)
    except CONTROL_FLOW:raise
    except (KeyError,ValueError,TypeError):
        raise ValueError(f'{node_id}: a listed knowledge source is unavailable or was removed. No answer was published. Start a new run with current evidence.') from None
'''
p = root / 'backend/app/platform_nodes.py'
p.write_text(p.read_text()+helper)
p = root / 'backend/app/registry.py'
text = p.read_text()
old = "    text,sources=ground_answer(inputs['text'],evidence)\n    return {'text': text, 'sources': json.dumps(sources,ensure_ascii=False)}"
assert text.count(old) == 1
p.write_text(text.replace(old, "    text,sources=ground_answer(inputs['text'],evidence)\n    from .platform_nodes import verify_published\n    await verify_published(ctx,ctx.node_id,sources)\n    return {'text': text, 'sources': json.dumps(sources,ensure_ascii=False)}", 1))
p = root / 'backend/app/platform_validation.py'
text = p.read_text()
old = "    for node in workflow.nodes:\n        if node.type not in ('retrieve','query') or not node.config.get('knowledge_base_id'):continue\n"
new = '''    for node in workflow.nodes:
        if node.type=='response' and node.id in (checkpoints or {}):
            from .platform_nodes import published_batches
            try:
                for batch in published_batches(json.loads(checkpoints[node.id]['sources'])):
                    await services.verify(batch,tenant)
            except (KeyError,ValueError,TypeError):
                errors.append(f'{node.id}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.')
        if node.type not in ('retrieve','query') or not node.config.get('knowledge_base_id'):continue
'''
assert text.count(old) == 1
p.write_text(text.replace(old,new,1))
print('Prepared partial R01 plan candidate:', root)
