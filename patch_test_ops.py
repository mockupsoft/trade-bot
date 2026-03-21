import re

with open("tests/ops/test_ops.py") as f:
    content = f.read()

# We want the HEAD version everywhere except for adding `total_trades=100` to EdgeProofMetrics since we added that gate
while "<<<<<<< HEAD" in content:
    content = re.sub(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)\n>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383", r"\1", content, flags=re.DOTALL)

# Add total_trades=100 to EdgeProofMetrics calls
content = content.replace("expectancy_overall=15.0,", "expectancy_overall=15.0, total_trades=100,")
content = content.replace("expectancy_overall=10.0, positive_regime_count=3,", "expectancy_overall=10.0, positive_regime_count=3, total_trades=100,")
content = content.replace("expectancy_overall=10.0, positive_regime_count=1,", "expectancy_overall=10.0, positive_regime_count=1, total_trades=100,")

with open("tests/ops/test_ops.py", "w") as f:
    f.write(content)
