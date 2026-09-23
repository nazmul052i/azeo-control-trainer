"""Truthful coverage checks for the Azeo primary function-block catalog.

This test deliberately distinguishes a native block from a stable trainer
alias and from a useful-but-incomplete substitute.  A palette search result is
not, by itself, an implementation claim.
"""
from __future__ import annotations

from collections import Counter

from azeo_control_trainer.core.strategy import blocks as _blocks  # noqa: F401
from azeo_control_trainer.core.strategy.block_catalog import (
    COMPATIBILITY_ALIASES,
    AZEO_MNEMONICS,
    EXPECTED_FAMILY_COUNTS,
    OUT_OF_SCOPE,
    PRIMARY_BLOCKS,
    SUBSTITUTES,
    CoverageKind,
    audit_registered_types,
    coverage_records,
    primary_mnemonics,
)
from azeo_control_trainer.core.strategy.model.block_registry import registry


def test_primary_catalog_contains_97_unique_entries() -> None:
    mnemonics = primary_mnemonics()

    assert len(mnemonics) == 97
    assert len(set(mnemonics)) == 97
    assert {
        family: len(entries) for family, entries in PRIMARY_BLOCKS.items()
    } == dict(EXPECTED_FAMILY_COUNTS)


def test_every_entry_has_one_honest_coverage_classification() -> None:
    records = coverage_records()
    counts = Counter(record.kind for record in records)

    assert len(records) == 97
    assert counts == {
        CoverageKind.NATIVE: 55,
        CoverageKind.COMPATIBILITY_ALIAS: 26,
        CoverageKind.SUBSTITUTE: 12,
        CoverageKind.OUT_OF_SCOPE: 4,
    }
    assert set(COMPATIBILITY_ALIASES).isdisjoint(SUBSTITUTES)
    assert set(COMPATIBILITY_ALIASES).isdisjoint(OUT_OF_SCOPE)
    assert set(SUBSTITUTES).isdisjoint(OUT_OF_SCOPE)


def test_coverage_targets_are_registered_and_exclusions_stay_explicit() -> None:
    registered = registry.all_types()

    assert audit_registered_types(registered) == ()
    assert set(OUT_OF_SCOPE).isdisjoint(registered)
    assert set(OUT_OF_SCOPE).isdisjoint(AZEO_MNEMONICS)

    for mnemonic, (implementation, note) in SUBSTITUTES.items():
        assert AZEO_MNEMONICS[mnemonic] == implementation
        assert implementation in registered
        assert note


def test_native_contracts_are_not_search_aliases() -> None:
    native = {
        "TAGAI", "TAGAO", "TAGDI", "TAGDO", "TAGIO", "ECTLSL", "ERAMP",
    }

    assert native.isdisjoint(COMPATIBILITY_ALIASES)
    assert native.isdisjoint(SUBSTITUTES)
    assert all(AZEO_MNEMONICS[name] == name for name in native)
