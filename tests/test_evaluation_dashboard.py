from pathlib import Path

from fastapi.testclient import TestClient

from multillm.gateway import app


ROOT = Path(__file__).resolve().parents[1]


def test_evaluation_workspace_is_mounted_and_uses_local_assets():
    response = TestClient(app).get("/evaluations")

    assert response.status_code == 200
    html = response.text
    assert "Mixture of Agents evaluation" in html
    assert 'src="/evaluations/assets/evaluations.js"' in html
    assert "cdn.jsdelivr" not in html
    assert "unpkg.com" not in html
    assert 'id="winMatrix"' in html
    assert 'id="paretoChart"' in html
    assert 'id="skillChart"' in html
    assert 'id="tokenWaterfall"' in html
    assert 'id="latencyChart"' in html
    assert 'id="resultsTable"' in html
    assert 'id="reviewQueue"' in html
    assert 'id="reviewResponseA"' in html
    assert 'id="reviewResponseB"' in html
    assert 'id="reviewRationale"' in html
    assert 'data-review-decision="response_a"' in html
    assert 'data-review-decision="response_b"' in html
    assert 'data-review-decision="tie"' in html


def test_evaluation_workspace_has_accessible_nonvisual_paths_and_mobile_controls():
    html = (ROOT / "multillm/static/evaluations.html").read_text(encoding="utf-8")

    assert 'aria-live="polite"' in html
    assert 'aria-describedby="winMatrixDescription"' in html
    assert '<caption>Evaluation results' in html
    assert 'id="mobileFilters"' in html
    assert 'data-testid="evaluation-export"' in html
    assert "prefers-reduced-motion" in html


def test_evaluation_bundle_has_d3_views_url_state_and_resize_behavior():
    script = (ROOT / "multillm/static/evaluations.js").read_text(encoding="utf-8")

    assert "URLSearchParams" in script
    assert "ResizeObserver" in script
    assert "renderWinMatrix" in script
    assert "renderPareto" in script
    assert "renderSkillProfile" in script
    assert "renderTokenWaterfall" in script
    assert "Proposer input" in script
    assert "Final output" in script
    assert "renderLatency" in script
    assert "renderReviewQueue" in script
    assert "/api/evaluations/reviews/queue" in script
    assert "X-MultiLLM-Reviewer" in script
    assert "data-review-decision" in script
    assert "replaceOptions" in script
    assert "summary.pairwise" in script
    assert "Known cost" in script
    assert 'params.delete("api_key")' in script
    assert "sessionStorage" in script
    assert "d3" in script.lower()


def test_main_dashboard_links_to_evaluation_workspace():
    html = (ROOT / "multillm/static/dashboard.html").read_text(encoding="utf-8")

    assert 'href="/evaluations"' in html


def test_vendored_d3_has_pinned_provenance_and_license():
    notice = (ROOT / "multillm/static/vendor/README.md").read_text(encoding="utf-8")
    license_text = (ROOT / "multillm/static/vendor/d3.LICENSE").read_text(encoding="utf-8")

    assert "D3 7.9.0" in notice
    assert "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539" in notice
    assert "ISC License" in license_text
