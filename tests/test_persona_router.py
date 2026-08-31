"""Unit tests del parser/selector del router de personas (sin HTTP)."""

from app.services.persona_router import NOBODY_TOKENS, parse_router_response

ELIGIBLE = ["Jean", "Fischl", "Keqing"]


def test_order_is_preserved():
    assert parse_router_response("Fischl\nJean", ELIGIBLE, 3) == ["Fischl", "Jean"]


def test_single_name():
    assert parse_router_response("Jean", ELIGIBLE, 3) == ["Jean"]


def test_dedupes_keeps_first_position():
    assert parse_router_response("Jean\nFischl\nJean", ELIGIBLE, 3) == ["Jean", "Fischl"]


def test_caps_at_max_count():
    assert (
        parse_router_response("Jean\nFischl\nKeqing", ELIGIBLE, 2)
        == ["Jean", "Fischl"]
    )


def test_case_insensitive_match_returns_canonical_name():
    assert parse_router_response("jean\nFISCHL", ELIGIBLE, 3) == ["Jean", "Fischl"]


def test_strips_quotes_and_punctuation():
    assert parse_router_response('"Jean."\n\'Fischl\'!', ELIGIBLE, 3) == ["Jean", "Fischl"]


def test_invalid_lines_are_skipped_valid_kept():
    assert (
        parse_router_response("La mejor es Jean, con certeza\nDendro\nJean", ELIGIBLE, 3)
        == ["Jean"]
    )


def test_nadie_variants_mean_nobody():
    assert parse_router_response("NADIE", ELIGIBLE, 3) == []
    assert parse_router_response("nadie", ELIGIBLE, 3) == []
    assert parse_router_response("None", ELIGIBLE, 3) == []
    assert parse_router_response("no one", ELIGIBLE, 3) == []


def test_nobody_tokens_cover_common_spellings():
    assert {"nadie", "none", "no one", "n/a", "-"} == NOBODY_TOKENS


def test_all_garbage_is_unparseable():
    assert parse_router_response("I don't know who", ELIGIBLE, 3) is None


def test_empty_is_unparseable():
    assert parse_router_response("", ELIGIBLE, 3) is None
    assert parse_router_response("\n\n", ELIGIBLE, 3) is None


def test_nadie_beats_names_on_same_response():
    # respuesta mezclada rara: el token explicito gana
    assert parse_router_response("NADIE\nJean", ELIGIBLE, 3) == []
