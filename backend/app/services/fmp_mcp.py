"""
FMP MCP Client
==============
Connects to Financial Modeling Prep's remote MCP server and exposes
its tools for use in Henry's Claude API calls.

The FMP MCP server provides 253+ financial tools (quotes, technicals,
fundamentals, news, SEC filings) via the Model Context Protocol. By
wiring these as tools on Claude's API calls, Henry gets structured
financial data directly — no web scraping, no fragile regex parsing.

Usage:
    from app.services.fmp_mcp import get_fmp_tools, call_fmp_tool

    # Get Anthropic-formatted tool definitions
    tools = await get_fmp_tools()

    # Execute a tool call from Claude's response
    result = await call_fmp_tool("getQuote", {"symbol": "AAPL"})
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_cached_tools: list[dict] | None = None
_cache_lock = asyncio.Lock()

# Tools Henry actually needs — requesting all 253 would bloat the prompt.
# These cover: quotes, technicals, screening, fundamentals, news.
TOOL_ALLOWLIST: set[str] = {
    # Quotes
    "getQuote", "getBatchQuotes", "getQuoteShort",
    # Technicals
    "getSMA", "getEMA", "getRSI", "getADX", "getStandardDeviation",
    # Screening & search
    "stockScreener", "searchSymbol",
    # Fundamentals
    "getCompanyProfile", "getKeyMetrics", "getRatios",
    "getIncomeStatement",
    # News & filings
    "getStockNews", "getStockNewsSentiment",
    "getLatestFinancialFilings", "getLatest8KFilings",
    # Price history
    "getHistoricalPrice",
    # Earnings
    "getEarningsCalendar", "getEarningsSurprises",
    # Analyst
    "getPriceTarget", "getUpgradesDowngrades",
    # Valuation & quality (added for scheduled_review / signal_evaluation /
    # ai_portfolio_decision / conflict_resolution lanes)
    "getDCF", "getLeveredDCF", "getOwnerEarnings",
    "getPiotroskiScore", "getAltmanZScore",
    # Statement depth
    "getBalanceSheet", "getCashFlowStatement",
    # Context
    "getEarningsTranscripts", "getCompanyPeers", "getTreasuryRates",
    # Gemini-lane additions (calendars, sectors, macro)
    # — used by morning_briefing / news_digest / upcoming_events /
    #   sector_analysis / ask_henry. Missing names surface as a startup
    #   WARN log so renames are caught immediately.
    "getEconomicCalendar",
    "getDividendsCalendar", "getStockSplitCalendar", "getIPOCalendar",
    "getSectorPerformance", "getSectorPE", "getIndustryPE",
}


def _fmp_mcp_url() -> str:
    settings = get_settings()
    key = settings.fmp_api_key
    if not key:
        raise RuntimeError("FMP_API_KEY not set")
    return f"https://financialmodelingprep.com/mcp?apikey={key}"


def _mcp_tool_to_anthropic(tool) -> dict:
    """Convert an MCP tool definition to Anthropic API tool format."""
    input_schema = {"type": "object", "properties": {}, "required": []}
    if tool.inputSchema:
        schema = tool.inputSchema
        if isinstance(schema, dict):
            input_schema = schema
        else:
            input_schema = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)

    return {
        "name": tool.name,
        "description": (tool.description or "")[:1024],
        "input_schema": input_schema,
    }


async def get_fmp_tools(force_refresh: bool = False) -> list[dict]:
    """Return FMP tools in Anthropic API format. Cached after first call."""
    global _cached_tools

    async with _cache_lock:
        if _cached_tools is not None and not force_refresh:
            return _cached_tools

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        url = _fmp_mcp_url()

        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()

                tools = []
                discovered_names: set[str] = set()
                for tool in response.tools:
                    discovered_names.add(tool.name)
                    if tool.name in TOOL_ALLOWLIST:
                        tools.append(_mcp_tool_to_anthropic(tool))

                missing = TOOL_ALLOWLIST - discovered_names
                if missing:
                    logger.warning(
                        f"FMP MCP: {len(missing)} allowlisted tools missing from catalog: "
                        f"{sorted(missing)}"
                    )

                async with _cache_lock:
                    _cached_tools = tools

                logger.info(
                    f"FMP MCP: discovered {len(response.tools)} tools, "
                    f"allowlisted {len(tools)}"
                )
                return tools

    except ImportError:
        logger.warning("FMP MCP: mcp package not installed (pip install mcp)")
        return []
    except Exception as e:
        logger.error(f"FMP MCP: failed to connect — {e}")
        return []


async def call_fmp_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Execute a single FMP MCP tool call and return the text result."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        url = _fmp_mcp_url()

        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)

                parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        parts.append(content.text)
                    elif hasattr(content, "data"):
                        parts.append(json.dumps(content.data))

                text = "\n".join(parts) if parts else "{}"
                logger.debug(f"FMP MCP {tool_name}: {len(text)} chars")
                return text

    except Exception as e:
        logger.error(f"FMP MCP tool {tool_name} failed: {e}")
        return json.dumps({"error": str(e)})


async def call_fmp_tools_batch(
    calls: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Execute multiple FMP MCP tool calls in a single connection."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        url = _fmp_mcp_url()
        results: list[str] = []

        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                for tool_name, arguments in calls:
                    try:
                        result = await session.call_tool(tool_name, arguments=arguments)
                        parts = []
                        for content in result.content:
                            if hasattr(content, "text"):
                                parts.append(content.text)
                            elif hasattr(content, "data"):
                                parts.append(json.dumps(content.data))
                        results.append("\n".join(parts) if parts else "{}")
                    except Exception as e:
                        logger.warning(f"FMP MCP batch: {tool_name} failed — {e}")
                        results.append(json.dumps({"error": str(e)}))

        return results

    except Exception as e:
        logger.error(f"FMP MCP batch failed: {e}")
        return [json.dumps({"error": str(e)})] * len(calls)
