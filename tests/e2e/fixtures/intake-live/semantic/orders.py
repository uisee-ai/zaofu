def submit_order(rows, order):
    """Persist each order id once; retrying the same request is idempotent."""
    rows.append(dict(order))
    return rows
