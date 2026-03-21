filepath = 'src/cte/ops/go_no_go.py'
with open(filepath, 'r') as f:
    content = f.read()

# we need to fix the dataclass issue: non-default argument follows default argument
lines = content.split('\n')
new_lines = []
for line in lines:
    if line.strip() == 'profit_factor: float | None = None':
        new_lines.append('    profit_factor: float | None')
    else:
        new_lines.append(line)

with open(filepath, 'w') as f:
    f.write('\n'.join(new_lines))
