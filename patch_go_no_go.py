import os

filepath = 'src/cte/ops/go_no_go.py'
with open(filepath, 'r') as f:
    content = f.read()

validation_code = """
from typing import Any

def validate_metrics(metrics: Any) -> None:
    \"\"\"Ensure metrics have no missing or silent default values.\"\"\"
    if metrics is None:
        raise ValueError("Metrics object cannot be None")

    for field_name, field_type in metrics.__annotations__.items():
        val = getattr(metrics, field_name, None)

        # Check required fields (those without Optional/| None)
        if "None" not in str(field_type) and val is None:
            raise ValueError(f"Missing required metric: {field_name}")

        if field_name == "uptime_pct" and val == 0:
            raise ValueError(f"Suspicious metric value: {field_name}={val}")
        if "latency" in field_name and val == 0:
            raise ValueError(f"Suspicious metric value: {field_name}={val}")
"""

import_end = content.find("@dataclass(frozen=True)\nclass ReportSection:")
if import_end != -1:
    content = content[:import_end] + validation_code + "\n\n" + content[import_end:]

with open(filepath, 'w') as f:
    f.write(content)
