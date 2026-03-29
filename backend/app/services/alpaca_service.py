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


alpaca_service = AlpacaOrderService()
