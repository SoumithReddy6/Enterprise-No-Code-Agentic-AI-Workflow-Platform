"""A53-I01 isolation copy: git archive 2ad952a + the changed files, one commit."""
import hashlib, pathlib, shutil, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
FILES = ['backend/app/accounting_state.py', 'backend/tests/test_parallel_accounting.py']
iso = pathlib.Path(tempfile.mkdtemp(prefix='a53-i01-iso-', dir=SP))
subprocess.run(f'git archive 2ad952a | tar -x -C "{iso}"', shell=True, check=True)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for f in FILES: shutil.copy2(f, iso / f)
git = lambda *a: subprocess.run(['git', *a], cwd=iso, check=True, capture_output=True, text=True).stdout.strip()
git('init', '-q'); git('add', '-A'); git('-c', 'user.email=iso@local', '-c', 'user.name=iso', 'commit', '-qm', 'a53 i01 isolation')
(SP / 'iso-i01-path').write_text(str(iso))
other = [p for p in subprocess.run(['git', 'diff', '--name-only', '2ad952a', '--', '.', ':!evals', ':!docs/reviews'],
         capture_output=True, text=True, check=True).stdout.split() if p not in FILES]
lines = [f'# A53-I01 isolation copy: git archive 2ad952a + the {len(FILES)} changed files, git init + one commit',
         f'copy: <scratchpad>/{iso.name}', f'commit: {git("rev-parse", "HEAD")}', '', '## Files copied (sha256; copy == working tree)']
lines += [f'{sha(pathlib.Path(f))} {f} {"match" if sha(pathlib.Path(f)) == sha(iso / f) else "DIFFER"}' for f in FILES]
lines += ['', '## Tracked files that differ from 2ad952a, outside these (evals/ and docs/reviews/ excluded)', *(other or ['none'])]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
