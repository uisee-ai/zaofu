def transfer(state, ledger, seen, actor, allowed, key, qty):
    if actor not in allowed:
        raise PermissionError("denied")
    if key in seen:
        old_qty, result = seen[key]
        if old_qty != qty:
            raise ValueError("conflict")
        return result
    if qty <= 0 or state["source"] < qty:
        raise ValueError("quantity")
    state["source"] -= qty
    state["target"] += qty
    try:
        ledger(qty)
    except RuntimeError:
        return {"error": "ledger unavailable"}
    result = {"id": key}
    seen[key] = (qty, result)
    return result
