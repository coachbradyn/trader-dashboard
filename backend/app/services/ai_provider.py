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
from typing import Any, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

# Provider routing config
FUNCTION_ROUTING = {
    # Claude (high-stakes)
    "signal_evaluation": "claude",
    "scheduled_review": "claude",
    "conflict_resolution": "claude",
    "ai_portfolio_decision": "claude",
    "cluster_labeling": "claude",
    # Gemini (high-volume, lower-stakes)
    "morning_briefing": "gemini",
    "watchlist_summary": "gemini",
    "ask_henry": "gemini",
    "screener_analysis": "gemini",
    "trade_review": "gemini",
    "memory_extraction": "gemini",
    "price_targets_gemini": "gemini",
    "bull_bear_thesis": "gemini",
    # Homepage surfaces (Gemini with FMP function-calling)
    "news_digest": "gemini",
    "upcoming_events": "gemini",
    "sector_analysis": "gemini",
}

# Keywords that escalate Ask Henry to Claude
ESCALATION_KEYWORDS = {
    "should", "recommend", "buy", "sell", "trade",
    "position", "allocate", "rebalance", "trim", "close",
}


def _should_escalate(question: str) -> bool:
    words = set(question.lower().split())
    return bool(words & ESCALATION_KEYWORDS)


# Gemini needs lower temperature for structured JSON output. Functions
# that return strict JSON get 0.3; everything else uses the default 0.7.
_GEMINI_TEMPERATURE: dict[str, float] = {
    "price_targets_gemini": 0.3,
    "bull_bear_thesis": 0.4,
    "screener_analysis": 0.5,
    "memory_extraction": 0.4,
    "news_digest": 0.4,
    "upcoming_events": 0.3,
    "sector_analysis": 0.5,
}

# Functions that should use Gemini's native JSON mode
# (response_mime_type: application/json). This forces the decoder to
# only emit valid JSON — no markdown, no prose, no citations.
_GEMINI_JSON_MODE: set[str] = {
    "memory_extraction",
}


async def call_ai(
    system: str,
    prompt: str,
    function_name: str = "general",
    max_tokens: int = 1500,
    question_text: str = None,  # For escalation check on ask_henry
    enable_web_search: bool = False,
    enable_fmp_tools: bool = False,
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
        gemini_temp = _GEMINI_TEMPERATURE.get(function_name)
        gemini_json = function_name in _GEMINI_JSON_MODE
        result, model, in_tok, out_tok = await _call_gemini(
            system, prompt, max_tokens, web_search=enable_web_search,
            temperature=gemini_temp, json_mode=gemini_json,
            fmp_tools=enable_fmp_tools, function_name=function_name,
        )
        if result is None:
            logger.warning(f"Gemini failed for {function_name}, falling back to Claude")
            result, model, in_tok, out_tok = await _call_claude(
                system, prompt, max_tokens, web_search=enable_web_search,
                fmp_tools=enable_fmp_tools,
            )
            provider = "claude"
            was_fallback = True
    else:
        result, model, in_tok, out_tok = await _call_claude(
            system, prompt, max_tokens, web_search=enable_web_search,
            fmp_tools=enable_fmp_tools,
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


async def _call_claude(
    system: str, prompt: str, max_tokens: int,
    web_search: bool = False, fmp_tools: bool = False,
) -> tuple:
    """Call Claude API using async client. Returns (text, model, input_tokens, output_tokens).

    When ``fmp_tools=True``, FMP MCP tools are added to the request and
    the function loops on ``stop_reason="tool_use"`` — executing each
    tool call via the FMP MCP client and feeding results back to Claude
    so it can incorporate live financial data into its response.
    """
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

    tools = []
    if web_search:
        tools.append({"type": "web_search_20260209", "name": "web_search"})

    fmp_tool_names: set[str] = set()
    if fmp_tools:
        try:
            from app.services.fmp_mcp import get_fmp_tools
            fmp_defs = await get_fmp_tools()
            for td in fmp_defs:
                tools.append(td)
                fmp_tool_names.add(td["name"])
            if fmp_defs:
                logger.info(f"Claude call: {len(fmp_defs)} FMP tools attached")
        except Exception as e:
            logger.warning(f"FMP tools unavailable: {e}")

    settings = get_settings()
    use_cache = getattr(settings, "prompt_cache_enabled", True) and not (web_search or fmp_tools)
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
                messages = [{"role": "user", "content": prompt}]
                timeout = 90.0 if fmp_tools else (60.0 if web_search else 45.0)
                kwargs = dict(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=messages,
                    timeout=timeout,
                )
                if tools:
                    kwargs["tools"] = tools

                response = await client.messages.create(**kwargs)
                total_in = getattr(response.usage, "input_tokens", 0) or 0
                total_out = getattr(response.usage, "output_tokens", 0) or 0

                # Tool-use loop: when Claude calls FMP tools, execute them
                # and feed results back. Max 8 iterations to bound cost.
                max_tool_rounds = 8
                for _ in range(max_tool_rounds):
                    if response.stop_reason == "tool_use":
                        tool_results = []
                        for block in response.content:
                            if block.type == "tool_use" and block.name in fmp_tool_names:
                                from app.services.fmp_mcp import call_fmp_tool
                                result_text = await call_fmp_tool(block.name, block.input or {})
                                truncate_at = 12_000 if block.name == "getEarningsTranscripts" else 4_000
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text[:truncate_at],
                                })
                                logger.info(f"FMP tool: {block.name}({block.input}) → {len(result_text)} chars")

                        if not tool_results:
                            break

                        messages = messages + [
                            {"role": "assistant", "content": response.content},
                            {"role": "user", "content": tool_results},
                        ]
                        response = await client.messages.create(
                            model=model,
                            max_tokens=max_tokens,
                            system=system_param,
                            messages=messages,
                            tools=tools,
                            timeout=timeout,
                        )
                        total_in += getattr(response.usage, "input_tokens", 0) or 0
                        total_out += getattr(response.usage, "output_tokens", 0) or 0

                    elif response.stop_reason == "pause_turn":
                        messages = messages + [
                            {"role": "assistant", "content": response.content},
                        ]
                        response = await client.messages.create(
                            model=model,
                            max_tokens=max_tokens,
                            system=system_param,
                            messages=messages,
                            tools=tools,
                            timeout=timeout,
                        )
                        total_in += getattr(response.usage, "input_tokens", 0) or 0
                        total_out += getattr(response.usage, "output_tokens", 0) or 0
                    else:
                        break

                text_parts = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)

                result_text = "\n".join(text_parts) if text_parts else ""
                return (result_text, model, total_in, total_out)

            except (anthropic.BadRequestError, anthropic.NotFoundError):
                continue
            except anthropic.AuthenticationError:
                return ("AI unavailable — invalid Claude API key.", model, None, None)
        return (None, MODELS[-1], None, None)
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        return (None, "claude-error", None, None)


async def _call_gemini(
    system: str, prompt: str, max_tokens: int, web_search: bool = False,
    temperature: float | None = None, json_mode: bool = False,
    fmp_tools: bool = False, function_name: str | None = None,
) -> tuple:
    """Call Gemini API. Returns (text, model, input_tokens, output_tokens).

    Walks a fallback chain of models (GEMINI_MODEL + GEMINI_FALLBACK_MODELS)
    so a deprecated or rate-limited slug on the primary doesn't silently
    route everything to Claude.

    When `web_search=True`, attaches Google Search grounding to the request
    so briefings, ask-henry, and other high-volume calls get fresh market
    context without paying Claude rates. Grounding is a first-class Gemini
    feature (no extra latency / cost on 2.0+ Flash).

    When ``fmp_tools=True`` and ``function_name`` is one of the lanes
    configured in ``gemini_tools.GEMINI_TOOL_SETS``, the matching FMP
    function declarations are attached and the response is processed in
    a tool-use loop (max 8 rounds), dispatching each ``function_call``
    back through ``call_gemini_tool``.
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

    function_declarations: list[Any] = []
    if fmp_tools and function_name:
        from app.services.gemini_tools import get_tools_for_function
        function_declarations = await get_tools_for_function(function_name)
        if function_declarations:
            logger.info(
                f"Gemini call ({function_name}): "
                f"{len(function_declarations)} FMP tools attached"
            )

    models = [settings.gemini_model] + [
        m.strip() for m in (settings.gemini_fallback_models or "").split(",") if m.strip()
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    models = [m for m in models if not (m in seen or seen.add(m))]

    def _build_tools():
        out = []
        if web_search:
            out.append(genai.types.Tool(google_search=genai.types.GoogleSearch()))
        if function_declarations:
            out.append(genai.types.Tool(function_declarations=function_declarations))
        return out

    last_err: Exception | None = None
    for model_name in models:
        try:
            tool_objs = _build_tools()
            config_kwargs = {
                "system_instruction": system,
                "max_output_tokens": max_tokens,
                "temperature": temperature if temperature is not None else 0.7,
                "tools": tool_objs,
            }
            # JSON mode forces the model to output valid JSON at the
            # decoding level — no markdown fences, no prose. Incompatible
            # with both grounding (citations) and function-calling
            # (function_call parts), so only enable when neither is on.
            if json_mode and not web_search and not function_declarations:
                config_kwargs["response_mime_type"] = "application/json"

            client = genai.Client(api_key=settings.gemini_api_key)
            config = genai.types.GenerateContentConfig(**config_kwargs)

            contents: list[Any] = [
                genai.types.Content(
                    role="user",
                    parts=[genai.types.Part.from_text(text=prompt)],
                )
            ]

            total_in = 0
            total_out = 0
            text_result: str | None = None
            max_tool_rounds = 8

            for round_idx in range(max_tool_rounds + 1):
                def _sync_call(mn=model_name, ctx=contents, cfg=config):
                    return client.models.generate_content(
                        model=mn,
                        contents=ctx,
                        config=cfg,
                    )

                response = await asyncio.wait_for(
                    asyncio.to_thread(_sync_call), timeout=90.0
                )

                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    total_in += getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                    total_out += getattr(response.usage_metadata, "candidates_token_count", 0) or 0

                candidate = (response.candidates or [None])[0]
                parts = (candidate.content.parts if candidate and candidate.content else None) or []

                function_calls = []
                text_chunks = []
                for part in parts:
                    fc = getattr(part, "function_call", None)
                    if fc and getattr(fc, "name", None):
                        function_calls.append(fc)
                    elif getattr(part, "text", None):
                        text_chunks.append(part.text)

                if not function_calls:
                    text_result = "".join(text_chunks) if text_chunks else None
                    if text_result is None:
                        # Fallback for safety-blocked / empty responses
                        try:
                            text_result = response.text
                        except Exception:
                            text_result = None
                    break

                if round_idx == max_tool_rounds:
                    logger.warning(
                        f"Gemini {model_name} hit max tool rounds ({max_tool_rounds}); "
                        f"returning partial text"
                    )
                    text_result = "".join(text_chunks) if text_chunks else None
                    break

                # Append model turn (with function_call parts), then dispatch
                # each tool and append their function_response parts as a
                # single user turn.
                contents.append(candidate.content)

                from app.services.gemini_tools import call_gemini_tool
                response_parts = []
                for fc in function_calls:
                    args = dict(fc.args) if getattr(fc, "args", None) else {}
                    result_text = await call_gemini_tool(fc.name, args)
                    logger.info(
                        f"Gemini FMP tool: {fc.name}({args}) → {len(result_text)} chars"
                    )
                    response_parts.append(
                        genai.types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result_text},
                        )
                    )

                contents.append(
                    genai.types.Content(role="user", parts=response_parts)
                )

            if text_result is None:
                logger.warning(f"Gemini {model_name} returned no text; trying next model")
                continue
            return (text_result, model_name, total_in or None, total_out or None)

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
