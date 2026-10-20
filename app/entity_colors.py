"""Item colors: the six keys a property, renter or expense category is drawn in.

The clients turn a key into shades (``rent-control/src/core/theme/colors.ts``); the server
only stores the key, so the shades can be retuned without a migration. The tuple order is
the assignment order.
"""
from collections import Counter
from collections.abc import Iterable
from typing import Literal

ENTITY_COLORS: tuple[str, ...] = ("sky", "rose", "olive", "indigo", "plum", "mustard")

EntityColor = Literal["sky", "rose", "olive", "indigo", "plum", "mustard"]


def least_used_color(used: Iterable[str | None]) -> str:
    """The color for a new item: whichever the owner's existing items use least, ties
    going to the earlier one. The first six items therefore all differ, which a random pick
    would not guarantee. Unknown and missing values are ignored."""
    counts = Counter(c for c in used if c in ENTITY_COLORS)
    return min(ENTITY_COLORS, key=lambda c: (counts[c], ENTITY_COLORS.index(c)))
