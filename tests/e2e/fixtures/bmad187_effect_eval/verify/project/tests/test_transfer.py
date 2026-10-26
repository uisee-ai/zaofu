import unittest

from src.presenter import present
from src.query import total
from src.transfer import transfer


class TransferTests(unittest.TestCase):
    def test_transfer_success(self):
        state = {"source": 10, "destination": 0, "ledger": []}
        result = transfer(state, 3)
        self.assertTrue(result["ok"])
        self.assertEqual(state["source"], 7)

    def test_present_known_payload(self):
        self.assertEqual(present({"id": "TX-7"}), "转移完成：TX-7")

    def test_total_existing_behavior(self):
        self.assertEqual(total({"source": 7, "destination": 3}), 10)
