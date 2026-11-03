"""Taxes and pre-tax price computation."""

from datetime import date

import pytest

from plein_tarif.market import discount_ht, excise_rate, price_ht, tax_zone_of, vat_rate


def test_excise_rates_by_zone_and_period():
    assert excise_rate("Gazole", date(2023, 5, 1)) == pytest.approx(0.5940 + 0.0135)
    assert excise_rate("Gazole", date(2023, 5, 1), "idf") == pytest.approx(0.5940 + 0.0135 + 0.0189)
    assert excise_rate("Gazole", date(2023, 5, 1), "corsica") == pytest.approx(0.5940)
    assert excise_rate("E10", date(2021, 1, 1)) == pytest.approx(0.6629 + 0.0073)
    assert excise_rate("SP98", date(2021, 1, 1), "corsica") == pytest.approx(0.6829 - 0.0100)
    assert excise_rate("E85", date(2022, 6, 1), "idf") == pytest.approx(0.1183)
    assert excise_rate("GPLc", date(2020, 1, 1)) == pytest.approx(0.2071 * 0.557)
    # 1 Aug 2025: regional majoration folded into the national rate (same total outside IdF)
    assert excise_rate("Gazole", date(2025, 9, 1)) == pytest.approx(0.6075)
    assert excise_rate("Gazole", date(2025, 9, 1), "idf") == pytest.approx(0.6075 + 0.0189)


def test_discount_periods_2022():
    assert discount_ht(date(2022, 3, 31)) == 0.0
    assert discount_ht(date(2022, 4, 1)) == 0.15
    assert discount_ht(date(2022, 9, 1)) == 0.25
    assert discount_ht(date(2022, 11, 15)) == 0.25
    assert discount_ht(date(2022, 11, 16)) == 0.0833
    assert discount_ht(date(2023, 1, 1)) == 0.0


def test_price_ht_worked_example():
    # Gazole at 1.800 EUR displayed in Toulouse on 2023-05-01: 1.800 / 1.2 - 0.6075 = 0.8925
    assert price_ht(1.800, vat_rate("mainland"), excise_rate("Gazole", date(2023, 5, 1))) == pytest.approx(0.8925)
    # During the 30-cent discount, the displayed price is net of it: the HT discount is added back
    p = price_ht(1.800, 0.20, excise_rate("Gazole", date(2022, 9, 15)), discount_ht(date(2022, 9, 15)))
    assert p == pytest.approx(1.800 / 1.2 - 0.6075 + 0.25)
    # Corsica: 13% VAT
    assert price_ht(1.13, vat_rate("corsica"), 0.0) == pytest.approx(1.0)


def test_tax_zone_of():
    assert tax_zone_of("75") == "idf" and tax_zone_of("2A") == "corsica" and tax_zone_of("31") == "mainland"
