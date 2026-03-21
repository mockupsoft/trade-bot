import re

with open("src/cte/ops/go_no_go.py") as f:
    content = f.read()
while "<<<<<<< HEAD" in content:
    content = re.sub(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)\n>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383", r"\1", content, flags=re.DOTALL)
with open("src/cte/ops/go_no_go.py", "w") as f:
    f.write(content)
