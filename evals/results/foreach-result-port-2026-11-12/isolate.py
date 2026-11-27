"""Build the For each result-port isolation copy: git archive 00281fb + the changed files, one commit."""
import hashlib, pathlib, shutil, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
FILES = """README.md backend/app/compiler.py backend/app/iteration.py backend/app/repeat.py
backend/tests/test_for_each_result_port.py docs/operations.md""".split()
iso = pathlib.Path(tempfile.mkdtemp(prefix='fe-iso-', dir=SP))
subprocess.run(f'git archive 00281fb | tar -x -C "{iso}"', shell=True, check=True)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for f in FILES:
    (iso / f).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, iso / f)
git = lambda *a: subprocess.run(['git', *a], cwd=iso, check=True, capture_output=True, text=True).stdout.strip()
git('init', '-q'); git('add', '-A'); git('-c', 'user.email=iso@local', '-c', 'user.name=iso', 'commit', '-qm', 'a61 part1 foreach isolation')
(SP / 'iso-path').write_text(str(iso))
other = [p for p in subprocess.run(['git', 'diff', '--name-only', '00281fb', '--', '.', ':!evals', ':!docs/reviews'],
         capture_output=True, text=True, check=True).stdout.split() if p not in FILES]
lines = [f'# For each result-port isolation copy: git archive 00281fb + the {len(FILES)} changed files, git init + one commit',
         f'copy: <scratchpad>/{iso.name}', f'commit: {git("rev-parse", "HEAD")}', '', '## Files copied (sha256; copy == working tree)']
lines += [f'{sha(pathlib.Path(f))} {f} {"match" if sha(pathlib.Path(f)) == sha(iso / f) else "DIFFER"}' for f in FILES]
lines += ['', f'## Tracked files that differ from 00281fb, outside the {len(FILES)} (evals/ and docs/reviews/ excluded)', *(other or ['none'])]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
