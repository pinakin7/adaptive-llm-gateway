import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(add(2, 3), 5)
