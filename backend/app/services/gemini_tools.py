"""
Gemini function-calling support for FMP tools.

Converts the FMP MCP tool catalog (already in Anthropic shape, served by
``fmp_mcp.get_fmp_tools``) into Google ``genai`` ``FunctionDeclaration``
objects, exposes per-Gemini-lane tool subsets, and dispatches tool calls
back through the same ``call_fmp_tool`` path Claude uses — so cache,
rate-limit, and fallback chains are inherited verbatim.

Usage from the Gemini provider path:

    from app.services.gemini_tools import get_tools_for_function, call_gemini_tool

    tools = await get_tools_for_function("morning_briefing")
    # ... pass tools=[Tool(function_declarations=tools)] to genai
    # ... when response has function_call parts, dispatch via call_gemini_tool
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Per-lane tool subsets. Each Gemini-routed function gets only what its
# lane needs — keeps prompts lean and outputs focused. Lanes not listed
# here pass no tools (synthesis-only).
GEMINI_TOOL_SETS: dict[str, set[str]] = {
    "morning_briefing": {
        "getEconomicCalendar", "getSectorPerformance",
        "getTreasuryRates", "getStockNews",
        "getStockNewsSentiment", "getEarningsCalendar",
    },
    "news_digest": {
        "getStockNews", "getStockNewsSentiment",
    },
    "upcoming_events": {
        "getEarningsCalendar", "getDividendsCalendar",
        "getStockSplitCalendar", "getIPOCalendar",
        "getEconomicCalendar",
    },
    "sector_analysis": {
        "getSectorPerformance", "getSectorPE", "getIndustryPE",
    },
    "watchlist_summary": {
        "getBatchQuotes", "getKeyMetrics", "getRatios",
        "getStockNews", "getEarningsCalendar",
    },
    "ask_henry": {
        "getQuote", "getBatchQuotes", "getKeyMetrics", "getRatios",
        "getStockNews", "getEarningsCalendar", "getSectorPerformance",
        "getTreasuryRates", "getCompanyProfile", "getPriceTarget",
    },
    "trade_review": {
        "getQuote", "getHistoricalPrice",
        "getEarningsCalendar", "getEarningsSurprises",
    },
}


_JSON_TYPE_TO_GENAI: dict[str, str] = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _coerce_schema_type(raw_type: Any) -> str | None:
    """Map a JSON-schema ``type`` value to a genai Type enum name.

    Handles unions like ``["string", "null"]`` by picking the first
    non-null concrete type. Returns None when nothing maps cleanly.
    """
    if isinstance(raw_type, list):
        for t in raw_type:
            if t and t != "null":
                mapped = _JSON_TYPE_TO_GENAI.get(t)
                if mapped:
                    return mapped
        return None
    if isinstance(raw_type, str):
        return _JSON_TYPE_TO_GENAI.get(raw_type)
    return None


def _convert_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Recursively convert a JSON-schema dict to a genai Schema dict.

    Returns None on unconvertible schemas (caller should skip the tool).
    """
    if not isinstance(schema, dict):
        return None

    raw_type = schema.get("type")
    # ``anyOf`` / ``oneOf`` — pick the first object/string variant we can
    # convert; otherwise give up.
    if raw_type is None and "anyOf" in schema:
        for variant in schema["anyOf"]:
            converted = _convert_schema(variant)
            if converted:
                return converted
        return None

    genai_type = _coerce_schema_type(raw_type)
    if genai_type is None:
        return None

    out: dict[str, Any] = {"type": genai_type}
    if "description" in schema:
        out["description"] = str(schema["description"])[:1024]
    if "enum" in schema and isinstance(schema["enum"], list):
        out["enum"] = [str(v) for v in schema["enum"]]

    if genai_type == "OBJECT":
        properties = schema.get("properties") or {}
        converted_props: dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            converted = _convert_schema(prop_schema)
            if converted is not None:
                converted_props[prop_name] = converted
        if converted_props:
            out["properties"] = converted_props
        required = schema.get("required") or []
        if isinstance(required, list):
            # Filter required to only props we successfully converted.
            kept = [r for r in required if r in (out.get("properties") or {})]
            if kept:
                out["required"] = kept

    if genai_type == "ARRAY":
        items = schema.get("items")
        if isinstance(items, dict):
            converted_items = _convert_schema(items)
            if converted_items is not None:
                out["items"] = converted_items
            else:
                # Default to string items if we can't introspect — keeps
                # the tool callable rather than dropping it entirely.
                out["items"] = {"type": "STRING"}
        else:
            out["items"] = {"type": "STRING"}

    return out


def _anthropic_tool_to_gemini(tool: dict[str, Any]) -> Any | None:
    """Convert one FMP tool dict (Anthropic shape) into a genai
    ``FunctionDeclaration``. Returns None when the schema can't be
    converted; caller logs and skips.
    """
    try:
        from google import genai  # type: ignore
    except ImportError:
        return None

    name = tool.get("name")
    if not name:
        return None

    raw_schema = tool.get("input_schema") or {"type": "object", "properties": {}}
    converted = _convert_schema(raw_schema) or {"type": "OBJECT", "properties": {}}

    try:
        return genai.types.FunctionDeclaration(
            name=name,
            description=(tool.get("description") or "")[:1024],
            parameters=converted,
        )
    except Exception as e:
        logger.warning(f"gemini_tools: could not build FunctionDeclaration for {name}: {e}")
        return None


async def get_tools_for_function(function_name: str) -> list[Any]:
    """Return ``FunctionDeclaration`` list for the given Gemini lane.

    Empty list when the lane has no tool subset configured or when the
    google-genai SDK is unavailable. Pulls the FMP tool catalog from
    ``fmp_mcp.get_fmp_tools`` (cached after first call) and filters by
    ``GEMINI_TOOL_SETS[function_name]``.
    """
    allowed = GEMINI_TOOL_SETS.get(function_name)
    if not allowed:
        return []

    try:
        from app.services.fmp_mcp import get_fmp_tools
        catalog = await get_fmp_tools()
    except Exception as e:
        logger.warning(f"gemini_tools: failed to load FMP catalog — {e}")
        return []

    declarations: list[Any] = []
    skipped: list[str] = []
    for tool in catalog:
        if tool.get("name") not in allowed:
            continue
        decl = _anthropic_tool_to_gemini(tool)
        if decl is None:
            skipped.append(tool.get("name", "?"))
        else:
            declarations.append(decl)

    missing_from_catalog = allowed - {t.get("name") for t in catalog}
    if missing_from_catalog:
        logger.warning(
            f"gemini_tools[{function_name}]: requested but not in catalog: "
            f"{sorted(missing_from_catalog)}"
        )
    if skipped:
        logger.warning(
            f"gemini_tools[{function_name}]: schema-conversion failed for: {skipped}"
        )

    return declarations


async def call_gemini_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a Gemini ``function_call`` to the FMP MCP layer.

    Returns the text payload (truncated by lane policy: 12k for
    transcripts, 4k otherwise). Errors come back as a short string the
    model can read and recover from.
    """
    try:
        from app.services.fmp_mcp import call_fmp_tool
        result_text = await call_fmp_tool(name, args or {})
    except Exception as e:
        logger.warning(f"gemini_tools: dispatch failed for {name}: {e}")
        return f"ERROR: tool {name} failed: {str(e)[:200]}"

    truncate_at = 12_000 if name == "getEarningsTranscripts" else 4_000
    return (result_text or "")[:truncate_at]
