#!/usr/bin/env python3
"""Fill Cardmarket average prices into a collection CSV.

This script expects:
- A collection CSV (e.g., ManaBox export) containing at least: Name, Set name, Collector number, Foil
- A Cardmarket product catalogue CSV/JSON
- A Cardmarket price guide CSV/JSON

It joins the collection rows to Cardmarket products, then writes a new CSV with
"card market price" filled from the price guide.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple, Union


def _open_text(path: str) -> io.TextIOBase:
    if path.lower().endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
    except csv.Error:
        # Fallback to comma
        class Simple(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL

        return Simple()


def _read_csv(path: str) -> Tuple[List[Dict[str, str]], List[str], csv.Dialect]:
    with _open_text(path) as f:
        sample = f.read(8192)
        f.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(f, dialect=dialect)
        rows = [
            { (k or "").strip(): (v or "").strip() for k, v in row.items() }
            for row in reader
        ]
        headers = [h.strip() for h in (reader.fieldnames or [])]
    return rows, headers, dialect


def _json_to_rows(obj: Union[dict, list]) -> List[Dict[str, str]]:
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        # Common container keys in exports
        for key in ("data", "products", "prices", "priceGuides", "priceGuide", "price_guide", "result", "results"):
            if key in obj and isinstance(obj[key], list):
                rows = obj[key]
                break
        else:
            # If dict of id -> row, convert to list
            if obj and all(isinstance(v, dict) for v in obj.values()):
                rows = list(obj.values())
            else:
                rows = []
    else:
        rows = []

    out: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({str(k).strip(): str(v).strip() for k, v in row.items()})
    return out


def _read_json(path: str) -> Tuple[List[Dict[str, str]], List[str], None]:
    with _open_text(path) as f:
        content = f.read()
    try:
        obj = json.loads(content)
        rows = _json_to_rows(obj)
    except json.JSONDecodeError:
        # Fallback to JSON Lines (NDJSON)
        rows = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append({str(k).strip(): str(v).strip() for k, v in obj.items()})
    headers: List[str] = []
    for row in rows:
        for k in row.keys():
            if k not in headers:
                headers.append(k)
    return rows, headers, None


def _read_table(path: str) -> Tuple[List[Dict[str, str]], List[str], Optional[csv.Dialect]]:
    lower = path.lower()
    if lower.endswith(".json") or lower.endswith(".json.gz"):
        return _read_json(path)
    return _read_csv(path)


def _load_scryfall_cache(path: str) -> Dict[str, Dict[str, str]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_scryfall_cache(path: str, data: Dict[str, Dict[str, str]]) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _fetch_scryfall_card(url: str) -> Optional[dict]:
    headers = {
        "User-Agent": "mtg-price-getter/1.0",
        "Accept": "application/json;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _scryfall_compact(card: dict) -> Dict[str, str]:
    return {
        "cardmarket_id": str(card.get("cardmarket_id") or ""),
        "collector_number": str(card.get("collector_number") or ""),
        "set": str(card.get("set") or ""),
        "set_name": str(card.get("set_name") or ""),
        "name": str(card.get("name") or ""),
        "frame_effects": ",".join(card.get("frame_effects") or []),
        "promo_types": ",".join(card.get("promo_types") or []),
        "border_color": str(card.get("border_color") or ""),
        "full_art": str(bool(card.get("full_art"))).lower(),
        "textless": str(bool(card.get("textless"))).lower(),
        "finishes": ",".join(card.get("finishes") or []),
        "lang": str(card.get("lang") or ""),
    }


def _scryfall_type(card: Dict[str, str]) -> str:
    promo_types = {p.strip() for p in card.get("promo_types", "").split(",") if p.strip()}
    frame_effects = {p.strip() for p in card.get("frame_effects", "").split(",") if p.strip()}
    finishes = {p.strip() for p in card.get("finishes", "").split(",") if p.strip()}
    border_color = card.get("border_color", "")
    full_art = card.get("full_art", "") == "true"
    textless = card.get("textless", "") == "true"

    if textless:
        return "normal"
    if "showcase" in frame_effects or "showcase" in promo_types:
        return "showcase"
    if "extendedart" in frame_effects or "extendedart" in promo_types:
        return "extended-art"
    if "borderless" in frame_effects or "borderless" in promo_types or border_color == "borderless" or full_art:
        return "borderless"
    if "retro" in frame_effects:
        return "retro"
    if "etched" in finishes:
        return "etched"
    return "normal"


def _load_scryfall_bulk(path: str) -> Tuple[Dict[str, Dict[str, str]], Dict[Tuple[str, str], Dict[str, str]]]:
    if not path or not os.path.exists(path):
        return {}, {}
    with _open_text(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        return {}, {}
    by_id: Dict[str, Dict[str, str]] = {}
    by_set_collector: Dict[Tuple[str, str], Dict[str, str]] = {}
    for card in data:
        if not isinstance(card, dict):
            continue
        compact = _scryfall_compact(card)
        cid = str(card.get("id") or "")
        if cid:
            by_id[cid] = compact
        set_code = compact.get("set", "")
        collector_number = compact.get("collector_number", "")
        if set_code and collector_number:
            by_set_collector[(set_code.lower(), collector_number)] = compact
    return by_id, by_set_collector


def _normalize_name(value: str) -> str:
    v = value.strip().lower()
    v = re.sub(r"\s+", " ", v)
    return v


def _normalize_number(value: str) -> str:
    v = value.strip()
    v = v.lstrip("0") or v
    return v


def _parse_bool(value: str) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"foil", "true", "yes", "1", "y", "t"}:
        return True
    if v in {"normal", "nonfoil", "non-foil", "false", "no", "0", "n", "f"}:
        return False
    return None


def _pick_column(headers: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    lookup = {h.lower(): h for h in headers}
    for c in candidates:
        if c.lower() in lookup:
            return lookup[c.lower()]
    return None


def _build_product_index(products: List[Dict[str, str]], headers: List[str]) -> Tuple[Dict[str, Dict[str, str]], Dict[Tuple[str, str, str, Optional[bool]], List[str]]]:
    id_col = _pick_column(headers, ["idProduct", "productId", "id", "Product ID", "ProductId"])
    name_col = _pick_column(headers, ["Name", "name", "Product Name", "productName"])
    set_col = _pick_column(headers, ["Expansion", "expansion", "Expansion Name", "Set", "Edition", "ExpansionName", "NameExpansion", "setName"])
    num_col = _pick_column(headers, ["Number", "Collector Number", "collectorNumber", "Card Number", "collector_number"])
    foil_col = _pick_column(headers, ["IsFoil", "Foil", "isFoil", "foil"])

    if not id_col or not name_col:
        raise ValueError("Product catalogue data must include product id and name columns.")

    by_id: Dict[str, Dict[str, str]] = {}
    index: Dict[Tuple[str, str, str, Optional[bool]], List[str]] = {}

    for row in products:
        pid = row.get(id_col, "").strip()
        if not pid:
            continue
        name = _normalize_name(row.get(name_col, ""))
        set_name = _normalize_name(row.get(set_col, "")) if set_col else ""
        number = _normalize_number(row.get(num_col, "")) if num_col else ""
        foil = _parse_bool(row.get(foil_col, "")) if foil_col else None

        by_id[pid] = {
            "name": name,
            "set_name": set_name,
            "number": number,
            "foil": str(foil) if foil is not None else "",
        }

        if foil is None:
            for foil_key in (None, True, False):
                key = (name, set_name, number, foil_key)
                index.setdefault(key, []).append(pid)
        else:
            key = (name, set_name, number, foil)
            index.setdefault(key, []).append(pid)

    return by_id, index


def _build_price_map(prices: List[Dict[str, str]], headers: List[str]) -> Dict[str, Dict[str, str]]:
    id_col = _pick_column(headers, ["idProduct", "productId", "id", "Product ID", "ProductId"])
    price_col = _pick_column(headers, ["avg7", "avg_7", "avg7Price", "avg7_price", "AVG7"])
    price_foil_col = _pick_column(headers, ["avg7-foil", "avg7_foil", "avg7Foil", "AVG7_FOIL"])

    if not id_col or not price_col:
        raise ValueError("Price guide data must include product id and avg7 price columns.")

    out: Dict[str, Dict[str, str]] = {}
    for row in prices:
        pid = row.get(id_col, "").strip()
        price = row.get(price_col, "").strip()
        price_foil = row.get(price_foil_col, "").strip() if price_foil_col else ""
        if not pid:
            continue
        out[pid] = {"avg": price, "avg_foil": price_foil}
    return out


def _match_product_ids(
    collection_row: Dict[str, str],
    index: Dict[Tuple[str, str, str, Optional[bool]], List[str]],
    name_col: str,
    set_col: Optional[str],
    num_col: Optional[str],
    foil_col: Optional[str],
) -> List[str]:
    name = _normalize_name(collection_row.get(name_col, ""))
    set_name = _normalize_name(collection_row.get(set_col, "")) if set_col else ""
    number = _normalize_number(collection_row.get(num_col, "")) if num_col else ""
    foil = _parse_bool(collection_row.get(foil_col, "")) if foil_col else None

    candidates: List[Tuple[str, str, str, Optional[bool]]] = []
    if name and set_name and number:
        candidates.append((name, set_name, number, foil))
        if foil is None:
            candidates.append((name, set_name, number, True))
            candidates.append((name, set_name, number, False))
    if name and set_name:
        candidates.append((name, set_name, "", foil))
        if foil is None:
            candidates.append((name, set_name, "", True))
            candidates.append((name, set_name, "", False))
    if name:
        candidates.append((name, "", "", foil))
        if foil is None:
            candidates.append((name, "", "", True))
            candidates.append((name, "", "", False))

    for key in candidates:
        if key in index:
            return index[key]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill Cardmarket average prices into a collection CSV.")
    parser.add_argument("--collection", required=True, help="Path to the collection CSV (e.g., ManaBox export).")
    parser.add_argument("--products", required=True, help="Path to Cardmarket product catalogue CSV/JSON (or .gz).")
    parser.add_argument("--prices", required=True, help="Path to Cardmarket price guide CSV/JSON (or .gz).")
    parser.add_argument("--output", help="Output CSV path. Defaults to <collection>.with_prices.csv")
    parser.add_argument("--add-product-id", action="store_true", help="Add a helper column with matched product id.")
    parser.add_argument("--no-scryfall", action="store_true", help="Disable Scryfall lookups.")
    parser.add_argument("--scryfall-cache", default=".scryfall_cache.json", help="Cache file for Scryfall lookups.")
    parser.add_argument("--scryfall-delay", type=float, default=0.12, help="Delay between Scryfall requests (seconds).")
    parser.add_argument("--scryfall-bulk", help="Path to Scryfall bulk data JSON (offline lookups).")
    args = parser.parse_args()

    collection_rows, collection_headers, collection_dialect = _read_csv(args.collection)
    product_rows, product_headers, _ = _read_table(args.products)
    price_rows, price_headers, _ = _read_table(args.prices)

    by_id, index = _build_product_index(product_rows, product_headers)
    price_map = _build_price_map(price_rows, price_headers)

    name_col = _pick_column(collection_headers, ["Name", "name", "Card", "card"])
    set_col = _pick_column(collection_headers, ["Set name", "Set", "Edition", "set", "setName"])
    set_code_col = _pick_column(collection_headers, ["Set code", "Set Code", "set code", "set_code", "setCode"])
    num_col = _pick_column(collection_headers, ["Collector number", "Number", "collectorNumber", "Card Number", "collector_number"])
    foil_col = _pick_column(collection_headers, ["Foil", "foil", "IsFoil", "isFoil"])
    scryfall_col = _pick_column(collection_headers, ["Scryfall ID", "Scryfall Id", "scryfall_id", "scryfallId"])

    if not name_col:
        raise ValueError("Collection CSV must include a Name column.")

    output_headers = list(collection_headers)
    if "card market price" not in output_headers:
        output_headers.append("card market price")
    if "card market type" not in output_headers:
        output_headers.append("card market type")
    if "card market finish" not in output_headers:
        output_headers.append("card market finish")
    if args.add_product_id and "card market product id" not in output_headers:
        output_headers.append("card market product id")

    use_scryfall = not args.no_scryfall and (scryfall_col or (set_code_col and num_col))
    scryfall_cache = _load_scryfall_cache(args.scryfall_cache) if use_scryfall else {}
    scryfall_bulk_by_id: Dict[str, Dict[str, str]] = {}
    scryfall_bulk_by_set_collector: Dict[Tuple[str, str], Dict[str, str]] = {}
    if use_scryfall and args.scryfall_bulk:
        scryfall_bulk_by_id, scryfall_bulk_by_set_collector = _load_scryfall_bulk(args.scryfall_bulk)

    matched = 0
    unmatched = 0

    output_rows: List[Dict[str, str]] = []
    for row in collection_rows:
        price = ""
        matched_id = ""
        card_type = ""
        finish = "nonfoil"

        if use_scryfall:
            scryfall_id = (row.get(scryfall_col, "").strip() if scryfall_col else "")
            card_data = None
            if args.scryfall_bulk:
                if scryfall_id and scryfall_id in scryfall_bulk_by_id:
                    card_data = scryfall_bulk_by_id.get(scryfall_id)
                elif set_code_col and num_col:
                    set_code = row.get(set_code_col, "").strip().lower()
                    collector_number = row.get(num_col, "").strip()
                    if set_code and collector_number:
                        card_data = scryfall_bulk_by_set_collector.get((set_code, collector_number))
            else:
                if scryfall_id:
                    if scryfall_id in scryfall_cache:
                        card_data = scryfall_cache[scryfall_id]
                    else:
                        data = _fetch_scryfall_card(f"https://api.scryfall.com/cards/{scryfall_id}")
                        if isinstance(data, dict):
                            card_data = _scryfall_compact(data)
                            scryfall_cache[scryfall_id] = card_data
                        time.sleep(args.scryfall_delay)
                elif set_code_col and num_col:
                    set_code = row.get(set_code_col, "").strip().lower()
                    collector_number = row.get(num_col, "").strip()
                    if set_code and collector_number:
                        data = _fetch_scryfall_card(f"https://api.scryfall.com/cards/{set_code}/{collector_number}")
                        if isinstance(data, dict):
                            card_data = _scryfall_compact(data)
                        time.sleep(args.scryfall_delay)

            if card_data:
                matched_id = card_data.get("cardmarket_id", "")
                if matched_id:
                    price_entry = price_map.get(str(matched_id), {})
                    is_foil = _parse_bool(row.get(foil_col, "")) if foil_col else False
                    finish = "foil" if is_foil else "nonfoil"
                    if is_foil and price_entry.get("avg_foil"):
                        price = price_entry.get("avg_foil", "")
                    else:
                        price = price_entry.get("avg", "")
                card_type = _scryfall_type(card_data)

        if not price:
            product_ids = _match_product_ids(row, index, name_col, set_col, num_col, foil_col)
            for pid in product_ids:
                if pid in price_map:
                    price_entry = price_map[pid]
                    is_foil = _parse_bool(row.get(foil_col, "")) if foil_col else False
                    finish = "foil" if is_foil else "nonfoil"
                    if is_foil and price_entry.get("avg_foil"):
                        price = price_entry.get("avg_foil", "")
                    else:
                        price = price_entry.get("avg", "")
                    matched_id = pid
                    break
        if price:
            matched += 1
        else:
            unmatched += 1
        row_out = dict(row)
        row_out["card market price"] = price
        row_out["card market type"] = card_type or "normal"
        row_out["card market finish"] = finish
        if args.add_product_id:
            row_out["card market product id"] = matched_id
        output_rows.append(row_out)

    output_path = args.output or (args.collection + ".with_prices.csv")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_headers, dialect=collection_dialect)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote {len(output_rows)} rows -> {output_path}")
    print(f"Matched prices: {matched}")
    print(f"Unmatched rows: {unmatched}")
    if use_scryfall:
        _save_scryfall_cache(args.scryfall_cache, scryfall_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
