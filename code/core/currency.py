"""
Deterministic currency converter using fixed, dated exchange rates.
"""

import csv
import os
from typing import Dict, Tuple


class CurrencyConverter:
    def __init__(self, exchange_rates_path: str = "dataset/exchange_rates.csv"):
        self.rates: Dict[Tuple[str, str, str], float] = {}
        self.pair_fallback: Dict[Tuple[str, str], float] = {}
        if os.path.exists(exchange_rates_path):
            self.load_rates(exchange_rates_path)

    def load_rates(self, path: str) -> None:
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_date = row["rate_date"].strip()
                from_c = row["from_currency"].strip()
                to_c = row["to_currency"].strip()
                rate = float(row["rate"].strip())
                self.rates[(r_date, from_c, to_c)] = rate
                self.pair_fallback[(from_c, to_c)] = rate

    def convert(self, amount: float, from_curr: str, to_curr: str, settlement_date: str) -> float:
        """
        Converts an amount from from_curr to to_curr using the settlement_date rate.
        If currencies match, returns original amount.
        """
        if from_curr == to_curr or amount == 0.0:
            return amount

        key = (settlement_date, from_curr, to_curr)
        if key in self.rates:
            return amount * self.rates[key]

        # Fallback to pair rate if date not found
        pair_key = (from_curr, to_curr)
        if pair_key in self.pair_fallback:
            return amount * self.pair_fallback[pair_key]

        # In case of inverted pair (e.g. to_currency -> from_currency)
        inv_key = (settlement_date, to_curr, from_curr)
        if inv_key in self.rates and self.rates[inv_key] > 0:
            return amount / self.rates[inv_key]
        inv_pair = (to_curr, from_curr)
        if inv_pair in self.pair_fallback and self.pair_fallback[inv_pair] > 0:
            return amount / self.pair_fallback[inv_pair]

        raise ValueError(
            f"No exchange rate found for {from_curr} -> {to_curr} on date {settlement_date}"
        )
