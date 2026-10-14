"""
Shared pytest configuration for the langchain_learn test suite.

Ensures the project root is on sys.path so that ``import app`` and
``from skills.author_style ...`` work regardless of how pytest is invoked.
"""

import sys
import pytest
from pathlib import Path

# Add the project root to sys.path (one level up from tests/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def close_background_workers():
    yield
    module = sys.modules.get("app")
    if module:
        services = module.app.extensions.pop("job_services", {})
        for service in services.values():
            service.close()
