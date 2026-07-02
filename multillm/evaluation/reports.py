"""Audit and management report renderers for evaluation runs."""

from __future__ import annotations

from html import escape
from typing import Any


def render_management_html(bundle: dict[str, Any]) -> str:
    run = bundle["run"]
    manifest = bundle["manifest"]
    summary = run.get("summary") or {}
    rows = []
    for output in run.get("outputs") or []:
        latency = output.get("latency") or {}
        cost = output.get("cost") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(str(output.get('caseId', '')))}</td>"
            f"<td>{escape(str(output.get('target', '')))}</td>"
            f"<td>{escape(str(output.get('status', '')))}</td>"
            f"<td>{escape(str(latency.get('total_ms', '')))}</td>"
            f"<td>{escape(str(cost.get('normalized_usd', '')))}</td>"
            f"<td><pre>{escape(str(output.get('outputText') or ''))}</pre></td>"
            "</tr>"
        )
    comparison_rows = []
    for comparison in bundle.get("comparisons") or []:
        comparison_rows.append(
            "<tr>"
            f"<td>{escape(str(comparison.get('caseId', '')))}</td>"
            f"<td>{escape(str(comparison.get('candidateTarget', '')))}</td>"
            f"<td>{escape(str(comparison.get('baselineTarget', '')))}</td>"
            f"<td>{escape(str(comparison.get('decision', '')))}</td>"
            f"<td>{escape(str(comparison.get('humanDecision') or ''))}</td>"
            f"<td>{escape(str(len(comparison.get('judgments') or [])))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MultiLLM evaluation {escape(str(run['id']))}</title>
  <style>
    body{{font:14px system-ui,sans-serif;margin:2rem;color:#17212b}}
    table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd5df;padding:.5rem;text-align:left;vertical-align:top}}
    th{{background:#eef3f7}}pre{{white-space:pre-wrap;max-width:60ch;margin:0}}
    .meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:.75rem;margin:1rem 0}}
    .meta div{{border-left:4px solid #0f766e;padding:.5rem;background:#f5faf9}}
  </style>
</head>
<body>
  <h1>Evaluation report</h1>
  <div class="meta">
    <div><strong>Run</strong><br>{escape(str(run['id']))}</div>
    <div><strong>Status</strong><br>{escape(str(run['status']))}</div>
    <div><strong>Suite</strong><br>{escape(str(run['suiteId']))}</div>
    <div><strong>Manifest SHA-256</strong><br>{escape(str(manifest['sha256']))}</div>
  </div>
  <h2>Summary</h2><pre>{escape(str(summary))}</pre>
  <h2>Pairwise comparisons</h2>
  <table><thead><tr><th>Case</th><th>MoA candidate</th><th>Baseline</th><th>Judge decision</th><th>Human decision</th><th>Judgments</th></tr></thead>
  <tbody>{''.join(comparison_rows)}</tbody></table>
  <h2>Outputs</h2>
  <table><thead><tr><th>Case</th><th>Target</th><th>Status</th><th>Total ms</th><th>Normalized USD</th><th>Output</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
</body></html>"""
