import math

from or_aws_fleet.display_format import compact_whole_number


def test_compact_whole_number_uses_k_for_thousands() -> None:
    assert compact_whole_number(55_384) == "55k"
    assert compact_whole_number(346_754) == "347k"
    assert compact_whole_number(977_484.49) == "977k"


def test_compact_whole_number_removes_decimals_below_one_thousand() -> None:
    assert compact_whole_number(52) == "52"
    assert compact_whole_number(81.2) == "81"
    assert compact_whole_number(999.49) == "999"


def test_compact_whole_number_supports_units_and_invalid_values() -> None:
    assert compact_whole_number(1_500, "km") == "2k km"
    assert compact_whole_number(-1_500) == "-2k"
    assert compact_whole_number(math.nan) == "0"
