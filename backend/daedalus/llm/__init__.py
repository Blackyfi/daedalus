"""OpenAI-compatible LLM client used by Argus + planning."""
from daedalus.llm.client import LLMClient, LLMError, get_llm_client

__all__ = ["LLMClient", "LLMError", "get_llm_client"]
