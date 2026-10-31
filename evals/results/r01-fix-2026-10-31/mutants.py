"""R01 mutants N1-N15: each applied to a fresh copy of the base; the planned tests must fail."""
import shutil,subprocess,sys,tempfile,re,pathlib
BASE=pathlib.Path(open(sys.argv[1]).read().strip()); PY=sys.argv[2]; OUT=sys.argv[3]
NEW='backend/tests/test_response_source_boundary.py'
PN='backend/app/platform_nodes.py'; PV='backend/app/platform_validation.py'; RG='backend/app/registry.py'
M={
'N1':([(RG,"    await verify_published(ctx,ctx.node_id,sources+reused)\n","")],['test_u1_','test_u2_','test_u3_','test_u16_']),
'N2':([(RG,"verify_published(ctx,ctx.node_id,sources+reused)","verify_published(ctx,ctx.node_id,[s for s in sources if s.get('cited')]+reused)")],['test_u1_','test_u3_']),
'N3':([(PN,"unique.setdefault(json.dumps(s,sort_keys=True,ensure_ascii=False,default=str),s)","unique.setdefault(len(unique),s)")],['test_u13_']),
'N4':([(PN,"if len(b)<PUBLISH_BATCH and all(","if all(")],['test_u15_']),
'N5':([(PV,"if node.type=='response' and checkpoints.get(node.id):","if False:")],['test_u7_']),
'N6':([(PN,"    except CONTROL_FLOW:raise\n    except (KeyError,ValueError,TypeError):","    except BaseException:")],['test_u19_']),
'N7':([(PN,"unique.setdefault(json.dumps(s,sort_keys=True,ensure_ascii=False,default=str),s)","unique.setdefault(s['id'],s)")],['test_u14_']),
'N8':([(PV,"        if owner in checkpoints:return True\n","        return owner in checkpoints\n")],['test_u5_u6_']),
'N9':([(PV,"(loop in checkpoints or isinstance(done,dict) and index in done)","loop in checkpoints")],['test_u5_u6_']),
'N10':([(PN,"        if batches and ctx.platform is None:\n            raise UnsafeExecutionError(f'{node_id}: knowledge source verification is unavailable. No answer was published.')\n","        if ctx.platform is None:return\n")],['test_u10_']),
'N11':([(PN,"try:value=json.loads(value)","try:value=json.loads(value) or []")],['test_u12_']),
'N12':([(PN,"raise UnsafeExecutionError(f'{node_id}: a knowledge source behind","raise ValueError(f'{node_id}: a knowledge source behind")],['test_u20_']),
'N13':([('backend/app/main.py',",previous.get('vector_dependencies'),previous.get('loop_progress'))",",previous.get('vector_dependencies'))")],['test_u8_']),
'N14':([(RG,"verify_published(ctx,ctx.node_id,sources+reused)","verify_published(ctx,ctx.node_id,sources)")],['test_u22_u23_','test_u24_']),
'N15':([('backend/app/iteration.py',"            if ctx.run is not None:ctx.run.setdefault('reused_owners',set()).add(item_owner(ctx,index))\n","")],['test_u22_u23_']),
}
lines=[];ok=True
for name,(edits,planned) in M.items():
    work=pathlib.Path(tempfile.mkdtemp(prefix=f'r01-{name}-',dir=BASE.parent))
    shutil.copytree(BASE,work/'repo'); repo=work/'repo'
    for f,a,b in edits:
        p=repo/f; s=p.read_text(); assert s.count(a)==1,(name,f,a[:50]); p.write_text(s.replace(a,b))
    r=subprocess.run([PY,'-m','pytest','-q','-p','no:cacheprovider','-rf',NEW],cwd=repo,capture_output=True,text=True,env={'PYTHONHASHSEED':'0','PATH':'/usr/bin:/bin'})
    failed=sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?:\s|$)',r.stdout,re.M)))
    summary=r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    killed={t:[f for f in failed if f.startswith(t)] for t in planned}
    good=all(killed.values()); ok&=good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT,'w').write('\n'.join(lines)+f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
