"""Ollama / OpenAI API ?????????"""

from .api_client_openai import _ApiClientOpenAIMixin
from .api_client_ollama import _ApiClientOllamaMixin
from .api_client_quick_welfare import QuickWelfareMixin


class _ApiClientMixin(_ApiClientOpenAIMixin, _ApiClientOllamaMixin, QuickWelfareMixin):
    """?? API ???????OpenAI ?? + Ollama??"""
