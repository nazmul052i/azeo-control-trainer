"""OPC UA type selection follows the tag model, not block-name guesses."""

from azeo_control_trainer.connectivity.opcua.pk_server import _canonical_ua_type
from azeo_control_trainer.core.strategy.model.block_base import DataType


def test_uppercase_tag_database_types_map_to_native_ua_scalar_families():
    assert _canonical_ua_type("BOOL") == "bool"
    assert _canonical_ua_type("INT") == "int"
    assert _canonical_ua_type("ENUM") == "int"
    assert _canonical_ua_type("STRING") == "string"
    assert _canonical_ua_type("FLOAT") == "float"


def test_model_enum_and_legacy_lowercase_spellings_are_supported():
    assert _canonical_ua_type(DataType.BOOL) == "bool"
    assert _canonical_ua_type("boolean") == "bool"
    assert _canonical_ua_type("integer") == "int"
    assert _canonical_ua_type("str") == "string"
