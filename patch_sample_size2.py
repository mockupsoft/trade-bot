import re

files = ['src/cte/ops/readiness.py', 'src/cte/ops/go_no_go.py']
for filepath in files:
    with open(filepath, 'r') as f:
        content = f.read()

    lines = content.split('\n')
    new_lines = []
    in_metrics = False

    for line in lines:
        if re.match(r'^class \w+Metrics:', line) or re.match(r'^class \w+Metrics\([^)]+\):', line):
            in_metrics = True
            new_lines.append(line)
            continue

        if in_metrics and line.startswith('def '):
            in_metrics = False

        if in_metrics and re.match(r'^    \w+: \w+', line):
            if ' = 0.0' in line:
                line = line.replace(' = 0.0', '')
            elif ' = 0' in line:
                line = line.replace(' = 0', '')
            elif ' = False' in line:
                line = line.replace(' = False', '')
            elif ' = True' in line:
                line = line.replace(' = True', '')

        new_lines.append(line)

    with open(filepath, 'w') as f:
        f.write('\n'.join(new_lines))
