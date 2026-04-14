import logging
import asyncio

logger = logging.getLogger(__name__)


class AlpacaOrderService:
    """Handles order execution via Alpaca API for per-portfolio trading."""

    async def get_client(self, api_key: str, secret_key: str, paper: bool = True):
        """Create an Alpaca trading client for the given credentials."""
        try:
            from alpaca.trading.client import TradingClient
            return TradingClient(api_key, secret_key, paper=paper)
        except ImportError:
            logger.error("alpaca-py not installed")
            return None

    async def test_connection(self, api_key: str, secret_key: str, paper: bool = True) -> dict:
        """Test Alpaca credentials and return account info."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return {"status": "error", "message": "alpaca-py not installed"}

            def _get_account():
                account = client.get_account()
                return {
                    "status": "connected",
                    "account_id": str(account.id),
                    "buying_power": float(account.buying_power),
                    "equity": float(account.equity),
                    "cash": float(account.cash),
                    "portfolio_value": float(account.portfolio_value),
                    "paper": paper,
                }

            return await asyncio.to_thread(_get_account)
        except Exception as e:
            return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"}

    async def get_account_info(self, api_key: str, secret_key: str, paper: bool = True) -> dict:
        """Get current account balance and positions."""
        return await self.test_connection(api_key, secret_key, paper)

    async def submit_order(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        ticker: str,
        qty: float,
        side: str,  # "buy" or "sell"
        max_order_amount: float = None,
    ) -> dict:
        """Submit a market order to Alpaca."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return {"status": "error", "message": "alpaca-py not installed"}

            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

            def _submit():
                order_data = MarketOrderRequest(
                    symbol=ticker,
                    qty=qty,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                )
                order = client.submit_order(order_data)
                return {
                    "status": "submitted",
                    "order_id": str(order.id),
                    "symbol": order.symbol,
                    "qty": str(order.qty),
                    "side": str(order.side),
                    "type": str(order.type),
                    "time_in_force": str(order.time_in_force),
                    "order_status": str(order.status),
                    "submitted_at": str(order.submitted_at),
                    "paper": paper,
                }

            return await asyncio.to_thread(_submit)
        except Exception as e:
            logger.error(f"Alpaca order failed: {e}")
            return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"}

    async def get_order_status(self, api_key: str, secret_key: str, paper: bool, order_id: str) -> dict:
        """Check order fill status."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return {"status": "error", "message": "alpaca-py not installed"}

            def _check():
                order = client.get_order_by_id(order_id)
                result = {
                    "order_id": str(order.id),
                    "status": str(order.status),
                    "symbol": order.symbol,
                    "qty": str(order.qty),
                    "side": str(order.side),
                }
                if order.filled_avg_price:
                    result["filled_price"] = float(order.filled_avg_price)
                if order.filled_qty:
                    result["filled_qty"] = float(order.filled_qty)
                if order.filled_at:
                    result["filled_at"] = str(order.filled_at)
                return result

            return await asyncio.to_thread(_check)
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    async def get_positions(self, api_key: str, secret_key: str, paper: bool) -> list[dict]:
        """Get all current positions from Alpaca account."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return []

            def _get():
                positions = client.get_all_positions()
                return [
                    {
                        "symbol": p.symbol,
                        "qty": float(p.qty),
                        "side": str(p.side),
                        "avg_entry_price": float(p.avg_entry_price),
                        "current_price": float(p.current_price),
                        "market_value": float(p.market_value),
                        "unrealized_pl": float(p.unrealized_pl),
                        "unrealized_plpc": float(p.unrealized_plpc),
                    }
                    for p in positions
                ]

            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.error(f"Failed to get Alpaca positions: {e}")
            return []

    # ── Options ──────────────────────────────────────────────────────
    #
    # Alpaca's trading API accepts options orders on the same TradingClient
    # once the account is approved for options.  Single-leg orders use
    # LimitOrderRequest; multi-leg spreads use the mleg ("multi-leg")
    # endpoint which alpaca-py exposes via OptionMultiLegOrderRequest
    # (SDK 0.28+).  We always use limit orders for options — market orders
    # on options blow through wide spreads.

    async def submit_options_order(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        option_symbol: str,
        qty: int,
        side: str,  # "buy" or "sell"
        limit_price: float | None = None,
    ) -> dict:
        """Submit a single-leg options limit order."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return {"status": "error", "message": "alpaca-py not installed"}
            if qty <= 0:
                return {"status": "error", "message": "qty must be positive"}
            if limit_price is None or limit_price <= 0:
                return {"status": "error", "message": "limit_price required for options orders"}

            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

            def _submit():
                req = LimitOrderRequest(
                    symbol=option_symbol,
                    qty=qty,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(float(limit_price), 2),
                )
                order = client.submit_order(req)
                return {
                    "status": "submitted",
                    "order_id": str(order.id),
                    "symbol": order.symbol,
                    "qty": str(order.qty),
                    "side": str(order.side),
                    "limit_price": float(limit_price),
                    "order_status": str(order.status),
                    "submitted_at": str(order.submitted_at),
                    "paper": paper,
                }

            return await asyncio.to_thread(_submit)
        except Exception as e:
            logger.error(f"Alpaca options order failed: {e}")
            return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"}

    async def submit_multi_leg_order(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        legs: list[dict],  # [{"option_symbol", "qty", "side"}]
        limit_price: float,  # net debit (positive) or credit (negative)
    ) -> dict:
        """Submit a multi-leg options order (spread/condor) as a single
        atomic ticket.  Each leg = {option_symbol, qty, side}.
        """
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return {"status": "error", "message": "alpaca-py not installed"}
            if not legs or len(legs) < 2:
                return {"status": "error", "message": "multi-leg order requires >=2 legs"}

            # alpaca-py exposes multi-leg order requests differently across
            # minor versions — try the common names in sequence.
            order_req_cls = None
            try:
                from alpaca.trading.requests import OptionLegRequest
                try:
                    from alpaca.trading.requests import LimitOrderRequest as _LOR
                    order_req_cls = _LOR  # accepts `order_class=mleg` + legs
                except Exception:
                    pass
            except ImportError:
                return {
                    "status": "error",
                    "message": "alpaca-py in this environment does not support multi-leg options orders",
                }

            from alpaca.trading.enums import (
                OrderSide, TimeInForce, PositionIntent, OrderClass,
            )
            from alpaca.trading.requests import OptionLegRequest

            def _side(s: str):
                return OrderSide.BUY if s.lower() == "buy" else OrderSide.SELL

            def _intent(s: str):
                # Opening a long leg = BUY_TO_OPEN; short leg = SELL_TO_OPEN.
                # Closing flows go through a separate path and would pass
                # different intents.
                return (
                    PositionIntent.BUY_TO_OPEN
                    if s.lower() == "buy"
                    else PositionIntent.SELL_TO_OPEN
                )

            def _submit():
                mleg = [
                    OptionLegRequest(
                        symbol=leg["option_symbol"],
                        ratio_qty=int(leg["qty"]),
                        side=_side(leg["side"]),
                        position_intent=_intent(leg["side"]),
                    )
                    for leg in legs
                ]
                req = order_req_cls(
                    qty=1,  # ratio_qty on legs expresses quantity
                    limit_price=round(float(limit_price), 2),
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.MLEG,
                    legs=mleg,
                )
                order = client.submit_order(req)
                return {
                    "status": "submitted",
                    "order_id": str(order.id),
                    "order_status": str(order.status),
                    "limit_price": float(limit_price),
                    "legs": [
                        {"option_symbol": leg["option_symbol"], "qty": leg["qty"], "side": leg["side"]}
                        for leg in legs
                    ],
                    "paper": paper,
                }

            return await asyncio.to_thread(_submit)
        except Exception as e:
            logger.error(f"Alpaca multi-leg options order failed: {e}")
            return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"}

    async def get_options_positions(self, api_key: str, secret_key: str, paper: bool) -> list[dict]:
        """Get current options positions from Alpaca (for reconciliation)."""
        try:
            client = await self.get_client(api_key, secret_key, paper)
            if not client:
                return []

            def _get():
                positions = client.get_all_positions()
                out: list[dict] = []
                for p in positions:
                    # Alpaca marks options via asset_class='us_option' — tolerate
                    # attribute vs dict access across SDK versions.
                    ac = getattr(p, "asset_class", None)
                    ac_str = str(ac) if ac is not None else ""
                    if "option" not in ac_str.lower():
                        # Also some versions report the Alpaca OCC-style symbol
                        # with a length > 15; fall back to that.
                        if len(getattr(p, "symbol", "") or "") < 15:
                            continue
                    out.append({
                        "symbol": p.symbol,
                        "qty": float(p.qty),
                        "side": str(p.side),
                        "avg_entry_price": float(p.avg_entry_price),
                        "current_price": float(p.current_price) if getattr(p, "current_price", None) else None,
                        "market_value": float(p.market_value) if getattr(p, "market_value", None) else None,
                        "unrealized_pl": float(p.unrealized_pl) if getattr(p, "unrealized_pl", None) else None,
                    })
                return out

            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.error(f"Failed to get Alpaca options positions: {e}")
            return []


alpaca_service = AlpacaOrderService()
