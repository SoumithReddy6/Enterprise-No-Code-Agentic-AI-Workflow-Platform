import pytest
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow,compile_workflow
from backend.app.registry import REGISTRY


def platform_flow():
    return Workflow.model_validate({'version':1,'name':'Agent','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}},
        {'id':'helper','type':'agent','config':{'provider':'demo','role':'critic'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},
        {'id':'delegate','source':'agent','target':'helper','kind':'agent','sourceHandle':'agents','targetHandle':'input'}]})


def test_specialist_is_a_dependency_not_a_sequential_graph_node():
    workflow=platform_flow()
    assert not validate_workflow(workflow)


def test_agent_attachment_cycle_rejected():
    graph=platform_flow();graph.edges.append(type(graph.edges[0])(id='back',source='helper',target='agent',kind='agent'))
    assert any('cycle' in e.lower() or 'flow' in e.lower() for e in validate_workflow(graph))

@pytest.mark.asyncio
async def test_agent_demo_runs_without_automatically_calling_specialist():
    graph=compile_workflow(platform_flow(),message='Hello').graph
    result=await graph.ainvoke({'values':{}})
    assert 'Hello' in result['values']['out']['text']
    assert 'helper' not in result['values']


def test_orphan_agents_rejected():
    graph=platform_flow();graph.edges=graph.edges[:2]
    assert any('unreachable' in e.lower() or 'unconnected' in e.lower() for e in validate_workflow(graph))
