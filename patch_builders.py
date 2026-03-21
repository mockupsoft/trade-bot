import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Find all build_* functions
    # E.g., def build_paper_to_demo_checklist(metrics: PaperToDemoMetrics | None = None) -> list[ReadinessGate]:
    # We replace `m = metrics or ...` with:
    # `if metrics is None: raise ValueError("Metrics object cannot be None")`
    # `validate_metrics(metrics)`
    # `m = metrics`

    lines = content.split('\n')
    new_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)

        # Match function definition start
        if line.startswith('def build_') and 'metrics' in line:
            # Skip until we hit the first line of the function body
            while not line.endswith('):') and not line.endswith('] :') and not line.endswith('] :') and not '->' in line and i < len(lines) - 1:
                i += 1
                line = lines[i]
                new_lines.append(line)

            # Now we are at the definition end, e.g., `) -> list[ReadinessGate]:` or similar
            # Check the next few lines for `m = metrics or`
            j = i + 1
            while j < len(lines) and (lines[j].strip().startswith('"""') or lines[j].strip() == '' or lines[j].strip().startswith('#')):
                new_lines.append(lines[j])
                j += 1

            i = j - 1
            # Found where code should start
        elif line.strip().startswith('m = metrics or ') or line.strip().startswith('m = metrics or\n'):
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.pop() # remove this line
            new_lines.append(f'{indent}if metrics is None:')
            new_lines.append(f'{indent}    raise ValueError("Metrics object cannot be None")')
            new_lines.append(f'{indent}validate_metrics(metrics)')
            new_lines.append(f'{indent}m = metrics')
        i += 1

    with open(filepath, 'w') as f:
        f.write('\n'.join(new_lines))

process_file('src/cte/ops/readiness.py')
process_file('src/cte/ops/go_no_go.py')
