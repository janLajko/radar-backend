def __getattr__(name: str):
    if name == "AnthropicProvider":
        from radar_backend.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "OpenAIProvider":
        from radar_backend.llm.providers.openai import OpenAIProvider

        return OpenAIProvider
    raise AttributeError(name)


__all__ = ["AnthropicProvider", "OpenAIProvider"]
