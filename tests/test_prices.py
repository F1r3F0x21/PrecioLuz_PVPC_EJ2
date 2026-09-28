import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import main


class NormalizePricesTests(unittest.TestCase):
    def test_normalizes_and_sorts_hourly_values(self):
        payload = [
            {"hour": "01-02", "price": "0,18"},
            {"hour": "00-01", "price": 0.12},
            {"hour": "02-03", "price": "not a price"},
        ]

        self.assertEqual(
            main.normalize_prices(payload),
            [{"hour": "00-01", "price": 0.12}, {"hour": "01-02", "price": 0.18}],
        )

    def test_accepts_prices_wrapper(self):
        self.assertEqual(
            main.normalize_prices({"prices": [{"hour": "00-01", "price": 0.1}]}),
            [{"hour": "00-01", "price": 0.1}],
        )

    def test_normalizes_ree_pvpc_series_and_converts_mwh_to_kwh(self):
        payload = {
            "included": [
                {
                    "type": "PVPC",
                    "attributes": {
                        "values": [
                            {"datetime": "2026-09-28T00:00:00.000+02:00", "value": 122.56},
                            {"datetime": "2026-09-28T01:00:00.000+02:00", "value": -0.5},
                        ]
                    },
                },
                {"type": "Precio mercado spot", "attributes": {"values": []}},
            ]
        }

        self.assertEqual(
            main.normalize_prices(payload),
            [{"hour": "00-01", "price": 0.12256}, {"hour": "01-02", "price": -0.0005}],
        )


class CacheTests(unittest.TestCase):
    def test_second_read_uses_sqlite_without_fetching_again(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "prices.sqlite3"
            with patch.object(main, "DATABASE_PATH", database):
                main.init_db()
                requested_day = date(2026, 1, 2)
                expected = [{"hour": "00-01", "price": 0.15}]
                with patch.object(main, "fetch_prices", return_value=expected) as fetch:
                    self.assertEqual(main.get_day_prices(requested_day), (expected, False))
                    self.assertEqual(main.get_day_prices(requested_day), (expected, True))
                fetch.assert_called_once_with(requested_day)


class WeeklyPricesTests(unittest.TestCase):
    def test_collects_seven_days_and_marks_their_extremes(self):
        selected_day = date(2026, 1, 7)

        def cached_prices(day):
            offset = (day - selected_day).days
            return [
                {"hour": "00-01", "price": float(offset)},
                {"hour": "23-24", "price": float(offset + 1)},
            ], True

        with patch.object(main, "get_day_prices", side_effect=cached_prices):
            report = main.collect_prices(selected_day)

        self.assertEqual(len(report["week"]), 14)
        self.assertEqual(report["extremes"]["minimum"]["day"], "2026-01-01")
        self.assertEqual(report["extremes"]["maximum"]["day"], "2026-01-07")


class PdfTests(unittest.TestCase):
    def test_export_contains_a_pdf_document(self):
        day = "2026-01-02"
        report = {
            "date": day,
            "source": "Test source",
            "prices": [{"day": day, "hour": "00-01", "price": 0.1}],
            "week": [{"day": day, "hour": "00-01", "price": 0.1}],
            "extremes": {
                "minimum": {"day": day, "hour": "00-01", "price": 0.1},
                "maximum": {"day": day, "hour": "00-01", "price": 0.1},
            },
        }

        self.assertTrue(main.create_pdf(report).read().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()