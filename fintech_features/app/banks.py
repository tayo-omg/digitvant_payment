"""The bank list, kept in memory. Refresh it with: python -m app.banks institutions.json"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Bank

_SEED = Path(__file__).with_name("banks_seed.json")


@dataclass(frozen=True)
class BankInfo:
    code: str
    nip_code: str | None
    name: str
    kind: str
    check_digit: bool
    aliases: tuple[str, ...]


_by_code: dict[str, BankInfo] = {}
_code_for: dict[str, str] = {}  # code or NIP code -> code
name_index: dict[str, list[str]] = {}  # any bank name -> codes
longest_name = 0
name_words: list[str] = []


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def reload(db: Session) -> None:
    global _by_code, _code_for, name_index, longest_name, name_words
    rows = db.scalars(select(Bank).where(Bank.active.is_(True)).order_by(Bank.name)).all()
    _by_code = {r.code: BankInfo(r.code, r.nip_code, r.name, r.kind, r.check_digit,
                                 tuple(r.aliases or ())) for r in rows}
    _code_for = {**{b.nip_code: c for c, b in _by_code.items() if b.nip_code}, **{c: c for c in _by_code}}
    index: dict[str, list[str]] = {}
    for b in _by_code.values():
        for name in {normalise(n) for n in (b.name, *b.aliases)} - {""}:
            index.setdefault(name, []).append(b.code)
    name_index = index
    longest_name = max((len(n.split()) for n in index), default=0)
    name_words = sorted({w for n in index for w in n.split() if len(w) >= 5})


def load(db: Session, rows: list[dict]) -> None:
    """Save the given list. Banks not in it are switched off, not deleted."""
    seen = set()
    for r in rows:
        bank = db.get(Bank, r["code"]) or Bank(code=r["code"])
        bank.nip_code = r.get("nip_code")
        bank.name = r["name"]
        bank.kind = r.get("kind", "bank")
        bank.check_digit = r.get("check_digit", len(r["code"]) == 3)
        bank.aliases = r.get("aliases", [])
        bank.active = True
        db.add(bank)
        seen.add(r["code"])
    for bank in db.scalars(select(Bank).where(Bank.code.not_in(seen))):
        bank.active = False
    db.commit()
    reload(db)


def ensure_loaded(db: Session) -> None:
    if db.scalar(select(Bank.code).limit(1)) is None:
        load(db, json.loads(_SEED.read_text())["banks"])
    else:
        reload(db)


def all_banks() -> list[BankInfo]:
    return list(_by_code.values())


def get(code: str | None) -> BankInfo | None:
    return _by_code.get(_code_for.get(code or "", ""))


def out(bank: BankInfo) -> dict:
    return {"code": bank.code, "name": bank.name}


if __name__ == "__main__":
    from .db import Base, SessionLocal, engine
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        load(db, json.loads(Path(sys.argv[1]).read_text())["banks"])
        print(f"Loaded {len(_by_code)} active banks")
