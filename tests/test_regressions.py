import pytest

from app.adaptive import AdaptiveAgent
from app.host_registry import HostAnalysisRegistry
from app.local_registry import LocalCrawlerRegistry


def test_adaptive_agent_has_registries():
    agent = AdaptiveAgent()
    assert agent.registry is not None
    assert agent.local_registry is not None


@pytest.mark.asyncio
async def test_existing_host_is_rejected(tmp_path):
    registry = HostAnalysisRegistry(str(tmp_path / "generated"))
    local = LocalCrawlerRegistry(str(tmp_path / "generated"))
    registry.mark("https://example.com/article/1", "abc123", "playwright")
    agent = AdaptiveAgent(registry=registry, local_registry=local)
    with pytest.raises(RuntimeError, match="HOST_ALREADY_ANALYZED"):
        await agent.inspect("https://example.com/article/2", {"title": ""})
