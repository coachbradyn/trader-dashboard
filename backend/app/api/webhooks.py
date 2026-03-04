from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.webhook import WebhookPayload
from app.services.trade_processor import process_webhook
from app.services.price_service import price_service

router = APIRouter()


@router.post("/webhook")
async def receive_webhook(payload: WebhookPayload, db: AsyncSession = Depends(get_db)):
    try:
        trade = await process_webhook(payload, db)

        # Register ticker for price tracking
        price_service.add_ticker(payload.ticker)

        return {
            "status": "ok",
            "trade_id": trade.id,
            "signal": payload.signal,
            "ticker": payload.ticker,
            "direction": payload.dir,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
