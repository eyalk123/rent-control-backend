"""Receipt scanning: the prompt, the owner's catalog, and the post-model clean-up.

The Anthropic call itself lives in :class:`DocumentExtractionService` with the lease scan,
which shares its client, file handling and telemetry. What is receipt-specific is here.

**The scanner never creates anything.** The owner's existing categories, suppliers and (when
asked) properties are handed to the model as lists of ids, and the model may only pick from
them. :func:`clean_receipt` then re-checks every id it returned against those same lists, so a
made-up or out-of-scope id becomes an empty field rather than an error — or someone else's
record. An empty field is the designed outcome when nothing matches.
"""
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException, status

from app.schemas.document_extraction import ReceiptExtraction, ReceiptFieldNote
from app.services import country_service

#: Longest edge a receipt photo is scaled down to before it is sent. Phone photos are 4000px
#: and up, which is past the API's per-image byte limit and gains nothing — the model reads
#: an image at a fixed resolution anyway. 2000px keeps handwriting legible after that.
_MAX_IMAGE_EDGE = 2000


@dataclass(frozen=True)
class CategoryOption:
    id: int
    #: The built-in key ("electricity") or the name the owner typed for their own category.
    label: str


@dataclass(frozen=True)
class SupplierOption:
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    category_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PropertyOption:
    id: int
    label: str


@dataclass(frozen=True)
class ReceiptCatalog:
    """What the scanner may pick from: the owner's active records, nothing else."""

    categories: tuple[CategoryOption, ...] = ()
    suppliers: tuple[SupplierOption, ...] = ()
    #: Empty when the client already knows the property, which tells the model not to look.
    properties: tuple[PropertyOption, ...] = field(default=())


#: Every value the transactions API accepts; narrowed per country in :func:`_allowed_methods`.
_PAYMENT_METHODS = (
    "cash", "bank_transfer", "check", "card", "mobile_payment", "other", "bit", "paybox",
)


def _tool_schema() -> dict:
    """The extraction schema, with ``payment_method`` closed to the values the API accepts.

    Free text here was read as "credit card" or "צ'ק" and then thrown away by the clean-up
    for not being one of the eight words; a closed list makes the model pick one. The
    Pydantic model keeps it a plain string, so a stray value is dropped by ``clean_receipt``
    rather than failing the whole scan. All eight are listed whatever the country — the
    country block says which apply — so the tool, and with it the prompt cache, is the
    same for every account.
    """
    schema = ReceiptExtraction.model_json_schema()
    schema["properties"]["payment_method"] = {
        "anyOf": [{"type": "string", "enum": list(_PAYMENT_METHODS)}, {"type": "null"}],
        "default": None,
    }
    return schema


RECEIPT_TOOL_NAME = "record_receipt_extraction"
RECEIPT_TOOL = {
    "name": RECEIPT_TOOL_NAME,
    "description": "Record the expense details read from the receipt.",
    "input_schema": _tool_schema(),
}

# Byte-identical for every request so it can be prompt-cached; everything that varies per
# account (country, the owner's lists) goes in later, uncached blocks.
RECEIPT_SYSTEM_PROMPT = """You read a receipt, invoice or payment slip that a landlord paid, to pre-fill an expense form in a property-management app. Receipts are very often HANDWRITTEN — a receipt book filled in by hand (in Hebrew, פנקס קבלות), an amount scribbled onto a printed form, a rubber stamp — and are often photographed at an angle, in poor light, folded or crumpled. They may be in any language; many are Hebrew, where right-to-left text sits beside left-to-right numbers.

Fill only what the receipt actually shows. Leave a field null when the receipt doesn't show it or you cannot read it. An empty field costs the user a few keystrokes; a wrong one can slip unnoticed into their books.

Fields:
- amount: the TOTAL actually paid (סה"כ, סה"כ לתשלום, Total), as a bare number — no currency symbol, no digit grouping. When the receipt shows items, a subtotal and VAT (מע"מ), take the final total including VAT. Add items up yourself only when no total is written at all, and then add a note.
- date: the date of the receipt or payment, ISO YYYY-MM-DD. Handwritten dates often use a two-digit year ("3/9/26") — write the full year. Read slashed or dotted dates in the order given in the country block.
- payment_method: how it was paid — exactly one of the values listed in the country block. Most receipts DO say this, often only implicitly, so look for it before giving up: מזומן → cash; המחאה / צ'ק / שיק / שיקים → check; העברה / העברה בנקאית / הפקדה → bank_transfer; אשראי / כרטיס אשראי / ויזה / מאסטרקארד → card; ביט → bit; פייבוקס → paybox. A receipt book (פנקס קבלות) usually has a payment table with a row or column per method — the method is whichever one has an amount, a tick or a circle in it. A method WRITTEN on the receipt always wins: if it says העברה בנקאית, the answer is bank_transfer, whatever else is filled in. Bank, branch and account numbers do NOT mean check on their own — a bank transfer carries exactly the same details. Only a cheque number, a cheque due date, or the word itself means check. A ticked box, a circled word or a single word next to "שולם ב" / "אופן תשלום" / "paid by" counts. If the clues disagree, give your best reading with a low-confidence note quoting them. Return null only when nothing on the receipt indicates the method.
- supplier_name: the business or person who issued the receipt, exactly as written — usually printed or stamped at the top, sometimes only handwritten.
- supplier_id: the id of the owner's supplier who issued the receipt, taken ONLY from the supplier list you are given. The owner saved each supplier by hand and the receipt was written by hand, so the same business is often spelled differently in the two — match generously on the name. Treat as the same supplier: spelling variants and typos (including swapped, missing or doubled letters), abbreviations, Hebrew/English transliteration, suffixes such as בע"מ or Ltd, and extra words on the receipt such as a second name, a partner or the trade ("ניסים כהן-אבי שרברבות" is the supplier "ניסים כהן"). A phone number or email on the receipt that matches a supplier is a match on its own. When the match rests on such a variant rather than an exact name, phone or email, still return the id, and add a medium-confidence note. Return null only when no supplier on the list is plausibly the same business. Never invent an id.
- category_ids: ids from the category list you are given that describe what was paid for — usually exactly one. Decide from the items or description on the receipt. The matched supplier's categories are a hint, not a limit: a supplier is sometimes paid for work outside the categories they are listed under. If nothing on the list fits, return an empty list; do not fall back to a catch-all such as "other" just to fill the field. Never invent an id.
- property_id: only when you are given a property list. The id of the property the work or purchase was for, when the receipt states an address that matches one on the list — anywhere on it: a "for" / "עבור" / "כתובת" line, the job description ("תיקון ברז ברח' הרצל 12"), or the customer details. Match the way a person would: the street name and house number are enough, without the city; ignore abbreviations and prefixes (רח', רחוב, שד', שדרות, St., Rd.), spelling variants, Hebrew/English transliteration, and a missing or differently written apartment number. When the match rests on such a variant rather than the full address as listed, still return the id and add a medium-confidence note. Return null when no address appears on the receipt, when nothing on the list is plausibly the same address, or when several properties fit equally well (the same building, no apartment number to tell them apart). Never infer the property from the supplier, the amount or anything other than an address on the receipt.

READING HANDWRITING AND NUMBERS
- Misread handwritten digits are the most common error. Look twice at 1/7, 4/9, 3/8, 5/6 and 0/6, and at whether a mark is a digit, a decimal point or a thousands separator.
- When a receipt states the amount twice — in digits and in words (סכום במילים / the sum in words), or as a line and as a total — use each to check the other. If they disagree, take the one that reads more clearly and add a low-confidence note quoting both.
- Thousands separators depend on the country convention given below. Output the bare value: "1.500" or "1,500" as one thousand five hundred → 1500; "1,234.56" → 1234.56.

Confidence: for every field you are NOT highly confident about — hard to read, inferred, ambiguous, or a supplier/category match that rests on a guess — add one `notes` entry with its `field` (the exact field name above), `confidence` ("medium" or "low"), and `source_text` (only the short text on the receipt you read it from, transcribed as you read it — not your reasoning, and not the record you matched it to). No note for a field you are confident about, and never a note for a field you left empty.

Return only the structured data."""

RECEIPT_INSTRUCTION = "Extract the expense details from this receipt."

#: The Israeli payment apps — offered only where the country has `bit_payments`.
_BIT_LIKE = {"bit", "paybox"}
_PAYMENT_ALIASES = {
    "wire_transfer": "bank_transfer",
    "credit_card": "card",
    "cheque": "check",
}

_SEPARATOR_BRIEF = {
    "1,234.56": "a comma groups thousands and a dot is the decimal point",
    "1.234,56": 'a DOT groups thousands and a COMMA is the decimal point, so "1.500" is one thousand five hundred',
    "1 234,56": "a space groups thousands and a comma is the decimal point",
}
_DATE_ORDER = {"DMY": "day before month", "MDY": "month before day", "YMD": "year first"}


def _allowed_methods(country: str | None) -> list[str]:
    config = country_service.config_for(country)
    if config.capabilities.bit_payments:
        return list(_PAYMENT_METHODS)
    return [m for m in _PAYMENT_METHODS if m not in _BIT_LIKE]


def country_brief(country: str | None) -> str:
    """What this account's receipts are expected to look like, from the country table."""
    config = country_service.config_for(country)
    return "\n".join(
        [
            f"This account is in {config.name}. Unless the receipt itself clearly says "
            f"otherwise, expect that:",
            f"- Numbers are written so that {_SEPARATOR_BRIEF[config.number_format]}.",
            f"- Amounts are in {config.currency}. Output the number alone, without a symbol.",
            f"- A date written with slashes or dots puts the {_DATE_ORDER[config.date_format]}.",
            "- `payment_method` must be one of: " + ", ".join(_allowed_methods(country)) + ".",
        ]
    )


def catalog_text(catalog: ReceiptCatalog) -> str:
    """The owner's lists, as the model sees them. Ids are what it must answer with."""
    lines = ["Categories (id: name):"]
    if catalog.categories:
        lines += [f"- {c.id}: {c.label}" for c in catalog.categories]
    else:
        lines.append("- (none — category_ids is always empty)")

    lines += ["", "Suppliers (id: name | phone | email | category ids):"]
    if catalog.suppliers:
        for s in catalog.suppliers:
            cats = ", ".join(str(c) for c in s.category_ids) or "-"
            lines.append(f"- {s.id}: {s.name} | {s.phone or '-'} | {s.email or '-'} | {cats}")
    else:
        lines.append("- (none — supplier_id is always null)")

    if catalog.properties:
        lines += ["", "Properties (id: address):"]
        lines += [f"- {p.id}: {p.label}" for p in catalog.properties]
    else:
        lines += ["", "No property list is given: property_id is always null."]
    return "\n".join(lines)


def normalize_receipt_image(file_bytes: bytes) -> bytes:
    """Upright, size-capped JPEG of a receipt photo.

    Phones store a portrait photo as landscape pixels plus an EXIF "rotate me" flag. The flag
    is applied here because a receipt read sideways is a receipt read badly, and nothing
    downstream of this honours it. The size cap is explained at ``_MAX_IMAGE_EDGE``.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90)
            return out.getvalue()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The image could not be read.",
        )


def clean_receipt(
    extraction: ReceiptExtraction,
    catalog: ReceiptCatalog,
    country: str | None = None,
    discarded: Optional[list[str]] = None,
) -> ReceiptExtraction:
    """Drop every value the form could not hold, then every note left about nothing.

    The ids are the point: each is checked against the catalog the model was given, so
    nothing outside the owner's own active records can reach the form. ``discarded`` is an
    optional out-param naming what was thrown away, for the log.
    """
    def _drop(name: str) -> None:
        if discarded is not None:
            discarded.append(f"{name}={getattr(extraction, name)!r}")
        setattr(extraction, name, [] if name == "category_ids" else None)

    if extraction.amount is not None and extraction.amount <= 0:
        _drop("amount")

    if extraction.date is not None:
        try:
            parsed = date.fromisoformat(extraction.date)
        except (ValueError, TypeError):
            _drop("date")
        else:
            # A future date is far likelier a misread digit than a real receipt. It is kept
            # and flagged rather than dropped: the user can see it and fix one digit.
            if parsed > date.today() + timedelta(days=1):
                _add_note(extraction, "date", f"{extraction.date} is in the future — check the year and month.")

    if extraction.payment_method is not None:
        method = _PAYMENT_ALIASES.get(extraction.payment_method, extraction.payment_method)
        if method in _allowed_methods(country):
            extraction.payment_method = method
        else:
            _drop("payment_method")

    known_categories = {c.id for c in catalog.categories}
    kept = list(dict.fromkeys(c for c in extraction.category_ids if c in known_categories))
    if kept != extraction.category_ids:
        if discarded is not None:
            discarded.append(f"category_ids={extraction.category_ids!r}")
        extraction.category_ids = kept

    suppliers = {s.id: s for s in catalog.suppliers}
    if extraction.supplier_id is not None:
        supplier = suppliers.get(extraction.supplier_id)
        if supplier is None:
            _drop("supplier_id")
        elif not extraction.category_ids and len(supplier.category_ids) == 1:
            # Nothing on the receipt said what was bought, and a supplier who works in one
            # category settles it. A category the model did choose is left alone even when
            # the supplier isn't listed under it — the form warns about that before saving.
            extraction.category_ids = [supplier.category_ids[0]]

    if extraction.property_id is not None and extraction.property_id not in {
        p.id for p in catalog.properties
    }:
        _drop("property_id")

    if extraction.supplier_name is not None:
        extraction.supplier_name = extraction.supplier_name.strip() or None

    extraction.notes = [n for n in extraction.notes if _is_populated(extraction, n.field)]
    return extraction


def _is_populated(extraction: ReceiptExtraction, name: str) -> bool:
    if name not in ReceiptExtraction.model_fields or name == "notes":
        return False
    value = getattr(extraction, name)
    return bool(value) if isinstance(value, list) else value is not None


def _add_note(extraction: ReceiptExtraction, name: str, text: str) -> None:
    if any(n.field == name for n in extraction.notes):
        return  # the model already flagged it; don't say it twice
    extraction.notes.append(ReceiptFieldNote(field=name, confidence="low", source_text=text))


def field_stats(extraction: ReceiptExtraction) -> tuple[int, int, int]:
    """Count populated fields, and low/medium-confidence counts from the notes."""
    names = ("amount", "date", "payment_method", "category_ids", "supplier_id", "property_id")
    extracted = sum(1 for n in names if _is_populated(extraction, n))
    low = sum(1 for n in extraction.notes if n.confidence == "low")
    medium = sum(1 for n in extraction.notes if n.confidence == "medium")
    return extracted, low, medium
