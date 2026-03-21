with open("src/cte/ops/readiness.py") as f:
    content = f.read()

# Fix minimum trades threshold logic - 100 instead of 50
content = content.replace("status=GateStatus.PASS if m.total_trades >= 50 else GateStatus.FAIL,", "status=GateStatus.PASS if m.total_trades >= 100 else GateStatus.FAIL,")
content = content.replace("threshold=\"50\",", "threshold=\"100\",")

with open("src/cte/ops/readiness.py", "w") as f:
    f.write(content)
