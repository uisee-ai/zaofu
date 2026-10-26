import unittest

from counter import advance


class CounterTests(unittest.TestCase):
    def test_default_step(self):
        self.assertEqual(advance(4), 5)
