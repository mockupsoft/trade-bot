import re

with open("src/cte/signals/composite.py") as f:
    content = f.read()

# We want to keep the unrolled summation (micro-optimization from HEAD)
content = re.sub(
r"""<<<<<<< HEAD
    # Micro-optimization: Unroll summation for known fixed keys to avoid generator overhead.
    primary = \(
        momentum.score \* w\["momentum"\]
        \+ orderflow.score \* w\["orderflow"\]
        \+ liquidation.score \* w\["liquidation"\]
        \+ microstructure.score \* w\["microstructure"\]
        \+ cross_venue.score \* w\["cross_venue"\]
    \)

=======
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""    # Micro-optimization: Unroll summation for known fixed keys to avoid generator overhead.
    primary = (
        momentum.score * w["momentum"]
        + orderflow.score * w["orderflow"]
        + liquidation.score * w["liquidation"]
        + microstructure.score * w["microstructure"]
        + cross_venue.score * w["cross_venue"]
    )
""", content)

content = re.sub(
r"""<<<<<<< HEAD
=======

    primary = sum\(sub_scores\[k\] \* w\[k\] for k in sub_scores\)
>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""", "", content)

with open("src/cte/signals/composite.py", "w") as f:
    f.write(content)
