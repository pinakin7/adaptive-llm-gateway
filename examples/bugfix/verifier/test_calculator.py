import unittest

from calculator import add


class IndependentCalculatorTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(9, 4), 13)

    def test_negative(self):
        self.assertEqual(add(-3, 8), 5)
