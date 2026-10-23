"""WI-07c: engine weakness whitelist (``config/engine_weakness.toml``; design 03 N4, matrix 07c-10/11).

An entry names an engine and character pairs it cannot tell apart; on a text cell (unit / flag / drug) that the
engine voted on, the pair is not compared. Entries that would touch digits, or name a numeric kind, are a
configuration error: the whitelist can never make two different numbers equal.
"""

from __future__ import annotations

import pytest

from klode_kit.audit import whitelist as W
from klode_kit.config import ConfigError

pytestmark = pytest.mark.unit

GOOD = '[[weakness]]\nengine = "vision"\nclass = "micro-sign"\npairs = [["u", "μ"]]\n'


def test_default_whitelist_loads_and_is_text_only():
    """The packaged file parses; no entry touches a digit or names a numeric kind; the vision misread
    ``10~9/L`` stays different from ``10^9/L`` (WI-07a: misreads are not normalised into agreement)."""
    wl = W.default()
    assert wl.rules
    for rule in wl.rules:
        assert set(rule.kinds) <= set(W.TEXT_KINDS)
        assert not any(ch.isdigit() for a, b in rule.pairs for ch in a + b)
    engines = ("vision", "mlx", "claude", "codex")
    assert wl.key("unit", "10~9/L", engines) != wl.key("unit", "10^9/L", engines)


def test_key_applies_only_to_text_kinds_and_present_engines():
    wl = W.parse(GOOD)
    assert wl.key("unit", "umol/L", ("vision", "mlx")) == wl.key("unit", "μmol/L", ("vision", "mlx"))
    assert wl.key("number", "u5", ("vision",)) == "u5"
    assert wl.key("date", "u", ("vision",)) == "u"
    assert wl.key("unit", "umol/L", ("mlx", "claude")) == "umol/L"


def test_rule_kinds_restrict():
    wl = W.parse(GOOD + 'kinds = ["flag"]\n')
    assert wl.rules[0].kinds == ("flag",)
    assert wl.key("unit", "umol/L", ("vision",)) == "umol/L"
    assert wl.key("flag", "u", ("vision",)) == wl.key("flag", "μ", ("vision",))


@pytest.mark.parametrize("text", [
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["0", "8"]]\n',            # digits
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["O", "0"]]\n',            # digit on one side
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["٣", "x"]]\n',            # any Unicode digit
    GOOD + 'kinds = ["number"]\n',                                                      # numeric kind
    GOOD + 'kinds = ["date"]\n',
    GOOD + 'kinds = []\n',
    '[[weakness]]\nengine = "ocrx"\nclass = "x"\npairs = [["u", "μ"]]\n',              # unknown engine
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = []\n',                       # no pairs
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["u", "u"]]\n',            # identical
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["", "u"]]\n',             # empty side
    '[[weakness]]\nengine = "vision"\nclass = "x"\npairs = [["u"]]\n',                 # not a pair
    '[[weakness]]\nengine = "vision"\npairs = [["u", "μ"]]\n',                          # missing class
    GOOD + 'action = "ignore"\n',                                                       # unknown key
    'weakness = 3\n',                                                                    # wrong shape
    'other = 1\n',                                                                       # unknown top key
    '[[weakness]\n',                                                                     # bad TOML
])
def test_parse_rejects(text):
    """Guard (L1-S3): every malformed or numeric-touching entry is ``ConfigError`` (exit 40)."""
    with pytest.raises(ConfigError) as err:
        W.parse(text)
    assert err.value.exit_code == 40


def test_load_from_path(tmp_path):
    path = tmp_path / "engine_weakness.toml"
    path.write_text(GOOD, encoding="utf-8")
    assert W.load(path).rules == W.parse(GOOD).rules
    with pytest.raises(ConfigError):
        W.load(tmp_path / "missing.toml")


def test_parse_rejects_non_table_entry():
    """Guard (L1-S3): a ``weakness`` array whose entry is not a table is ``ConfigError``."""
    with pytest.raises(ConfigError):
        W.parse("weakness = [1]\n")
