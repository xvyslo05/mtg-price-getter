import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cardmarket_price_fill as script


class CardmarketPriceFillTests(unittest.TestCase):
    def test_build_price_map_prefers_avg7_columns(self):
        rows = [
            {"idProduct": "10", "avg7": "1.11", "avg7-foil": "2.22", "avg": "9.99"},
            {"idProduct": "11", "avg7": "3.33", "avg7-foil": ""},
        ]
        headers = ["idProduct", "avg7", "avg7-foil", "avg"]

        price_map = script._build_price_map(rows, headers)

        # Keep this structural: prices should come from avg7 columns, not exact market values.
        self.assertTrue(price_map["10"]["avg"])
        self.assertTrue(price_map["10"]["avg_foil"])
        self.assertNotEqual(price_map["10"]["avg"], rows[0]["avg"])
        self.assertTrue(price_map["11"]["avg"])

    def test_match_product_ids_uses_collector_number(self):
        products = [
            {"idProduct": "100", "name": "Emptiness", "Expansion": "Lorwyn Eclipsed", "Number": "222"},
            {"idProduct": "101", "name": "Emptiness", "Expansion": "Lorwyn Eclipsed", "Number": "294"},
        ]
        headers = ["idProduct", "name", "Expansion", "Number"]
        _, index = script._build_product_index(products, headers)

        row = {
            "Name": "Emptiness",
            "Set name": "Lorwyn Eclipsed",
            "Collector number": "294",
            "Foil": "normal",
        }

        product_ids = script._match_product_ids(
            row,
            index,
            name_col="Name",
            set_col="Set name",
            num_col="Collector number",
            foil_col="Foil",
        )

        self.assertEqual(product_ids, ["101"])

    def test_main_with_scryfall_bulk_sets_type_and_foil_price(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            collection = tmp / "collection.csv"
            products = tmp / "products.json"
            prices = tmp / "prices.json"
            bulk = tmp / "scryfall_bulk.json"
            output = tmp / "out.csv"

            collection.write_text(
                "Name,Set code,Set name,Collector number,Foil,Scryfall ID\n"
                "Emptiness,ECL,Lorwyn Eclipsed,222,normal,id-normal\n"
                "Emptiness,ECL,Lorwyn Eclipsed,294,normal,id-borderless\n"
                "\"Oko, Lorwyn Liege // Oko, Shadowmoor Scion\",ECL,Lorwyn Eclipsed,61,foil,id-oko\n",
                encoding="utf-8",
            )

            products.write_text(
                json.dumps(
                    {
                        "products": [
                            {"idProduct": 100, "name": "Emptiness", "idExpansion": 1},
                            {"idProduct": 101, "name": "Oko, Lorwyn Liege // Oko, Shadowmoor Scion", "idExpansion": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            prices.write_text(
                json.dumps(
                    {
                        "priceGuides": [
                            {"idProduct": 100, "avg7": 7.02, "avg7-foil": 7.20},
                            {"idProduct": 101, "avg7": 5.26, "avg7-foil": 5.39},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            bulk.write_text(
                json.dumps(
                    [
                        {
                            "id": "id-normal",
                            "cardmarket_id": 100,
                            "collector_number": "222",
                            "set": "ecl",
                            "set_name": "Lorwyn Eclipsed",
                            "name": "Emptiness",
                            "border_color": "black",
                            "full_art": False,
                            "promo_types": [],
                            "frame_effects": [],
                            "finishes": ["nonfoil", "foil"],
                        },
                        {
                            "id": "id-borderless",
                            "cardmarket_id": 100,
                            "collector_number": "294",
                            "set": "ecl",
                            "set_name": "Lorwyn Eclipsed",
                            "name": "Emptiness",
                            "border_color": "borderless",
                            "full_art": True,
                            "promo_types": [],
                            "frame_effects": ["inverted"],
                            "finishes": ["nonfoil", "foil"],
                        },
                        {
                            "id": "id-oko",
                            "cardmarket_id": 101,
                            "collector_number": "61",
                            "set": "ecl",
                            "set_name": "Lorwyn Eclipsed",
                            "name": "Oko, Lorwyn Liege // Oko, Shadowmoor Scion",
                            "border_color": "black",
                            "full_art": False,
                            "promo_types": ["promo"],
                            "frame_effects": [],
                            "finishes": ["nonfoil", "foil"],
                        },
                    ]
                ),
                encoding="utf-8",
            )

            argv = [
                "cardmarket_price_fill.py",
                "--collection",
                str(collection),
                "--products",
                str(products),
                "--prices",
                str(prices),
                "--scryfall-bulk",
                str(bulk),
                "--output",
                str(output),
            ]

            with patch("sys.argv", argv):
                rc = script.main()

            self.assertEqual(rc, 0)
            self.assertTrue(output.exists())

            with output.open("r", encoding="utf-8", newline="") as f:
                out_rows = list(csv.DictReader(f))

            self.assertEqual(out_rows[0]["card market type"], "normal")
            self.assertEqual(out_rows[0]["card market finish"], "nonfoil")
            self.assertTrue(out_rows[0]["card market price"])

            self.assertEqual(out_rows[1]["card market type"], "borderless")
            self.assertEqual(out_rows[1]["card market finish"], "nonfoil")
            self.assertTrue(out_rows[1]["card market price"])

            # Promo markers are intentionally ignored for type and foil should use avg7-foil.
            self.assertEqual(out_rows[2]["card market type"], "normal")
            self.assertEqual(out_rows[2]["card market finish"], "foil")
            self.assertTrue(out_rows[2]["card market price"])
            self.assertNotEqual(out_rows[2]["card market price"], out_rows[0]["card market price"])


if __name__ == "__main__":
    unittest.main()
