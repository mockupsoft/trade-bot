import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # We missed fixing build_dashboard_paper_to_testnet_gates which takes `metrics: DashboardPaperToTestnetMetrics` directly, not Option[Metrics]

    lines = content.split('\n')
    new_lines = []

    for line in lines:
        if line.strip() == 'return [' and 'metrics.testnet_keys' in '\n'.join(lines[lines.index(line):min(lines.index(line)+20, len(lines))]):
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}if metrics is None:')
            new_lines.append(f'{indent}    raise ValueError("Metrics object cannot be None")')
            new_lines.append(f'{indent}validate_metrics(metrics)')
        new_lines.append(line)

    with open(filepath, 'w') as f:
        f.write('\n'.join(new_lines))

process_file('src/cte/ops/readiness.py')
