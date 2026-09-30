"""The offline chat parser: intents, fuzzy products and vendors, windows, clarifications."""

from __future__ import annotations

import pytest

from negotiation_mcp import nlu


@pytest.fixture(scope="module")
def cat(sample_book):
    return nlu.catalog_from_book(sample_book)


@pytest.mark.parametrize("text, intent, sku, vendor", [
    ("best price for ceftriaxone from medisindo", "options", "CEF1G-MED", "Medisindo"),
    ("Best price for CEFTRI from Medisindo?", "options", "CEF1G-MED", "Medisindo"),
    ("cefriaxone medisndo", "options", "CEF1G-MED", "Medisindo"),
    ("negotiate iv cannula with prima", "negotiate", "IVC22-PRI", "Prima Alkes"),
    ("what should i say to prima about cannula", "negotiate", "IVC22-PRI", "Prima Alkes"),
    ("evidence for hba1c sehat", "details", "HBA1C-SEH", "Sehat Medika"),
    ("troponin from global diagnostika", "options", "TNI-GLO", "Global Diagnostika"),
    ("paracetamol infusion quote from nusantara", "options", "PCT1G-NUS", "Nusantara Farma"),
    ("sutures from medisindo", "options", "SUT20-MED", "Medisindo"),
    ("drug eluting stent surgika", "options", "DES-SUR", "Surgika Utama"),
    ("syringe 3ml prima", "options", "SYR3-PRI", "Prima Alkes"),
    ("what do we pay for gloves", "lookup", "GLV-M-NUS", None),
])
def test_products_and_vendors(cat, text, intent, sku, vendor):
    p = nlu.parse(text, cat)
    assert (p.intent, p.sku, p.vendor) == (intent, sku, vendor)


@pytest.mark.parametrize("text, intent", [
    ("show me risks", "alerts"), ("any red flags?", "alerts"), ("where can we save money", "savings"),
    ("which contracts expire this month", "renewals"), ("renewals in the next 60 days", "renewals"),
    ("my renewals", "my_renewals"), ("help", "help"), ("hello", "greeting"),
    ("how much do we spend with sehat medika", "vendor"), ("blah blah", "unknown"),
])
def test_intents(cat, text, intent):
    assert nlu.parse(text, cat).intent == intent


def test_missing_vendor_is_asked_for_with_candidates(cat):
    p = nlu.parse("hba1c", cat)
    assert p.missing == ["vendor"] and len(p.candidates) == 3


def test_missing_product_is_asked_for(cat):
    p = nlu.parse("negotiate with medisindo", cat)
    assert p.missing == ["sku"] and p.vendor == "Medisindo"


def test_hospital_and_windows(cat):
    assert nlu.parse("makassar gloves price", cat).hospital == "Site Makassar"
    assert nlu.parse_window("next 60 days") == (0, 60)
    assert nlu.parse_window("30-90 days") == (30, 90)
    assert nlu.parse_window("next 2 weeks") == (0, 14)
    assert nlu.parse_window("this month") == (0, 30)
    assert nlu.parse_window("no window here") is None


def test_unknown_offers_close_matches(cat):
    assert nlu.parse("injection", cat).suggestions


def test_empty_text_is_help(cat):
    assert nlu.parse("   ", cat).intent == "help"
