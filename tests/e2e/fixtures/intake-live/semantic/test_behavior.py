import unittest

from auth import login
from orders import submit_order


class ExistingBehavior(unittest.TestCase):
    def test_valid_login(self):
        self.assertTrue(login("secret"))

    def test_invalid_login(self):
        self.assertFalse(login("wrong"))

    def test_duplicate_submission(self):
        rows = []
        order = {"id": "order-1", "amount_cents": 100}
        submit_order(rows, order)
        submit_order(rows, order)
        self.assertEqual(len(rows), 1)
