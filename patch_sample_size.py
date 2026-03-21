import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Find dataclass declarations
    blocks = re.findall(r'@dataclass\(frozen=True\)\nclass \w+Metrics:.*?((?=\n@dataclass|\n\ndef |\Z))', content, re.DOTALL)

    for block in blocks:
        new_block = block

        # Replace types with unsafe defaults with required ones
        lines = new_block.split('\n')
        new_lines = []
        for line in lines:
            if re.match(r'^\s+\w+: \w+ = 0\.0$', line):
                new_lines.append(line.replace(' = 0.0', ''))
            elif re.match(r'^\s+\w+: \w+ = 0$', line):
                new_lines.append(line.replace(' = 0', ''))
            elif re.match(r'^\s+\w+: \w+ = False$', line):
                new_lines.append(line.replace(' = False', ''))
            elif re.match(r'^\s+\w+: \w+ = True$', line):
                new_lines.append(line.replace(' = True', ''))
            else:
                new_lines.append(line)

        content = content.replace(block, '\n'.join(new_lines))

    with open(filepath, 'w') as f:
        f.write(content)

process_file('src/cte/ops/readiness.py')
process_file('src/cte/ops/go_no_go.py')
