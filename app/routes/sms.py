"""
SMS Notification Utility — Twilio
Sends order notification SMS to the business owner's forwarding number.
Danish language only.
"""

import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER

logger = logging.getLogger(__name__)


def _get_twilio_client():
    """Lazily create a Twilio client. Returns None if credentials are missing."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
        logger.warning(
            "[SMS] Twilio credentials are not fully configured. "
            "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER in .env"
        )
        return None
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def _format_order_message(order_data: dict) -> str:
    """Build a clean Danish SMS body from order data."""
    customer = order_data.get("customer_name", "Ukendt")
    phone = order_data.get("phone", "Ukendt")
    total = order_data.get("total", 0)
    items = order_data.get("items", [])

    # Format items list
    if isinstance(items, list) and items:
        items_lines = []
        for item in items:
            if isinstance(item, dict):
                qty = item.get("quantity", 1)
                name = item.get("name", "Vare")
                size = item.get("size", "")
                size_str = f" ({size})" if size else ""
                items_lines.append(f"  • {qty}x {name}{size_str}")
            else:
                items_lines.append(f"  • {item}")
        items_text = "\n".join(items_lines)
    else:
        items_text = "  • Ingen varer angivet"

    # Format total
    try:
        total_formatted = f"{float(total):.2f} kr."
    except (ValueError, TypeError):
        total_formatted = f"{total} kr."

    msg = (
        f"Ny ordre fra Foodvoice.ai\n"
        f"Kunde: {customer}\n"
        f"Tlf: {phone}\n"
        f"Varer:\n{items_text}\n"
        f"Total: {total_formatted}"
    )
    return msg


async def send_order_sms(to_number: str, order_data: dict) -> bool:
    """
    Send an SMS with order details to the given phone number.

    This function is designed to be fire-and-forget: it logs errors
    but never raises exceptions, so it cannot break the order flow.

    Returns True on success, False on failure.
    """
    try:
        client = _get_twilio_client()
        if not client:
            return False

        body = _format_order_message(order_data)

        # Twilio's client.messages.create is synchronous, but it's fast (~200ms).
        # We call it directly inside the async context; for higher throughput
        # you could wrap it in asyncio.to_thread().
        import asyncio
        message = await asyncio.to_thread(
            client.messages.create,
            body=body,
            from_=TWILIO_FROM_NUMBER,
            to=to_number,
        )

        logger.info(
            f"[SMS] ✅ Order SMS sent successfully to {to_number} "
            f"(SID: {message.sid})"
        )
        return True

    except TwilioRestException as e:
        logger.error(f"[SMS] ❌ Twilio API error sending to {to_number}: {e}")
        return False
    except Exception as e:
        logger.error(f"[SMS] ❌ Unexpected error sending SMS to {to_number}: {e}")
        return False
