filepath = 'src/cte/ops/readiness.py'
with open(filepath, 'r') as f:
    content = f.read()

lines = content.split('\n')
new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if '"""Gates for the v1 dashboard: infrastructure truth + declared validation metrics (env)."""' in line:
        indent = line[:len(line) - len(line.lstrip())]
        new_lines.append(f'{indent}if metrics is None:')
        new_lines.append(f'{indent}    raise ValueError("Metrics object cannot be None")')
        new_lines.append(f'{indent}validate_metrics(metrics)')

with open(filepath, 'w') as f:
    f.write('\n'.join(new_lines))
