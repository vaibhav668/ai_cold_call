from abc import ABC, abstractmethod
import httpx
from typing import List, Dict, Any, Tuple, Optional
from app.core.config import settings
from app.core.logging import logger

class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """Abstract method to retrieve chat completion turns."""
        pass

    async def _mock_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """Mock completions logic returning standard answers or tool invocation requests."""
        last_user_message = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_message = m["content"].lower()
                break
                
        if "book" in last_user_message or "schedule" in last_user_message or "appointment" in last_user_message:
            if tools:
                return None, [
                    {
                        "id": "call_mock_book_123",
                        "type": "function",
                        "function": {
                            "name": "book_appointment",
                            "arguments": '{"date": "2026-08-01", "time": "14:00"}'
                        }
                    }
                ]
                
        if "human" in last_user_message or "operator" in last_user_message or "transfer" in last_user_message:
            if tools:
                return None, [
                    {
                        "id": "call_mock_transfer_123",
                        "type": "function",
                        "function": {
                            "name": "transfer_to_human",
                            "arguments": "{}"
                        }
                    }
                ]
                
        return "Hello! I can help you book an appointment or answer questions. How can I help?", None

class GroqProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.LLM_MODEL
        self.url = "https://api.groq.com/openai/v1/chat/completions"

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        if not self.api_key or self.api_key == "test_groq_key":
            logger.warning("Groq API key missing. Returning Mock completions...")
            return await self._mock_completion(messages, tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Note: If it fails, raise Exception so the fallback manager handles it.
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"Groq error code {response.status_code}",
                    request=response.request,
                    response=response
                )
            
            data = response.json()
            choice = data["choices"][0]["message"]
            return choice.get("content"), choice.get("tool_calls")

class OpenRouterProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.api_key = settings.OPENROUTER_API_KEY
        self.model = settings.LLM_MODEL
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        if not self.api_key or self.api_key == "test_openrouter_key":
            logger.warning("OpenRouter API key missing. Returning Mock completions...")
            return await self._mock_completion(messages, tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://voice-agent-api.onrender.com",
            "X-Title": "VoiceAgent.AI"
        }
        
        # Translate Groq model parameters to standard OpenRouter model namespaces if needed
        model = self.model
        if "llama-3.1-8b" in model:
            model = "meta-llama/llama-3.1-8b-instruct"

        payload = {
            "model": model,
            "messages": messages
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"OpenRouter error code {response.status_code}",
                    request=response.request,
                    response=response
                )
            
            data = response.json()
            choice = data["choices"][0]["message"]
            return choice.get("content"), choice.get("tool_calls")

class LLMManager:
    def __init__(self) -> None:
        self.primary = GroqProvider()
        self.fallback = OpenRouterProvider()

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """Handles chat completions generation with automatic failure fallback logic."""
        try:
            logger.info("Sending prompt to Primary LLM Provider (Groq)...")
            return await self.primary.generate_completion(messages, tools)
        except Exception as e:
            logger.warning(f"Primary LLM Provider (Groq) failed: {e}. Switching to Fallback Provider (OpenRouter)...")
            try:
                return await self.fallback.generate_completion(messages, tools)
            except Exception as fe:
                logger.error(f"Fallback LLM Provider (OpenRouter) failed: {fe}")
                return "I am having trouble connecting to my brain right now. Can you repeat that?", None

# Expose LLMService name as alias to prevent breaking downstream file imports
LLMService = LLMManager
