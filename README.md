# MTG Price Getter (Cardmarket + ManaBox)

This project fills Cardmarket prices into a ManaBox export CSV and adds card finish/type metadata.

## Inputs

- **Collection CSV**: Exported from the ManaBox app (Collection → export CSV).
- **Cardmarket price guide JSON**: Magic: The Gathering price guide (download link below).
- **Cardmarket product list JSON**: Magic: The Gathering singles product list (download link below).
- **Scryfall bulk data** (optional but recommended): Used to identify print types (borderless/showcase/extended-art/etc.) and map to Cardmarket IDs without live API calls.

## Download links

Cardmarket:
- Price guide (Magic singles): [price_guide_1.json](https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json)
- Product list (Magic singles): [products_singles_1.json](https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_1.json)

Scryfall:
- Bulk data index (choose **Default Cards** for English only, or **All cards** for all mutations): [https://api.scryfall.com/bulk-data](https://scryfall.com/docs/api/bulk-data)
  - From the JSON, pick the `download_uri` for `default_cards` and download it (this is what the script expects for `--scryfall-bulk`).

## Usage

Place your files in the project folder or provide full paths.

```bash
python3 /Users/robinvyslouzil/Projects/Private/mtg-price-getter/cardmarket_price_fill.py \
  --collection /Users/robinvyslouzil/Projects/Private/mtg-price-getter/collection.csv \
  --products /Users/robinvyslouzil/Projects/Private/mtg-price-getter/products.json \
  --prices /Users/robinvyslouzil/Projects/Private/mtg-price-getter/prices.json \
  --scryfall-bulk /Users/robinvyslouzil/Projects/Private/mtg-price-getter/scryfall_bulk.json \
  --output /Users/robinvyslouzil/Projects/Private/mtg-price-getter/collection.with_prices.csv
```

### Output columns

The script adds:
- `card market price` (uses **AVG7** / **AVG7-foil** from Cardmarket)
- `card market type` (normal, borderless, showcase, extended-art, retro, etched)
- `card market finish` (foil/nonfoil)

### Notes

- If you omit `--scryfall-bulk`, the script can still try live Scryfall lookups, but offline bulk data is faster and more reliable.
- Tokens and emblems generally do not have Cardmarket prices; those rows will remain blank.

## Files

- Script: `/Users/robinvyslouzil/Projects/Private/mtg-price-getter/cardmarket_price_fill.py`
- Output (example): `/Users/robinvyslouzil/Projects/Private/mtg-price-getter/collection.with_prices.csv`
