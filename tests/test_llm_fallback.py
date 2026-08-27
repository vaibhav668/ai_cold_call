import pytest
import httpx
from typing import Tuple, Optional, List, Dict, Any, AsyncGenerator
from app.services.llm_service import LLMManager, BaseLLMProvider, GroqProvider, OpenRouterProvider

class MockSuccessProvider(BaseLLMProvider):
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.called = False

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        self.called = True
        return self.response_text, None

    async def generate_completion_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[Tuple[Optional[str], Optional[List[Dict[str, Any]]]], None]:
        self.called = True
        yield self.response_text, None


class MockFailureProvider(BaseLLMProvider):
    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        raise httpx.ConnectError("Failed to connect to primary LLM backend.")

    async def generate_completion_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[Tuple[Optional[str], Optional[List[Dict[str, Any]]]], None]:
        raise httpx.ConnectError("Failed to connect to primary LLM backend.")
        # Need a yield to make it an async generator
        yield None

@pytest.mark.anyio
async def test_llm_manager_primary_success(monkeypatch):
    manager = LLMManager()
    
    mock_groq = MockSuccessProvider("Groq response success")
    mock_openrouter = MockSuccessProvider("OpenRouter response fallback")
    
    monkeypatch.setattr(manager, "primary", mock_groq)
    monkeypatch.setattr(manager, "fallback", mock_openrouter)
    
    content, tools = await manager.generate_completion([{"role": "user", "content": "hi"}])
    
    assert content == "Groq response success"
    assert mock_groq.called is True
    assert mock_openrouter.called is False

@pytest.mark.anyio
async def test_llm_manager_fallback_flow(monkeypatch):
    manager = LLMManager()
    
    mock_groq_fail = MockFailureProvider()
    mock_openrouter_success = MockSuccessProvider("OpenRouter response fallback")
    
    monkeypatch.setattr(manager, "primary", mock_groq_fail)
    monkeypatch.setattr(manager, "fallback", mock_openrouter_success)
    
    content, tools = await manager.generate_completion([{"role": "user", "content": "hi"}])
    
    assert content == "OpenRouter response fallback"
    assert mock_openrouter_success.called is True
