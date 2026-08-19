import pytest


@pytest.fixture(scope="session", autouse=True)
def _configure_asyncio():
    """pytest-asyncio 默认使用 auto mode"""
    pass
