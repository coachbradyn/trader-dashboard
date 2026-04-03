"""
Dual AI Provider System (Claude + Gemini)
==========================================
Routes AI calls to the appropriate provider based on function type.
High-stakes decisions go to Claude; high-volume lower-stakes work goes to Gemini.
Falls back to Claude if Gemini fails.
"""

import time
import logging
import asyncio
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

# Provider routing config
FUNCTION_ROUTING = {
    # Claude (high-stakes)
    "signal_evaluation": "claude",
    "scheduled_review": "claude",
    "conflict_resolution": "claude",
    "ai_portfolio_decision": "claude",
    # Gemini (high-volume, lower-stakes)
    "morning_briefing": "gemini",
    "watchlist_summary": "gemini",
    "ask_henry": "gemini",
    "screener_analysis": "gemini",
    "trade_review": "gemini",
    "memory_extraction": "gemini",
}

# Keywords that escalate Ask Henry to Claude
ESCALATION_KEYWORDS = {
    "should", "recommend", "buy", "sell", "trade",
    "position", "allocate", "rebalance", "trim", "close",
}


def _should_escalate(question: str) -> bool:
    words = set(question.lower().split())
    return bool(words & ESCALATION_KEYWORDS)


async def call_ai(
    system: str,
    prompt: str,
    function_name: str = "general",
    max_tokens: int = 1500,
    question_text: str = None,  # For escalation check on ask_henry
    enable_web_search: bool = False,
) -> str:
    """
    Route AI call to the appropriate provider based on function_name.
    Falls back to Claude if Gemini fails.
    """
    settings = get_settings()
    mode = settings.ai_routing_mode

    # Determine provider
    if mode == "claude_only" or not settings.gemini_api_key:
        provider = "claude"
    elif mode == "gemini_only":
        provider = "gemini"
    else:
        provider = FUNCTION_ROUTING.get(function_name, "claude")
        # Escalation check for ask_henry
        if function_name == "ask_henry" and question_text and _should_escalate(question_text):
            provider = "claude"
            logger.info("Escalated ask_henry to Claude (detected decision keywords)")

    # Try primary provider
    start = time.monotonic()
    was_fallback = False

    # Only enable web search on Claude calls (not Gemini — cost/latency)
    use_web_search = enable_web_search and provider == "claude"

    if provider == "gemini":
        result, model, in_tok, out_tok = await _call_gemini(system, prompt, max_tokens)
        if result is None:
            # Fallback to Claude
            logger.warning(f"Gemini failed for {function_name}, falling back to Claude")
            result, model, in_tok, out_tok = await _call_claude(system, prompt, max_tokens, web_search=enable_web_search)
            provider = "claude"
            was_fallback = True
    else:
        result, model, in_tok, out_tok = await _call_claude(system, prompt, max_tokens, web_search=use_web_search)

    latency = int((time.monotonic() - start) * 1000)

    # Log usage asynchronously
    asyncio.create_task(_log_usage(
        provider=provider,
        function_name=function_name,
        model=model or "unknown",
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_ms=latency,
        was_fallback=was_fallback,
    ))

    return result or "AI analysis temporarily unavailable."


async def _call_claude(system: str, prompt: str, max_tokens: int, web_search: bool = False) -> tuple:
    """Call Claude API using async client. Returns (text, model, input_tokens, output_tokens)."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return (None, "claude-import-error", None, None)

    MODELS = [
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    tools = None
    if web_search:
        tools = [{"type": "web_search_20260209", "name": "web_search"}]

    try:
        client = anthropic.AsyncAnthropic()
        for model in MODELS:
            try:
                kwargs = dict(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=90.0 if web_search else 60.0,
                )
                if tools:
                    kwargs["tools"] = tools

                response = await client.messages.create(**kwargs)

                # Handle web search: Claude may use web search and return multiple
                # content blocks. We need to loop if stop_reason is "tool_use" (for
                # user-defined tools), but web_search is a server-side tool — Claude
                # handles it internally. The response may include web_search_tool_result
                # blocks alongside text blocks. We just extract all text blocks.
                # If stop_reason is "pause_turn", re-send to let server continue.
                messages = kwargs["messages"]
                max_continuations = 3
                for _ in range(max_continuations):
                    if response.stop_reason == "pause_turn":
                        messages = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": response.content},
                        ]
                        response = await client.messages.create(
                            model=model,
                            max_tokens=max_tokens,
                            system=system,
                            messages=messages,
                            tools=tools,
                            timeout=60.0,
                        )
                    else:
                        break

                # Extract text from all text blocks in the response
                text_parts = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)

                result_text = "\n".join(text_parts) if text_parts else ""

                usage = response.usage
                return (
                    result_text,
                    model,
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                )
            except (anthropic.BadRequestError, anthropic.NotFoundError):
                continue
            except anthropic.AuthenticationError:
                return ("AI unavailable — invalid Claude API key.", model, None, None)
        return (None, MODELS[-1], None, None)
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        return (None, "claude-error", None, None)


async def _call_gemini(system: str, prompt: str, max_tokens: int) -> tuple:
    """Call Gemini API. Returns (text, model, input_tokens, output_tokens)."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return (None, None, None, None)

    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai package not installed, skipping Gemini")
        return (None, None, None, None)

    model_name = "gemini-2.0-flash"

    try:
        # Run in thread pool since genai may be sync, with timeout protection
        def _sync_call():
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                ),
            )
            text = response.text
            # Try to get usage metadata
            in_tok = None
            out_tok = None
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                in_tok = getattr(response.usage_metadata, 'prompt_token_count', None)
                out_tok = getattr(response.usage_metadata, 'candidates_token_count', None)
            return text, in_tok, out_tok

        text, in_tok, out_tok = await asyncio.wait_for(
            asyncio.to_thread(_sync_call), timeout=90.0
        )
        return (text, model_name, in_tok, out_tok)

    except asyncio.TimeoutError:
        logger.error("Gemini call timed out (90s)")
        return (None, "gemini-timeout", None, None)
    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        return (None, "gemini-error", None, None)


async def _log_usage(
    provider: str,
    function_name: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    latency_ms: int,
    was_fallback: bool,
):
    """Log AI usage to database asynchronously."""
    try:
        from app.database import async_session
        from app.models.ai_usage import AIUsage

        async with async_session() as db:
            usage = AIUsage(
                provider=provider,
                function_name=function_name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                was_fallback=was_fallback,
            )
            db.add(usage)
            await db.commit()
    except Exception as e:
        logger.debug(f"Failed to log AI usage: {e}")
