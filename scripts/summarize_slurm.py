#!/usr/bin/env python3
"""Collect openCARP Slurm logs and project their measured loop time to 5 s."""
import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re
import statistics


def match(pattern, text):
    result = re.search(pattern, text, re.MULTILINE)
    if result is None:
        raise ValueError(f"Missing field: {pattern}")
    return result


def seconds(clock):
    hours, minutes, secs = map(int, clock.split(':'))
    return hours * 3600 + minutes * 60 + secs


def timestamp(text, action):
    # Logs use local CSCS time; require the same timezone at start and finish.
    found = match(rf'^{re.escape(action)} on (.+) (\S+)$', text)
    return datetime.strptime(found[1], '%a %d %b %Y %H:%M:%S'), found[2]


def read_log(path):
    text = path.read_text()
    job_id = int(match(r'slurm-(\d+)\.out$', path.name)[1])
    nodes = int(match(r'^nodes:\s*(\d+)', text)[1])
    tasks = int(match(r'^tasks:\s*(\d+)', text)[1])
    script = match(r'^(\S+) started on ', text)[1]
    start, start_zone = timestamp(text, script + ' started')
    end, end_zone = timestamp(text, script + ' finished')
    if start_zone != end_zone:
        raise ValueError('Start/finish timezones differ')
    job_wall = (end - start).total_seconds()
    mesh = match(r'^Reading reference mesh:\s*(.+)\.\*$', text)[1]
    progress = re.findall(
        r'^(\d+\.\d+)\s+(\d+\.\d+)\s+[\d.]+\s+(\d+:\d+:\d+)\s+\d+:\d+:\d+\s*$',
        text, re.MULTILINE,
    )
    if not progress or float(progress[-1][1]) != 100:
        raise ValueError('No completed simulation progress record')
    simulated_ms = float(progress[-1][0])
    if float(progress[0][0]) != 0 or simulated_ms <= 0:
        raise ValueError('Expected a simulation starting at zero')
    loop_wall = seconds(progress[-1][2])
    if not 0 < loop_wall <= job_wall:
        raise ValueError('Inconsistent job/loop wall times')
    row = dict(job_id=job_id, mesh=mesh, nodes=nodes, tasks=tasks,
               simulated_ms=simulated_ms, job_wall_s=job_wall,
               loop_wall_s=loop_wall, nonloop_wall_s=job_wall-loop_wall)
    for title, prefix in [('Electrics', 'electric'), ('Myocard Ionics', 'ionic')]:
        values = match(
            rf'^{title}:\s*Init:\s*([\d.]+) sec\s*Compute:\s*([\d.]+) sec\s*Output:\s*([\d.]+) sec', text)
        for key, value in zip(['init_s', 'compute_s', 'output_s'], values.groups()):
            row[f'{prefix}_{key}'] = float(value)
    row['projected_5s_loop_s'] = loop_wall * 5000 / simulated_ms
    row['projected_5s_wall_s'] = row['nonloop_wall_s'] + row['projected_5s_loop_s']
    row['projected_5s_node_hours'] = nodes * row['projected_5s_wall_s'] / 3600
    row['tag_region_empty'] = bool(re.search(r'Tag region \d+ is empty', text))
    row['region_warnings'] = 'One or more configured regions are empty' in text
    row['ionic_review'] = nodes >= 2  # User flagged these runs; not an automatic diagnosis.
    row['source_log'] = str(path)
    return row


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def clock(seconds_value):
    minutes = int(seconds_value / 60 + 0.5)
    return f'{minutes // 60}h:{minutes % 60:02d}m'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('exp'))
    args = parser.parse_args()
    paths = sorted(args.directory.glob('slurm-*.out'))
    if not paths:
        parser.error('No slurm-*.out files found')
    rows = []
    for path in paths:
        try:
            rows.append(read_log(path))
        except ValueError as error:
            parser.error(f'{path}: {error}')
    baselines = {}
    for mesh in {row['mesh'] for row in rows}:
        candidates = [row['projected_5s_wall_s'] for row in rows
                      if row['mesh'] == mesh and row['tasks'] == 1 and row['nodes'] == 1]
        if not candidates:
            parser.error(f'No single-task baseline for {mesh}')
        baselines[mesh] = statistics.mean(candidates)
    for row in rows:
        row['speedup'] = baselines[row['mesh']] / row['projected_5s_wall_s']
        row['efficiency'] = row['speedup'] / row['tasks']
    rows.sort(key=lambda row: (row['mesh'], row['tasks'], row['job_id']))
    groups = defaultdict(list)
    for row in rows:
        groups[(row['mesh'], row['nodes'], row['tasks'])].append(row)
    summary = []
    for (mesh, nodes, tasks), runs in sorted(groups.items()):
        record = dict(mesh=mesh, nodes=nodes, tasks=tasks, repeats=len(runs))
        for key in ['electric_compute_s', 'ionic_compute_s', 'projected_5s_wall_s',
                    'projected_5s_node_hours', 'job_wall_s', 'loop_wall_s']:
            record[f'{key}_mean'] = statistics.mean(run[key] for run in runs)
            record[f'{key}_sd'] = statistics.stdev(run[key] for run in runs) if len(runs) > 1 else 0
        record['speedup'] = baselines[mesh] / record['projected_5s_wall_s_mean']
        record['efficiency'] = record['speedup'] / tasks
        record['job_ids'] = ';'.join(str(run['job_id']) for run in runs)
        summary.append(record)
    write_csv(args.directory / 'scaling_runs.csv', rows)
    write_csv(args.directory / 'scaling_summary.csv', summary)

    latex = [
        '% Requires booktabs and siunitx. Electric/Ionic columns are measured compute seconds.',
        '% Every run starts at 0 and completes 100 ms of simulated time.',
        '% Projected 5-sec WT = (logged job wall - logged loop wall) + loop wall * 5000 / simulated_ms.',
        '% Thus startup/cleanup is included once; the full logged loop (including output) is scaled.',
        '% Loop/job timestamps have one-second resolution; all projections assume constant average cost.',
        '% Speed-up baseline: mean projected 5-sec WT of the single-task runs ON THE SAME MESH.',
        '% Efficiency = speed-up/tasks. 5-sec NH = allocated nodes * projected WT / 3600.',
        '% Tasks are measured; one GPU/task is assumed from the supplied Slurm configuration.',
        '% Printed Electrics compute EXCLUDES Ionic compute (Electrics::output_timings subtracts it).',
        '% All runs warn about empty/unassigned regions; 225-um runs also report all four tagreg lists empty.',
        '% Eight-task/two-node ionic timings are flagged by the user for later review; not excluded.',
        '% Solver settings/ionic targets are not printed and cannot be independently confirmed from these logs.',
    ]
    for mesh in sorted(baselines):
        latex += [f'% Mesh: {mesh}', r'\begin{tabular}{rrrrrrrrr}', r'\toprule',
                  r'\textbf{Nodes} & \textbf{GPUs/tasks} & \textbf{Electric} & \textbf{Ionic} & \textbf{5-sec WT} & \textbf{Speed-up} & \textbf{Efficiency} & \textbf{5-sec NH} & \textbf{JobID} \\',
                  r'\midrule']
        previous_tasks = None
        for row in [r for r in rows if r['mesh'] == mesh]:
            if previous_tasks is not None and row['tasks'] != previous_tasks:
                latex.append(r'\addlinespace')
            if row['ionic_review']:
                latex.append('% Ionic timing requires review (two-node run).')
            latex.append(
                f"{row['nodes']} & {row['tasks']} & \\SI{{{row['electric_compute_s']:.2f}}}{{\\s}} & "
                f"\\SI{{{row['ionic_compute_s']:.2f}}}{{\\s}} & {clock(row['projected_5s_wall_s'])} & "
                f"{row['speedup']:.2f} & {row['efficiency']:.2f} & {row['projected_5s_node_hours']:.2f} & {row['job_id']} " + r'\\')
            previous_tasks = row['tasks']
        latex += [r'\bottomrule', r'\end{tabular}', '']
    (args.directory / 'scaling_tables.tex').write_text('\n'.join(latex))
    print(f'Parsed {len(rows)} completed runs into {len(summary)} configurations.')
    for row in summary:
        print(Path(row['mesh']).name, row['nodes'], row['tasks'], row['repeats'],
              f"E={row['electric_compute_s_mean']:.2f}, I={row['ionic_compute_s_mean']:.2f}, "
              f"WT={clock(row['projected_5s_wall_s_mean'])}, speedup={row['speedup']:.2f}, "
              f"eff={row['efficiency']:.2f}, NH={row['projected_5s_node_hours_mean']:.2f}")


if __name__ == '__main__':
    main()
