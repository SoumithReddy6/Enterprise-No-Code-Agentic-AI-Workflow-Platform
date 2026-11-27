"""Run A6.1 Part 2 mutants M34-M52 in a scratch copy of the frontend. Usage: mutants.py <copy> <out dir> [ids]"""
import json, subprocess, sys, shutil
from pathlib import Path
copy, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
only = set(sys.argv[3:])
W, R, E, C = 'lib/workflow.ts', 'lib/run-status.ts', 'components/workflow-editor.tsx', 'components/condition-fields.tsx'
U = lambda *ids: ('unit', ids)
B = lambda *ids: ('browser', ids)
MUTANTS = {
 'M00': ('control: no change', [], [U('U01','U02','U03','U04','U05','U06','U07','U08'), B('B01','B02','B03','B04','B05','B06','B07'), ('spec', ('switch.spec.ts',))]),
 'M34': ('Repeat not in the palette', [(W, "        repeat: 'Control',\n", '')], [U('U01'), B('B01')]),
 'M35': ('The Repeat card draws no check handle', [(E, "(spec.type === 'repeat' ? ['body', 'check'] : ['body'])", "['body']")], [B('B02'), B('B01')]),
 'M36': ('connectionKind returns flow for the body handle', [(W, "if (sourceHandle === 'body')\n    return LOOP_TYPES.includes(sourceType) ? 'loop' : null;", "if (sourceHandle === 'body')\n    return LOOP_TYPES.includes(sourceType) ? 'flow' : null;")], [U('U02'), B('B01')]),
 'M37': ('A loop connection does not set body', [(W, 'n.id === edge.source && port ?', "n.id === edge.source && port && port !== 'body' ?")], [U('U03'), B('B01')]),
 'M38': ('withoutLoopAttachment clears the setting when another target is removed', [(W, 'if (!port || !owner || owner.config[port] !== e.target) continue;', 'if (!port || !owner) continue;')], [U('U03')]),
 'M39': ('nodeStatuses keeps loop_item events', [(R, "e.node_id && e.kind !== 'loop_item' && e.kind !== 'loop_summary'", "e.node_id && e.kind !== 'loop_summary'")], [U('U04'), B('B04')]),
 'M40': ('The iteration number is item_index (from 0)', [(R, '${unit} ${event.item_index + 1}', '${unit} ${event.item_index}')], [U('U05'), B('B04')]),
 'M41': ('The summary note reads limit as rule', [(R, "    case 'rule': return", "    case 'limit':\n    case 'rule': return")], [U('U05')]),
 'M42': ('incompleteRun ignores the node type', [(R, "cause.kind === 'loop' && typeOf(workflow, cause.node_id) === 'repeat'", 'false')], [U('U06')]),
 'M43': ('ConditionFields loses withConditionOperator (Switch too)', [(C, 'onChange(withConditionOperator(rule, String(value)));', 'onChange({ ...rule, operator: String(value) });')], [B('B03'), ('spec', ('switch.spec.ts',))]),
 'M44': ('A new Repeat gets no default until', [(E, "    if (type === 'repeat') spec.config = newRepeatConfig();", '')], [B('B01')]),
 'M45': ("canvasEdge keeps targetHandle 'input'", [(W, "targetHandle: edge.targetHandle === 'input' ? null : (edge.targetHandle ?? null),", 'targetHandle: edge.targetHandle ?? null,')], [U('U07'), B('B02')]),
 'M46': ('The flow source of a loop card has no id', [(E, "id={isLoopType(spec.type) ? 'flow' : undefined}", 'id={undefined}')], [B('B02')]),
 'M47': ('Keyboard Delete keeps the separate node and edge handlers', [
     (E, "const relevant = changes.filter((c) => c.type !== 'select' && c.type !== 'remove');", "const relevant = changes.filter((c) => c.type !== 'select');\n                  if (relevant.some((c) => c.type === 'remove')) snapshot();"),
     (E, "setEdges((es) => applyEdgeChanges(changes.filter((c) => c.type !== 'remove'), es));", "if (changes.some((c) => c.type === 'remove')) snapshot();\n                  setEdges((es) => applyEdgeChanges(changes, es));"),
     (E, "                onDelete={({ nodes: gone, edges: cut }) =>\n                  removeFromCanvas(gone.map((n) => n.id), cut.map((e) => e.id))\n                }\n", '')], [B('B03')]),
 'M48': ('The occupied-port refusal is removed (editor)', [(E, 'if (portInUse(graphNow(), from.id, port)) {', 'if (false && portInUse(graphNow(), from.id, port)) {')], [B('B03')]),
 'M49': ('A missing met is read as false', [(R, 'if (row?.met === false)', 'if (!row?.met)')], [U('U05')]),
 'M50': ('unparsedJsonSettings ignores until', [(W, "    if (node.type === 'repeat') rule(node.config.until, 'Stop rule');", '')], [U('U08'), B('B07')]),
 'M51': ('edgePort maps only an unnamed flow edge to flow (not output)', [(W, "if (handle !== null && handle !== 'output') return handle;", 'if (handle !== null) return handle;')], [U('U07'), B('B02')]),
 'M52': ('portInUse and removeElements read the raw sourceHandle', [
     (W, 'edgePort(e, typeOf(graph, source)) === port', '(e.sourceHandle ?? null) === port'),
     (W, 'const port = edgePort(e, typeOf(graph, e.source));', 'const port = e.sourceHandle ?? null;')], [U('U03')]),
}
results = {}
for mid, (desc, edits, oracles) in MUTANTS.items():
    if only and mid not in only: continue
    files = {f for f, _, _ in edits}
    saved = {f: (copy / f).read_text() for f in files}
    try:
        for f, old, new in edits:
            text = (copy / f).read_text()
            assert text.count(old) == 1, (mid, f, old[:60], text.count(old))
            (copy / f).write_text(text.replace(old, new))
        log, verdicts = [], []
        for kind, ids in oracles:
            if kind == 'unit':
                cmd = ['node', '--experimental-strip-types', '--test', '--test-name-pattern', '^(' + '|'.join(ids) + ') ', 'tests/repeat.test.ts']
            elif kind == 'browser':
                cmd = ['npx', 'playwright', 'test', 'tests/browser/repeat.spec.ts', '--reporter=line', '-g', '(' + '|'.join(ids) + ') ']
            else:
                cmd = ['npx', 'playwright', 'test', *[f'tests/browser/{i}' for i in ids], '--reporter=line']
            p = subprocess.run(cmd, cwd=copy, capture_output=True, text=True)
            log.append(f"$ {' '.join(cmd)}\nexit {p.returncode}\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}")
            ran_none = 'No tests found' in p.stdout + p.stderr or 'ℹ tests 0' in p.stdout
            assert not ran_none, (mid, kind, ids, 'oracle selected no test')
            verdicts.append({'oracle': f'{kind}:{",".join(ids)}', 'killed': p.returncode != 0})
        (out / f'{mid}.txt').write_text(f'{mid}: {desc}\n\n' + '\n\n'.join(log))
        results[mid] = {'change': desc, 'oracles': verdicts, 'killed': any(v['killed'] for v in verdicts)}
        print(mid, results[mid]['killed'], [(v['oracle'], v['killed']) for v in verdicts], flush=True)
    finally:
        for f, text in saved.items(): (copy / f).write_text(text)
prev = json.loads((out / 'summary.json').read_text()) if (out / 'summary.json').exists() else {}
(out / 'summary.json').write_text(json.dumps({**prev, **results}, indent=1) + '\n')
