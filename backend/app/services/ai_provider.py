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
    "price_targets_gemini": "gemini",
    "bull_bear_thesis": "gemini",
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

    # Single diagnostic line per call — shows in Railway logs exactly which
    # provider was chosen, and whether web search was requested. Cheap to
    # keep at INFO; makes "why is Claude getting everything?" trivial to
    # answer with one grep.
    logger.info(
        f"AI route: {function_name} → {provider} "
        f"(mode={mode}, web_search={enable_web_search})"
    )

    # Try primary provider
    start = time.monotonic()
    was_fallback = False

    if provider == "gemini":
        result, model, in_tok, out_tok = await _call_gemini(
            system, prompt, max_tokens, web_search=enable_web_search
        )
        if result is None:
            # Fallback to Claude — preserve the caller's web_search intent.
            logger.warning(f"Gemini failed for {function_name}, falling back to Claude")
            result, model, in_tok, out_tok = await _call_claude(
                system, prompt, max_tokens, web_search=enable_web_search
            )
            provider = "claude"
            was_fallback = True
    else:
        result, model, in_tok, out_tok = await _call_claude(
            system, prompt, max_tokens, web_search=enable_web_search
        )

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

    # Prompt caching: wrap the system prompt in a single cached block so the
    # same prefix across calls (base prompt + strategies + memories that
    # haven't changed) is billed at ~10% of input token cost after the first
    # hit. Requires the system param to be a list of content blocks, not a
    # plain string. Blocks with cache_control must be >=1024 tokens for Sonnet
    # — our system prompt easily clears that once strategies + memories land.
    settings = get_settings()
    use_cache = getattr(settings, "prompt_cache_enabled", True) and not web_search
    if use_cache:
        system_param = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_param = system

    try:
        client = anthropic.AsyncAnthropic()
        for model in MODELS:
            try:
                kwargs = dict(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=60.0 if web_search else 45.0,
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
                max_continuations = 1
                for _ in range(max_continuations):
                    if response.stop_reason == "pause_turn":
                        messages = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": response.content},
                        ]
                        response = await client.messages.create(
                            model=model,
                            max_tokens=max_tokens,
                            system=system_param,
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


async def _call_gemini(
    system: str, prompt: str, max_tokens: int, web_search: bool = False
) -> tuple:
    """Call Gemini API. Returns (text, model, input_tokens, output_tokens).

    Walks a fallback chain of models (GEMINI_MODEL + GEMINI_FALLBACK_MODELS)
    so a deprecated or rate-limited slug on the primary doesn't silently
    route everything to Claude.

    When `web_search=True`, attaches Google Search grounding to the request
    so briefings, ask-henry, and other high-volume calls get fresh market
    context without paying Claude rates. Grounding is a first-class Gemini
    feature (no extra latency / cost on 2.0+ Flash).
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.warning("Gemini skipped: GEMINI_API_KEY is empty")
        return (None, None, None, None)

    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai package not installed, skipping Gemini")
        return (None, None, None, None)

    models = [settings.gemini_model] + [
        m.strip() for m in (settings.gemini_fallback_models or "").split(",") if m.strip()
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    models = [m for m in models if not (m in seen or seen.add(m))]

    last_err: Exception | None = None
    for model_name in models:
        try:
            def _sync_call(mn=model_name):
                client = genai.Client(api_key=settings.gemini_api_key)
                tools = (
                    [genai.types.Tool(google_search=genai.types.GoogleSearch())]
                    if web_search else []
                )
                response = client.models.generate_content(
                    model=mn,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=max_tokens,
                        temperature=0.7,
                        tools=tools,
                    ),
                )
                # response.text can raise when content is blocked by safety
                # filters — surface that so we try the next model.
                try:
                    text = response.text
                except Exception as e:
                    raise RuntimeError(f"response.text raised: {e}") from e
                in_tok = None
                out_tok = None
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    in_tok = getattr(response.usage_metadata, "prompt_token_count", None)
                    out_tok = getattr(response.usage_metadata, "candidates_token_count", None)
                return text, in_tok, out_tok

            text, in_tok, out_tok = await asyncio.wait_for(
                asyncio.to_thread(_sync_call), timeout=90.0
            )
            if text is None:
                logger.warning(f"Gemini {model_name} returned None text; trying next model")
                continue
            return (text, model_name, in_tok, out_tok)

        except asyncio.TimeoutError:
            logger.error(f"Gemini {model_name} timed out (90s)")
            last_err = asyncio.TimeoutError()
            continue
        except Exception as e:
            last_err = e
            logger.warning(f"Gemini {model_name} failed: {str(e)[:200]}")
            continue

    logger.error(f"Gemini exhausted all models {models}: last error {last_err}")
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
