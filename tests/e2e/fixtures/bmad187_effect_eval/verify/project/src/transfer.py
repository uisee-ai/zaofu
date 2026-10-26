def transfer(state, amount, *, fail_ledger=False):
    state["source"] -= amount
    state["destination"] += amount
    if fail_ledger:
        raise RuntimeError("ledger unavailable")
    state["ledger"].append(amount)
    return {"transfer_id": "TX-7", "ok": True}
