"""Unified LLM client — routes to Anthropic (primary) or OpenAI (fallback)."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Maximum time (seconds) to wait for a single LLM call before giving up
_LLM_TIMEOUT = 120

# Track which provider is active (sticky after first successful call)
_active_provider: str | None = None
_anthropic_failed: bool = False


async def llm_complete(
    prompt: str,
    api_key_anthropic: str,
    api_key_openai: str,
    model_anthropic: str = "claude-sonnet-4-5-20250929",
    model_openai: str = "gpt-4o",
    max_tokens: int = 5000,
    temperature: float = 0,
) -> str:
    """Send a prompt to an LLM and return the response text.

    Tries Anthropic first. If Anthropic returns a billing/auth error (400/401/402),
    falls back to OpenAI for this call AND all future calls in this session.
    """
    global _active_provider, _anthropic_failed

    # If Anthropic hasn't permanently failed, try it first
    if not _anthropic_failed and api_key_anthropic:
        try:
            return await asyncio.wait_for(
                _call_anthropic(
                    prompt, api_key_anthropic, model_anthropic, max_tokens, temperature,
                ),
                timeout=_LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Anthropic call timed out after %ds", _LLM_TIMEOUT)
            if api_key_openai:
                logger.info("Falling back to OpenAI for this call")
            else:
                raise RuntimeError(f"Anthropic LLM call timed out after {_LLM_TIMEOUT}s")
        except _AnthropicBillingError:
            logger.warning("Anthropic billing error — switching to OpenAI for all future calls")
            _anthropic_failed = True
        except Exception as e:
            logger.error("Anthropic error: %s", e)
            # For non-billing errors, still try OpenAI as fallback
            if api_key_openai:
                logger.info("Falling back to OpenAI for this call")
            else:
                raise

    # Fallback to OpenAI
    if api_key_openai:
        if _active_provider != "openai":
            _active_provider = "openai"
            logger.info("Using OpenAI (%s) for LLM extraction", model_openai)
        return await asyncio.wait_for(
            _call_openai(
                prompt, api_key_openai, model_openai, max_tokens, temperature,
            ),
            timeout=_LLM_TIMEOUT,
        )

    raise RuntimeError(
        "No LLM provider available. Both Anthropic (credit balance too low) "
        "and OpenAI (no OPENAI_API_KEY set) are unavailable.\n"
        "Add OPENAI_API_KEY to your .env file as a fallback."
    )


class _AnthropicBillingError(Exception):
    """Raised when Anthropic returns a billing/credit error."""


async def _call_anthropic(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call Anthropic Claude API."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        global _active_provider
        if _active_provider != "anthropic":
            _active_provider = "anthropic"
        return response.content[0].text
    except anthropic.APIStatusError as e:
        # Check for billing/credit errors
        if e.status_code in (400, 401, 402):
            msg = str(e).lower()
            if "credit" in msg or "balance" in msg or "billing" in msg:
                raise _AnthropicBillingError(str(e)) from e
        raise


async def _call_openai(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call OpenAI API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def get_active_provider() -> str:
    """Return the currently active LLM provider name."""
    return _active_provider or "anthropic"


def reset_provider_state() -> None:
    """Reset provider state (for testing)."""
    global _active_provider, _anthropic_failed
    _active_provider = None
    _anthropic_failed = False
