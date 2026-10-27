import unittest
from transfer import transfer

class TransferTests(unittest.TestCase):
    def test_retry_keeps_one_change(self):
        state, seen, rows = {"source": 10, "target": 0}, {}, []
        first = transfer(state, rows.append, seen, "alice", {"alice"}, "k", 3)
        self.assertEqual(transfer(state, rows.append, seen, "alice", {"alice"}, "k", 3), first)
        self.assertEqual((state, rows), ({"source": 7, "target": 3}, [3]))
    def test_denied_is_unchanged(self):
        state, seen, rows = {"source": 10, "target": 0}, {}, []
        with self.assertRaises(PermissionError):
            transfer(state, rows.append, seen, None, {"alice"}, "k", 3)
        self.assertEqual((state, rows), ({"source": 10, "target": 0}, []))
