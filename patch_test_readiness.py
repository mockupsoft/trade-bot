with open("tests/ops/test_readiness.py") as f:
    content = f.read()

content = content.replace("PerformanceMetrics", "EdgeProofMetrics")

with open("tests/ops/test_readiness.py", "w") as f:
    f.write(content)
