import pytest
from x99toolkit import fpt

@pytest.fixture(autouse=True)
def no_unit_test_elevation(monkeypatch):
    # Unit tests never ask for UAC. The real transport test calls the helper directly.
    monkeypatch.setattr(fpt, 'needs_elevation', lambda: False)
