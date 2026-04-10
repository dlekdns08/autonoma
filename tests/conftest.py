import pytest
from autonoma.event_bus import bus

@pytest.fixture(autouse=True)
def _reset():
    bus._handlers.clear()
    yield
    bus._handlers.clear()
