"""A5.3 Part 3 isolation copy: git archive f571853 (the Part 3 design commit) + the changed files, one commit.

No frontend file changes, so no node_modules is needed. Usage: python isolate.py <scratch dir> <output file>
"""
import hashlib, pathlib, shutil, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
FILES = ['scripts/check_production_scenarios.py', 'scripts/scenarios/parallel_branches.py',
         'README.md', 'docs/platform-reference.md', 'docs/production-scenario-testing.md',
         'docs/superpowers/specs/2026-11-08-parallel-branches-scenarios-design.md']
iso = pathlib.Path(tempfile.mkdtemp(prefix='a53-p3-iso-', dir=SP))
subprocess.run(f'git archive f571853 | tar -x -C "{iso}"', shell=True, check=True)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for f in FILES: shutil.copy2(f, iso / f)
git = lambda *a: subprocess.run(['git', *a], cwd=iso, check=True, capture_output=True, text=True).stdout.strip()
git('init', '-q'); git('add', '-A'); git('-c', 'user.email=iso@local', '-c', 'user.name=iso', 'commit', '-qm', 'a53 part3 isolation')
(SP / 'iso-p3-path').write_text(str(iso))
changed = subprocess.run(['git', 'status', '--porcelain', '--', '.', ':!evals', ':!docs/reviews'], capture_output=True, text=True, check=True).stdout
other = [l[3:] for l in changed.splitlines() if l[3:] not in FILES]
lines = [f'# A5.3 Part 3 isolation copy: git archive f571853 + the {len(FILES)} changed files, git init + one commit',
         f'copy: <scratchpad>/{iso.name}', f'commit: {git("rev-parse", "HEAD")}', '', '## Files copied (sha256; copy == working tree)']
lines += [f'{sha(pathlib.Path(f))} {f} {"match" if sha(pathlib.Path(f)) == sha(iso / f) else "DIFFER"}' for f in FILES]
lines += ['', '## Other changed or untracked files outside evals/ and docs/reviews/', *(other or ['none'])]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
