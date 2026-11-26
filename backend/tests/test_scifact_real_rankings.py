"""Real independent labels: ensure actual RRF isn't assumed to improve ranking."""
import json
import pytest
from tools.eval_scifact_rankings import FIXTURE, run


def test_scifact_real_data_comparison():
    data=json.loads(FIXTURE.read_text())
    assert data["corpus_count"]==5183 and data["test_queries"]==300
    r=run(data["cases"])
    assert r["chosen_title_weight"]==0
    assert r["development"]["weights"][-1][1]["ndcg"]==pytest.approx(.6711357455)
    assert r["held_back"]["rrf"]["ndcg"]==pytest.approx(.5764294257)
    assert r["held_back"]["combined"]["ndcg"]==pytest.approx(.6542094250)
    assert r["held_back"]["combined"]["recall"]>r["held_back"]["rrf"]["recall"]
