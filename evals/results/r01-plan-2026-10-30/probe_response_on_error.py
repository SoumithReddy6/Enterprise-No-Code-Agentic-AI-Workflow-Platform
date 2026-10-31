import asyncio,traceback
from app.models import Workflow
from app.compiler import compile_workflow,validate_workflow
for policy in ('continue','route'):
    w=Workflow.model_validate({'version':1,'name':'p','nodes':[{'id':'input','type':'chat_input'},
        {'id':'out','type':'response','on_error':policy,'inputs':{'text':'input.message'}}],
        'edges':[{'id':'a','source':'input','target':'out'}]})
    print(policy,'validation errors:',validate_workflow(w))
    try:
        r=asyncio.run(compile_workflow(w,message='hi').graph.ainvoke({'values':{}}))
        print(policy,'success run ->',r['values'].get('out'))
    except Exception as e:print(policy,'success run raises',type(e).__name__,str(e)[:120])
