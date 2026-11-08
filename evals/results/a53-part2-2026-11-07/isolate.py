"""A5.3 Part 2 isolation copy: git archive 4db70a7 + the changed files, one commit.

node_modules is a symlink to the working tree's (dependencies unchanged: package files are not in FILES).
"""
import hashlib, os, pathlib, shutil, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
FILES = ['frontend/lib/workflow.ts', 'frontend/components/workflow-editor.tsx', 'frontend/app/globals.css',
         'frontend/tests/parallel.test.ts', 'frontend/tests/browser/parallel-branches.spec.ts',
         'frontend/tests/browser/parallel-issues.json', 'backend/tests/test_parallel_editor_contract.py',
         'README.md', 'docs/operations.md', 'docs/platform-reference.md',
         'docs/superpowers/specs/2026-11-07-parallel-branches-editor-design.md']
iso = pathlib.Path(tempfile.mkdtemp(prefix='a53-p2-iso-', dir=SP))
subprocess.run(f'git archive 4db70a7 | tar -x -C "{iso}"', shell=True, check=True)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for f in FILES: shutil.copy2(f, iso / f)
git = lambda *a: subprocess.run(['git', *a], cwd=iso, check=True, capture_output=True, text=True).stdout.strip()
git('init', '-q'); git('add', '-A'); git('-c', 'user.email=iso@local', '-c', 'user.name=iso', 'commit', '-qm', 'a53 part2 isolation')
os.symlink(pathlib.Path('frontend/node_modules').resolve(), iso / 'frontend/node_modules')
(SP / 'iso-p2-path').write_text(str(iso))
changed = subprocess.run(['git', 'status', '--porcelain', '--', '.', ':!evals', ':!docs/reviews'], capture_output=True, text=True, check=True).stdout
other = [l[3:] for l in changed.splitlines() if l[3:] not in FILES]
lines = [f'# A5.3 Part 2 isolation copy: git archive 4db70a7 + the {len(FILES)} changed files, git init + one commit',
         f'copy: <scratchpad>/{iso.name}', f'commit: {git("rev-parse", "HEAD")}', 'frontend/node_modules: symlink to the working tree (no dependency change)',
         '', '## Files copied (sha256; copy == working tree)']
lines += [f'{sha(pathlib.Path(f))} {f} {"match" if sha(pathlib.Path(f)) == sha(iso / f) else "DIFFER"}' for f in FILES]
lines += ['', '## Other changed or untracked files outside evals/ and docs/reviews/', *(other or ['none'])]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
