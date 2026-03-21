import re

with open("src/cte/ops/readiness.py") as f:
    content = f.read()

# Replace all conflicts with HEAD (main branch dataclass logic) except we need to add the sample_size gate to EdgeProofMetrics
while "<<<<<<< HEAD" in content:
    content = re.sub(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)\n>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383", r"\1", content, flags=re.DOTALL)

with open("src/cte/ops/readiness.py", "w") as f:
    f.write(content)
