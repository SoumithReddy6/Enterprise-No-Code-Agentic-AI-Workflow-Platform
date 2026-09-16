import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow,compile_workflow

def graph():
    return Workflow.model_validate({'version':1,'name':'Knowledge flow','nodes':[{'id':'input','type':'chat_input'},{'id':'retrieve','type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'},'inputs':{'query':'input.message'}},{'id':'agent','type':'agent','config':{'provider':'demo'},'inputs':{'input':'retrieve.context'}},{'id':'out','type':'response','inputs':{'text':'agent.text'}}], 'edges':[{'id':'a','source':'input','target':'retrieve'},{'id':'b','source':'retrieve','target':'agent'},{'id':'c','source':'agent','target':'out'}]})

def test_named_kb_flow_needs_no_vector_node():assert validate_workflow(graph())==[]

@pytest.mark.asyncio
async def test_retrieve_preserves_question_and_evidence_for_agent():
    async def platform(action,*args):
        if action=='kb_retrieve':return {'query':args[1],'knowledge_base_id':'kb1','version':1,'sources':[{'text':'Bluebird is the codename','id':'chunk','document_id':'doc'}]}
        if action in ('record_vector_sources','verify_vector_sources'):return None
        raise AssertionError(action)
    result=await compile_workflow(graph(),message='What codename?',platform_resolver=platform).graph.ainvoke({'values':{}})
    context=json.loads(result['values']['retrieve']['context'])
    assert context['question']=='What codename?' and context['passages'][0]['text']=='Bluebird is the codename'
    assert 'Bluebird' in result['values']['out']['text']
