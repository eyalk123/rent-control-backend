"""Shared vocabulary for the ``cpi_rent_change`` notification.

The event has two stages generated in two very different places, and this module is
what keeps them speaking the same language:

- the **heads-up** (``STAGE_UPCOMING``, offset :data:`UPCOMING_OFFSET`) comes from the
  rules engine, because "an anniversary is approaching" is derivable from current state;
- the **confirmation** (``STAGE_CHANGED``, offset :data:`CHANGED_OFFSET`) is emitted by
  the indexing job at the moment it overwrites ``lease_years``, because one line later
  the old amount no longer exists anywhere.

Both share a ``period_key`` — the ISO anniversary of the lease year being repriced — so
the feed's existing dedup, collapse and group-dismiss all work unchanged.
"""
import json
from datetime import date
from typing import Optional

from app.services.lease_periods import period_index_on, period_start

STAGE_UPCOMING = "upcoming"
STAGE_CHANGED = "changed"

# Days before the anniversary for the heads-up. Also the notification's ``offset``,
# which is what keeps the two stages distinct rows under the dedup key.
UPCOMING_OFFSET = 30
CHANGED_OFFSET = 0

# How long a CPI notification lingers before clearing itself. Unlike rent-due and
# lease-expiring, nothing the user does resolves "the index moved" — so without this the
# feed would accumulate every rent change forever.
AUTO_DISMISS_DAYS = 30


def parse_lease_years(raw: Optional[str]) -> list[dict]:
    """The stored ``lease_years`` blob as a list, or ``[]`` for anything unreadable."""
    if not raw:
        return []
    try:
        years = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return years if isinstance(years, list) else []


def year_rule_mode(year: dict) -> Optional[str]:
    rule = year.get("rule")
    return rule.get("mode") if isinstance(rule, dict) else None


def is_cpi_linked(mode: Optional[str], lease_years: list[dict]) -> bool:
    """Whether the indexing job can move this lease's rent at all — whole-lease ``cpi``
    mode, or a ``custom`` lease carrying at least one CPI year."""
    if mode == "cpi":
        return True
    if mode == "custom":
        return any(year_rule_mode(y) == "cpi" for y in lease_years)
    return False


def lease_year_index(
    lease_start: Optional[date], lease_years: list[dict], on: date
) -> Optional[int]:
    """Zero-based index of the lease period containing ``on``, or ``None`` when ``on``
    falls outside the lease.

    Takes the periods rather than just their count: with variable-length periods the
    index is no longer derivable from the elapsed years alone.
    """
    return period_index_on(lease_start, lease_years, on)


def anniversary_of(lease_start: date, lease_years: list[dict], year_index: int) -> date:
    """When the given period begins — the date its rent is repriced on.

    Still called an anniversary because that is what it is for the common lease of whole
    years; for one carrying a short period it is simply the next period boundary.
    """
    return period_start(lease_start, lease_years, year_index)


def is_material(
    old_amount: float, new_amount: float, min_amount: float, min_percent: float
) -> bool:
    """Whether a change is big enough to be worth telling the owner about.

    The floor is the *larger* of an absolute and a relative threshold, so it holds up
    across a portfolio: ₪10 keeps a cheap flat from reporting rounding noise, 0.5% keeps
    an expensive one from reporting a change that is ₪10 but meaningless.
    """
    delta = abs(new_amount - old_amount)
    if delta == 0:
        return False
    floor = max(min_amount or 0.0, abs(old_amount) * (min_percent or 0.0) / 100)
    return delta >= floor


def build_data(
    stage: str,
    *,
    old_amount: float,
    new_amount: float,
    effective_date: date,
    year_index: int,
    base_index: Optional[float] = None,
    known_index: Optional[float] = None,
    index_month: Optional[str] = None,
    index_source: Optional[str] = None,
) -> dict:
    """The notification's ``data`` payload.

    Clients render from this: the amounts and the effective date go in the push, the
    index fields are shown when a feed row is expanded so a disputed figure can be
    traced back to the reading it came from.
    """
    delta = new_amount - old_amount
    return {
        "stage": stage,
        "old_amount": old_amount,
        "new_amount": new_amount,
        "delta": delta,
        "delta_percent": round(delta / old_amount * 100, 2) if old_amount else None,
        "effective_date": effective_date.isoformat(),
        "year_index": year_index,
        "base_index": base_index,
        "known_index": known_index,
        "index_month": index_month,
        "index_source": index_source,
    }
