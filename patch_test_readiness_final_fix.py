import re

with open("tests/ops/test_ops.py") as f:
    content = f.read()

# Fix the last remaining PaperToDemoMetrics initialization that misses reconciliation_clean
content = content.replace("PaperToDemoMetrics(\n            paper_days=10, paper_trades=60, crash_free_days=10,\n            all_tests_pass=True, state_machine_violations=0,\n            api_keys_configured=True\n        )",
"PaperToDemoMetrics(paper_days=10, paper_trades=60, crash_free_days=10, reconciliation_clean=True, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)")

content = content.replace("PaperToDemoMetrics(\n            paper_days=10, paper_trades=60, crash_free_days=10,\n            reconciliation_clean=True, all_tests_pass=True,\n            state_machine_violations=0, api_keys_configured=True\n        )",
"PaperToDemoMetrics(paper_days=10, paper_trades=60, crash_free_days=10, reconciliation_clean=True, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)")

content = content.replace("PaperToDemoMetrics(paper_days=10, paper_trades=60, crash_free_days=10, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)",
"PaperToDemoMetrics(paper_days=10, paper_trades=60, crash_free_days=10, reconciliation_clean=True, all_tests_pass=True, state_machine_violations=0, api_keys_configured=True)")

content = content.replace("PaperToDemoMetrics(paper_days=3, paper_trades=10)",
"PaperToDemoMetrics(paper_days=3, paper_trades=10, crash_free_days=0, reconciliation_clean=False, all_tests_pass=False, state_machine_violations=0, api_keys_configured=False)")

with open("tests/ops/test_ops.py", "w") as f:
    f.write(content)

with open("tests/ops/test_readiness.py") as f:
    content = f.read()

# Fix the NameError 'modified_value' bug
# Let's inspect test_readiness.py to find the actual parameter names of the test function
match = re.search(r"def test_edge_proof_individual_failures\((.*?)\):", content)
if match:
    params = [p.strip() for p in match.group(1).split(",")]
    if "field" in params and "bad_value" in params:
        content = content.replace("kwargs[modified_field] = modified_value", "kwargs[field] = bad_value")

with open("tests/ops/test_readiness.py", "w") as f:
    f.write(content)
