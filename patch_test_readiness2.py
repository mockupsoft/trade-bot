with open("tests/ops/test_readiness.py") as f:
    content = f.read()

content = content.replace(", min_trades=100", "")

with open("tests/ops/test_readiness.py", "w") as f:
    f.write(content)
