"""results.json -> summary.md. A missing number prints as "unmeasured", never as zero."""
import json
import statistics
import sys
from pathlib import Path


def show(value, digits=1):
    return 'unmeasured' if value is None else f'{value:,.{digits}f}' if isinstance(value, float) else f'{value:,}'


def write_summary(out):
    out = Path(out)
    report = json.loads((out / 'results.json').read_text())
    groups = {}
    for cell in report['cells']:
        groups.setdefault((cell['workflow'], cell['database'], cell['latency_ms'], cell['workers']), []).append(cell)
    machine = report['machine']
    lines = [
        '# Load baseline', '',
        '**This is a relative baseline for later changes on the same machine. It is not a capacity claim.** '
        'One laptop ran the API, the workers, the model stub and (for PostgreSQL) the database container.', '',
        f"- Commit: `{report['commit']}` (last change to `backend/app`: `{report['source_commit']}`); dirty: {report['dirty']}",
        f"- Machine: {machine['platform']}, {machine['cpu_count']} CPUs, {show(machine['memory_gb'])} GB; Python {machine['python']}",
        f"- SQLite {report['database']['sqlite']}; PostgreSQL {report['database']['postgres'] or 'not run'} ({report['database']['postgres_settings']['container']}, max_connections 500)",
        f"- Relay settings (fixed in the code): {report['relay_settings']}",
        f"- Mode: {report['mode']}",
        f"- Command: `{report['command']}`", '',
        'Columns: runs/min is the median of the repeats, with the minimum and maximum. Drain time runs from the first '
        'claim to the last finish. Claim-to-finish is for one run. Commits and updated rows are database-wide deltas '
        'divided by completed runs (PostgreSQL only); they include heartbeats, idle polls and the harness\'s one status '
        'query a second.', '']
    for workflow in sorted({key[0] for key in groups}):
        lines += [f'## Workflow `{workflow}`', '',
                  '| Database | Latency ms | Workers × slots | Runs | Runs/min (min–max) | Drain s | Claim-to-finish p50 / p95 s | Commits/run | Rows updated/run | Re-claims | Flags |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
        for key in sorted(k for k in groups if k[0] == workflow):
            cells = groups[key]
            good = [c for c in cells if c.get('runs_per_min') is not None]
            flags = sorted({flag for c in cells for flag in c.get('flags', [])})
            if not good:
                lines.append(f"| {key[1]} | {key[2]} | {key[3]} × 4 | {cells[0]['runs']} | unmeasured | unmeasured | unmeasured | unmeasured | unmeasured | unmeasured | {', '.join(flags)} |")
                continue
            rates = [c['runs_per_min'] for c in good]
            median = statistics.median(rates)
            if median and (max(rates) - min(rates)) / median > 0.15:
                flags.append('noisy')
            middle = sorted(good, key=lambda c: c['runs_per_min'])[len(good) // 2]
            exec_time = middle.get('claim_to_finish') or {}
            lines.append(
                f"| {key[1]} | {key[2]} | {key[3]} × 4 | {middle['runs']} | {show(median)} ({show(min(rates))}–{show(max(rates))}) | "
                f"{show(middle['drain_seconds'], 2)} | {show(exec_time.get('p50'), 3)} / {show(exec_time.get('p95'), 3)} | "
                f"{show(middle['commits_per_run'])} | {show(middle['rows_updated_per_run'])} | {sum(c['reclaims'] for c in good)} | {', '.join(flags) or '—'} |")
        lines.append('')
    lines += ['## What this does not show', '',
              '- Latency below saturation (open-loop arrival): unmeasured.',
              '- Claim latency as its own number: unmeasured (no claim timestamp column exists; queue wait in a drain is the drain order).',
              '- Behavior on several hosts, with a real model, with tools, loops, approvals or the event stream: unmeasured.',
              '- SQL statement counts on SQLite: unmeasured.',
              '- Throughput with tuned PostgreSQL settings or a database on its own host: unmeasured.', '']
    (out / 'summary.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    write_summary(sys.argv[1])
