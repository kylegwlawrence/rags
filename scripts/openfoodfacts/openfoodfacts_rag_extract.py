"""Extract one Doc per Open Food Facts product for the RAG indexer.

Each `products` row is rendered as section-headered markdown:

    ## Product
    Brand: Kellogg's
    Categories: Breakfast cereals, Cereals
    Countries: United States, Canada

    ## Ingredients
    Whole grain wheat, sugar, salt, ...

    ## Nutrition (per 100g)
    Energy: 357 kcal
    Fat: 1.2g
    Saturated fat: 0.2g
    Carbohydrates: 74g
    Sugars: 11g
    Fiber: 11g
    Proteins: 12g
    Salt: 1.1g

Empty fields are omitted. `chunk_markdown` splits on `##` headings so each
chunk's `section` column carries "Product", "Ingredients", or "Nutrition".

`doc_id` is the `code` field (barcode) when non-empty, otherwise `str(id)`.

Version key is `content_hash(code, product_name, brands, categories_en,
ingredients_text)` plus `CLEANER_VERSION`. The source DB has no per-row
`updated_at`, so a content hash is the only edit-detection signal.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per row in `openfoodfacts.products`.

    Rows with neither a product name nor ingredients are skipped (no useful
    text to embed). Rows with an empty code use their integer `id` as doc_id.

    Args:
        conn: Read-only connection to `data/openfoodfacts/openfoodfacts.db`.
        limit: Maximum number of products to yield. None processes all.
    """
    sql = (
        "SELECT id, code, product_name, brands, categories_en, countries_en, "
        "       ingredients_text, "
        "       energy_kcal_100g, fat_100g, saturated_fat_100g, "
        "       carbohydrates_100g, sugars_100g, fiber_100g, "
        "       proteins_100g, salt_100g "
        "FROM products ORDER BY id"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        doc = _build_doc(row)
        if doc is not None:
            yield doc


def _clean(value: str | None) -> str:
    """Strip HTML and normalise whitespace; return empty string for None/blank."""
    return normalize_whitespace(strip_html(value or ""))


def _build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one products row into a markdown Doc, or None if there's nothing to embed."""
    product_name = _clean(row["product_name"])
    brands = _clean(row["brands"])
    categories_en = _clean(row["categories_en"])
    countries_en = _clean(row["countries_en"])
    ingredients_text = _clean(row["ingredients_text"])

    if not product_name and not ingredients_text:
        return None

    code: str = row["code"] or ""
    doc_id = code if code else str(row["id"])
    title = product_name or doc_id

    parts: list[str] = []

    # Product section — identity and availability metadata
    product_lines: list[str] = []
    if brands:
        product_lines.append(f"Brand: {brands}")
    if categories_en:
        product_lines.append(f"Categories: {categories_en}")
    if countries_en:
        product_lines.append(f"Countries: {countries_en}")
    if product_lines:
        parts.append("## Product\n" + "\n".join(product_lines))

    if ingredients_text:
        parts.append(f"## Ingredients\n{ingredients_text}")

    # Nutrition section — only emit if at least one nutrient value is present
    nutrition_lines: list[str] = []
    nutrient_pairs = [
        ("Energy", row["energy_kcal_100g"], "kcal"),
        ("Fat", row["fat_100g"], "g"),
        ("Saturated fat", row["saturated_fat_100g"], "g"),
        ("Carbohydrates", row["carbohydrates_100g"], "g"),
        ("Sugars", row["sugars_100g"], "g"),
        ("Fiber", row["fiber_100g"], "g"),
        ("Proteins", row["proteins_100g"], "g"),
        ("Salt", row["salt_100g"], "g"),
    ]
    for label, val, unit in nutrient_pairs:
        v = _clean(val)
        if v:
            nutrition_lines.append(f"{label}: {v}{unit}")
    if nutrition_lines:
        parts.append("## Nutrition (per 100g)\n" + "\n".join(nutrition_lines))

    text = "\n\n".join(parts)
    if not text.strip():
        return None

    version = content_hash(
        code, product_name, brands, categories_en, countries_en, ingredients_text,
        row["energy_kcal_100g"], row["fat_100g"], row["saturated_fat_100g"],
        row["carbohydrates_100g"], row["sugars_100g"], row["fiber_100g"],
        row["proteins_100g"], row["salt_100g"],
    )
    return Doc(
        doc_id=doc_id,
        title=title,
        version=f"{version}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )
