"""Calls of each lineage walk on the doubled chain (n Prompts, each binding the previous value
twice), one call from the end of the chain. Run from the root of a copy of the code to measure.
unchecked_model_input from input.message; checked_dependency from a verify_extraction's text;
static_label from input.message. The nested calls_model of 88d4d8b is counted by the profiler."""
import sys, time
from backend.app import compiler, provenance
from backend.app.models import Node

def chain(n, root_type, root_port):
    nodes = {'r': Node(id='r', type=root_type)}; prev = f'r.{root_port}'
    for i in range(n):
        nodes[f'p{i}'] = Node(id=f'p{i}', type='prompt', inputs={'message': prev, 'x': prev}); prev = f'p{i}.text'
    return nodes, prev

def count(module, names, call):
    n = [0]
    def prof(frame, event, arg):
        if event == 'call' and frame.f_code.co_name in names and frame.f_code.co_filename == module.__file__: n[0] += 1
    t = time.perf_counter(); sys.setprofile(prof)
    try: value = call()
    finally: sys.setprofile(None)
    return n[0], value, round(time.perf_counter() - t, 3)

for n in map(int, sys.argv[1:]):
    a, ra = chain(n, 'chat_input', 'message'); b, rb = chain(n, 'verify_extraction', 'text')
    rows = {'unchecked_model_input': count(compiler, {'unchecked_model_input', 'calls_model', '_model_walk'}, lambda: compiler.unchecked_model_input(ra, a, {})),
            'checked_dependency': count(compiler, {'checked_dependency', '_checked_walk'}, lambda: compiler.checked_dependency(rb, b)),
            'static_label': count(provenance, {'static_label', '_static_walk'}, lambda: provenance.static_label(a, ra))}
    print(n, {k: {'calls': c, 'answer': v, 'seconds_profiled': s} for k, (c, v, s) in rows.items()}, flush=True)
