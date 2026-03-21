with open("src/cte/ops/readiness.py") as f:
    content = f.read()

# Add total_trades to EdgeProofMetrics
content = content.replace("class EdgeProofMetrics:\n    expectancy_overall", "class EdgeProofMetrics:\n    total_trades: int = 0\n    expectancy_overall")

# Add sample_size gate to build_edge_proof_checklist
gate_str = """    return [
        ReadinessGate(
            name="sample_size", category="edge_stability",
            description="Minimum number of trades completed",
            status=GateStatus.PASS if m.total_trades >= 50 else GateStatus.FAIL,
            value=str(m.total_trades), threshold="50",
        ),
        # ── Edge Stability ────────────────────────────────────"""
content = content.replace("    return [\n        # ── Edge Stability ────────────────────────────────────", gate_str)

with open("src/cte/ops/readiness.py", "w") as f:
    f.write(content)
