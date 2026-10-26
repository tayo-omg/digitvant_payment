from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import Transaction


def get_owned_txn(db: Session, txn_id: str, user_id: str) -> Transaction:
    txn = db.get(Transaction, txn_id)
    if not txn or txn.user_id != user_id:  # same 404 either way, no ID probing
        raise HTTPException(404, "Transaction not found")
    return txn
