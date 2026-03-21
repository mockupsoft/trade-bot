
with open("src/cte/ops/readiness.py") as f:
    content = f.read()

content = content.replace(
"""            name="paper_trade_count", category="validation",
            description="≥50 paper / simulated trades in active epoch (analytics journal)",
            status=GateStatus.PASS if m.paper_trades >= 50 else GateStatus.FAIL,
            value=str(m.paper_trades), threshold="100",
        ),""",
"""            name="paper_trade_count", category="validation",
            description="≥50 paper / simulated trades in active epoch (analytics journal)",
            status=GateStatus.PASS if m.paper_trades >= 50 else GateStatus.FAIL,
            value=str(m.paper_trades), threshold="50",
        ),""")

content = content.replace(
"""            name="demo_trade_count", category="validation",
            description="Demo trading executed ≥50 distinct trades",
            status=GateStatus.PASS if m.demo_trades >= 50 else GateStatus.FAIL,
            value=str(m.demo_trades), threshold="100",
        ),""",
"""            name="demo_trade_count", category="validation",
            description="Demo trading executed ≥50 distinct trades",
            status=GateStatus.PASS if m.demo_trades >= 50 else GateStatus.FAIL,
            value=str(m.demo_trades), threshold="50",
        ),""")

content = content.replace(
"""        ReadinessGate(
            name="paper_trade_count",
            category="validation",
            description="≥50 paper / simulated trades in active epoch (analytics journal)",
            status=GateStatus.PASS if metrics.paper_trades >= 50 else GateStatus.FAIL,
            value=str(metrics.paper_trades),
            threshold="100",
        ),""",
"""        ReadinessGate(
            name="paper_trade_count",
            category="validation",
            description="≥50 paper / simulated trades in active epoch (analytics journal)",
            status=GateStatus.PASS if metrics.paper_trades >= 50 else GateStatus.FAIL,
            value=str(metrics.paper_trades),
            threshold="50",
        ),""")

content = content.replace(
"""        ReadinessGate(
            name="demo_trade_count",
            category="validation",
            description="Demo trading executed ≥50 distinct trades",
            status=GateStatus.SKIP,
            value="—",
            threshold="100",
            detail=_PHASE5_SKIP_DETAIL,
        ),""",
"""        ReadinessGate(
            name="demo_trade_count",
            category="validation",
            description="Demo trading executed ≥50 distinct trades",
            status=GateStatus.SKIP,
            value="—",
            threshold="50",
            detail=_PHASE5_SKIP_DETAIL,
        ),""")

with open("src/cte/ops/readiness.py", "w") as f:
    f.write(content)
