"""Shared fixtures for accessibility tests.

Delegates to the common conftest for TestClient setup.
"""

from __future__ import annotations

# Re-export shared fixtures from tests/conftest.py
# pytest automatically discovers fixtures in parent conftest files,
# so dashboard_client and page_urls are available without explicit import.
