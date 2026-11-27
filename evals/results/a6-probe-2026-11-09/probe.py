import json
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow
def wf(nodes, edges): return Workflow.model_validate({'version':1,'name':'p','nodes':[{'id':'input','type':'chat_input'},*nodes],'edges':edges})
agent=lambda id,role='reasoner',inputs=None:{'id':id,'type':'agent','inputs':inputs if inputs is not None else {'input':'input.message'},'config':{'provider':'demo','role':role}}
out=lambda src:{'id':'out','type':'response','inputs':{'text':src}}
# 1 back-edge: draft -> critic -> condition(false) -> draft
cyc=wf([agent('draft'),agent('critic','critic',{'input':'draft.text'}),{'id':'ok','type':'condition','inputs':{'value':'critic.text'},'config':{'operator':'contains','contains':'PASS'}},out('draft.text')],
 [{'id':'e1','source':'input','target':'draft'},{'id':'e2','source':'draft','target':'critic'},{'id':'e3','source':'critic','target':'ok'},
  {'id':'e4','source':'ok','target':'out','sourceHandle':'true'},{'id':'e5','source':'ok','target':'draft','sourceHandle':'false'}])
print('cycle:',validate_workflow(cyc))
# 2 nested for_each
nest=wf([{'id':'list','type':'tool_python','inputs':{'input':'input.message'},'config':{}},
         {'id':'outer','type':'for_each','inputs':{'items':'list.text'},'config':{'body':'inner'}},
         {'id':'inner','type':'for_each','config':{'body':'a'}},{**agent('a'),'inputs':{}},out('outer.summary')],
 [{'id':'e1','source':'input','target':'list'},{'id':'e2','source':'list','target':'outer'},{'id':'e3','source':'outer','target':'out'},
  {'id':'l1','source':'outer','target':'inner','kind':'loop'},{'id':'l2','source':'inner','target':'a','kind':'loop'}])
print('nested:',validate_workflow(nest))
