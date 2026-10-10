import unittest

import pandas as pd

from tests.helpers import contract
from lakehouseguard.generator import generate_orders, utc
from lakehouseguard.healing import heal


class Healing(unittest.TestCase):
    def setUp(self):
        self.c = contract()
        self.df = generate_orders(30, utc(2026, 10, 10, 5), seed=5)

    def test_clean_data_is_untouched(self):
        out, actions, rows = heal(self.df, self.c)
        self.assertEqual(actions, {})
        self.assertEqual(rows, 0)
        pd.testing.assert_frame_equal(out.reset_index(drop=True), self.df.reset_index(drop=True), check_dtype=False)

    def test_trim_case_alias(self):
        df = self.df.copy()
        df["city"] = df["city"].astype(object)
        df.loc[0, "city"] = "  Pune "
        df.loc[1, "city"] = "mumbai"
        df.loc[2, "city"] = "Bangalore"
        out, actions, rows = heal(df, self.c)
        self.assertEqual(list(out["city"][:3]), ["Pune", "Mumbai", "Bengaluru"])
        self.assertEqual(actions["trim:city"], 1)
        self.assertEqual(actions["normalize:city"], 2)   # mumbai (case) + Bangalore (alias); "  Pune " only needed a trim
        self.assertEqual(rows, 3)

    def test_unknown_value_is_not_guessed(self):
        df = self.df.copy()
        df["city"] = df["city"].astype(object)
        df.loc[0, "city"] = "Atlantis"
        out, _, rows = heal(df, self.c)
        self.assertEqual(out.loc[0, "city"], "Atlantis")
        self.assertEqual(rows, 0)

    def test_currency_numbers_parsed_garbage_kept(self):
        df = self.df.copy()
        df["amount"] = df["amount"].astype(object)
        df.loc[0, "amount"] = "₹1,234.50"
        df.loc[1, "amount"] = "Rs. 99"
        df.loc[2, "amount"] = "N/A"
        out, actions, rows = heal(df, self.c)
        self.assertEqual(out.loc[0, "amount"], 1234.5)
        self.assertEqual(out.loc[1, "amount"], 99.0)
        self.assertEqual(out.loc[2, "amount"], "N/A")      # left for quarantine
        self.assertEqual(actions["numeric_parse:amount"], 2)
        self.assertEqual(rows, 2)

    def test_missing_columns_and_nulls_are_ignored(self):
        df = self.df.drop(columns=["amount"]).copy()
        df["city"] = df["city"].astype(object)
        df.loc[0, "city"] = None
        out, _, rows = heal(df, self.c)
        self.assertTrue(pd.isna(out.loc[0, "city"]))
        self.assertEqual(rows, 0)

    def test_all_null_string_column(self):
        df = self.df.copy()
        df["customer_id"] = None
        out, actions, _ = heal(df, self.c)
        self.assertNotIn("trim:customer_id", actions)


if __name__ == "__main__":
    unittest.main()
