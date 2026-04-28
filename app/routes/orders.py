from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db, Order
from app.auth import get_current_user

router = APIRouter()

@router.get("/api/orders")
async def get_orders(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Fetch all orders from the database, ordered by latest."""
    try:
        orders = db.query(Order).order_by(Order.id.asc()).all()
        return {"orders": [
            {
                "id": o.id,
                "name": str(o.name or "Unknown"),
                "phone": str(o.phone or "N/A"),
                "order": str(o.order or "[]"),
                "total": float(o.total or 0),
                "call_id": str(o.call_id or ""),
                "created_at": o.created_at.isoformat() if o.created_at else ""
            } for o in orders
        ], "total": len(orders)}
    except Exception as e:
        print(f"[BACKEND ERROR] {e}")
        return {"orders": [], "total": 0, "error": str(e)}

@router.delete("/api/orders/{order_id}")
async def delete_order(order_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Delete a specific order by ID."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()
    return {"success": True}
