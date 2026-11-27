"""The fixed load workflows. Version them by name; never edit one in place."""
MODEL = 'load-stub'


def _agent():
    return {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'},
            'config': {'provider': 'ollama', 'model': MODEL, 'max_steps': 1}}


def w3():
    """Input -> Agent (one model call) -> Response: 3 nodes."""
    return {'version': 1, 'name': 'load-w3', 'nodes': [
        {'id': 'input', 'type': 'chat_input'}, _agent(),
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'e0', 'source': 'input', 'target': 'agent'}, {'id': 'e1', 'source': 'agent', 'target': 'out'}]}


def w20():
    """Input -> Agent -> 17 Prompt nodes in a chain -> Response: 20 nodes, one model call."""
    nodes = [{'id': 'input', 'type': 'chat_input'}, _agent()]
    edges = [{'id': 'e0', 'source': 'input', 'target': 'agent'}]
    previous = 'agent'
    for index in range(17):
        node = f'p{index:02d}'
        nodes.append({'id': node, 'type': 'prompt', 'inputs': {'message': f'{previous}.text'}, 'config': {'template': '{message}'}})
        edges.append({'id': f'c{index:02d}', 'source': previous, 'target': node})
        previous = node
    nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': f'{previous}.text'}})
    edges.append({'id': 'eo', 'source': previous, 'target': 'out'})
    return {'version': 1, 'name': 'load-w20', 'nodes': nodes, 'edges': edges}


WORKFLOWS = {'w3': (w3, 1), 'w20': (w20, 1)}  # name -> (builder, model calls for each run)
