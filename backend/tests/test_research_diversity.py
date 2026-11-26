"""目的: RRF の偏りを抑える比較候補。固定した人工 relevance は実 ranker の精度を表さない。"""
import pytest
from app.workflows.research_diversity import diversify


def rows(*names):
    return [{"url": n} for n in names]


@pytest.mark.parametrize("names,aspects,primary,expected", [
    (("popular","official","noise"), {"speed":{"official":.95,"popular":.9}}, {"official"}, ["official","popular","noise"]),
    (("popular","speed","power","noise"), {"speed":{"speed":.95,"popular":.7},"power":{"power":.95}}, set(), ["speed","power","popular","noise"]),
    (("best","official","noise"), {"speed":{"best":.95,"official":.2}}, {"official"}, ["best","official","noise"]),
    (("a","b","c"), {}, {"c"}, ["a","b","c"]),
    (("a","b","c"), {"speed":{"a":.8,"b":.8}}, set(), ["a","b","c"]),
])
def test_diversity_cases(names, aspects, primary, expected):
    got = diversify(rows(*names), lambda x:x, aspects=aspects, verified_primary=frozenset(primary), limit=len(names))
    assert [r["url"] for r in got] == expected


@pytest.mark.parametrize("bad", [float("nan"), -1, 2, True])
def test_invalid_relevance(bad):
    with pytest.raises(ValueError):
        diversify(rows("a"), lambda x:x, aspects={"x":{"a":bad}})


def test_low_relevance_aspect_does_not_force_noise():
    got = diversify(rows("best", "noise", "other", "second", "third"), lambda x:x,
                    aspects={"speed":{"best":.95,"second":.9,"third":.85},
                             "power":{"noise":.1,"other":.2}}, limit=2)
    assert [r["url"] for r in got] == ["best", "second"]
