from src.transfer import transfer


def present(payload):
    return "转移完成：" + payload["id"]


def transfer_and_present(state, amount):
    return present(transfer(state, amount))
