import re

with open("tests/ops/test_campaign.py") as f:
    content = f.read()

# We want the HEAD version everywhere
while "<<<<<<< HEAD" in content:
    content = re.sub(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)\n>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383", r"\1", content, flags=re.DOTALL)

with open("tests/ops/test_campaign.py", "w") as f:
    f.write(content)
