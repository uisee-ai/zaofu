"""TR-BOOTSTRAP-FEATURE-001 (doc 42 §2.9) — first-run bootstrap feature.

`zf init` calls :func:`install_bootstrap_feature` to create the
``F-zaofu-bootstrap`` Feature + 4 guided Tasks + ``.zf/bootstrap.md`` so
new users see multi-agent activity on their very first ``zf start``.
"""

from zf.core.bootstrap.feature_template import (
    BOOTSTRAP_FEATURE_DESCRIPTION,
    BOOTSTRAP_FEATURE_ID,
    BOOTSTRAP_FEATURE_TITLE,
)
from zf.core.bootstrap.installer import install_bootstrap_feature
from zf.core.bootstrap.task_templates import BOOTSTRAP_TASKS

__all__ = [
    "BOOTSTRAP_FEATURE_ID",
    "BOOTSTRAP_FEATURE_TITLE",
    "BOOTSTRAP_FEATURE_DESCRIPTION",
    "BOOTSTRAP_TASKS",
    "install_bootstrap_feature",
]
