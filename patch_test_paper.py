import re

with open("tests/dashboard/test_paper_warmup_thresholds.py") as f:
    content = f.read()

content = re.sub(
r"""<<<<<<< HEAD
from typing import TYPE_CHECKING

from cte.dashboard.paper_runner import _dashboard_warmup_thresholds

if TYPE_CHECKING:
    import pytest

=======
from cte.dashboard.paper_runner import _dashboard_warmup_thresholds

>>>>>>> origin/testing-improvement-edge-proof-checklist-10121990404867502383""",
"""from typing import TYPE_CHECKING

from cte.dashboard.paper_runner import _dashboard_warmup_thresholds

if TYPE_CHECKING:
    import pytest
""", content)

with open("tests/dashboard/test_paper_warmup_thresholds.py", "w") as f:
    f.write(content)
