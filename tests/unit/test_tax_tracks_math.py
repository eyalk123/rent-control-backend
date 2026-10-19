"""The tax tracks arithmetic, against the 2025 figures and the Tax Authority's own examples."""
import pytest

from app.services.tax_tracks_math import (
    bracket_tax,
    exempt_amount,
    income_for_tax,
    income_tax_on_rent,
    surtax_on_rent,
)

PERSONAL = [[84120, 10], [120720, 14], [228000, 20], [301200, 31], [560280, 35], [None, 47]]
OTHER = [[301200, 31], [560280, 35], [None, 47]]
CREDITS = 2.25 * 242 * 12  # a resident's basic points, 2025


def test_bracket_tax_from_zero():
    assert bracket_tax(PERSONAL, 0, 100_000) == pytest.approx(8412 + 15880 * 0.14)


def test_bracket_tax_stacked_on_other_income():
    # 50,000 placed on top of 290,000 crosses from 31% into 35% at 301,200.
    assert bracket_tax(PERSONAL, 290_000, 50_000) == pytest.approx(11_200 * 0.31 + 38_800 * 0.35)


def test_basic_points_are_used_up_near_65_000():
    # The plan's "about 65,000 ₪", computed rather than written down.
    assert income_for_tax(PERSONAL, CREDITS) == pytest.approx(65_340)


def test_rent_under_60_starts_at_31_percent():
    # Other income in the 20% bracket; the rent is still taxed at 31%.
    tax = income_tax_on_rent(PERSONAL, OTHER, 200_000, 50_000, CREDITS)
    assert tax == pytest.approx(50_000 * 0.31)


def test_from_60_the_rent_follows_the_personal_brackets():
    tax = income_tax_on_rent(PERSONAL, PERSONAL, 0, 100_000, CREDITS)
    assert tax == pytest.approx(8412 + 15880 * 0.14 - CREDITS)


def test_credit_points_help_the_rent_only_where_other_income_left_them():
    # Other income of 30,000 pays 3,000, leaving 3,534 of credits for the rent.
    tax = income_tax_on_rent(PERSONAL, OTHER, 30_000, 20_000, CREDITS)
    assert tax == pytest.approx(20_000 * 0.31 - (CREDITS - 3000))


def test_surtax_matches_circular_5_2025_example_3_3():
    """400,000 from work and 1,100,000 from capital: 23,353 general and 7,569 capital."""
    general, capital = surtax_on_rent(721_560, 3, 2, 400_000, 1_100_000)
    assert round(general) == 23_353
    assert round(capital) == 7_569


def test_surtax_example_3_2_has_no_capital_part():
    """Capital income of 700,000 is under the threshold on its own."""
    general, capital = surtax_on_rent(721_560, 3, 2, 400_000, 700_000)
    assert round(general) == 11_353
    assert capital == 0


def test_no_capital_surtax_before_2025():
    assert surtax_on_rent(721_560, 3, None, 0, 800_000)[1] == 0


def test_exemption_with_every_flat_on_it():
    ceiling = 5654
    assert exempt_amount(5000, 5000, ceiling) == 5000
    rent = 8000
    assert rent - exempt_amount(rent, rent, ceiling) == 2 * (rent - ceiling)
    assert exempt_amount(12000, 12000, ceiling) == 0


def test_exemption_counts_the_rent_of_flats_on_other_tracks():
    # 5,000 on the exemption, 5,000 more on 10%: the ceiling shrinks to 1,308.
    assert exempt_amount(5000, 10000, 5654) == 1308
