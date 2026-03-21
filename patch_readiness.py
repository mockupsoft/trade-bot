import os

filepath = 'src/cte/ops/readiness.py'
with open(filepath, 'r') as f:
    content = f.read()

# Add validation import and function
validation_code = """
def validate_metrics(metrics: Any) -> None:
    \"\"\"Ensure metrics have no missing or silent default values.\"\"\"
    if metrics is None:
        raise ValueError("Metrics object cannot be None")

    for field_name, field_type in metrics.__annotations__.items():
        val = getattr(metrics, field_name, None)

        # Check required fields (those without Optional/| None)
        if "None" not in str(field_type) and val is None:
            raise ValueError(f"Missing required metric: {field_name}")

        # Add sanity checks for negative values that shouldn't be negative, etc.
        # But specifically the prompt mentions "Detect suspicious defaults (e.g., 0 where not realistic)"
        # Let's add some basic sanity checks for specific fields
        if field_name == "uptime_pct" and val == 0:
            raise ValueError(f"Suspicious metric value: {field_name}={val}")
        if "latency" in field_name and val == 0:
            raise ValueError(f"Suspicious metric value: {field_name}={val}")
"""

# Insert validation code after imports
import_end = content.find("class GateStatus(StrEnum):")
if import_end != -1:
    from_typing_import = "\nfrom typing import Any\n"
    if "from typing import" not in content[:import_end]:
        content = content[:import_end] + from_typing_import + validation_code + "\n\n" + content[import_end:]
    else:
        content = content[:import_end] + validation_code + "\n\n" + content[import_end:]

with open(filepath, 'w') as f:
    f.write(content)
