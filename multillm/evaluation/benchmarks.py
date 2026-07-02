"""Official benchmark protocol registry and local, checksum-first import adapters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import EvaluationCase


@dataclass(frozen=True)
class BenchmarkDefinition:
    id: str
    name: str
    official_metric: str
    source_url: str
    protocol_adapter: str
    code_license: str
    data_license: str
    original_moa: bool
    supplemental: bool
    customer_redistributable: bool
    download_mode: str = "on_demand"

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "officialMetric": self.official_metric,
            "sourceUrl": self.source_url,
            "protocolAdapter": self.protocol_adapter,
            "codeLicense": self.code_license,
            "dataLicense": self.data_license,
            "originalMoA": self.original_moa,
            "supplemental": self.supplemental,
            "customerRedistributable": self.customer_redistributable,
            "downloadMode": self.download_mode,
        }


BENCHMARKS: dict[str, BenchmarkDefinition] = {
    "alpacaeval-2": BenchmarkDefinition(
        id="alpacaeval-2",
        name="AlpacaEval 2.0",
        official_metric="length_controlled_win_rate",
        source_url="https://github.com/tatsu-lab/alpaca_eval",
        protocol_adapter="alpaca_eval",
        code_license="Apache-2.0",
        data_license="CC-BY-NC-4.0",
        original_moa=True,
        supplemental=False,
        customer_redistributable=False,
    ),
    "mt-bench": BenchmarkDefinition(
        id="mt-bench",
        name="MT-Bench",
        official_metric="turn_score_1_to_10",
        source_url="https://github.com/lm-sys/FastChat/tree/main/fastchat/llm_judge",
        protocol_adapter="fastchat_mt_bench",
        code_license="Apache-2.0",
        data_license="upstream-terms",
        original_moa=True,
        supplemental=False,
        customer_redistributable=False,
    ),
    "flask": BenchmarkDefinition(
        id="flask",
        name="FLASK",
        official_metric="fine_grained_skill_score",
        source_url="https://github.com/kaistAI/FLASK",
        protocol_adapter="flask_12_skill",
        code_license="upstream-unspecified",
        data_license="upstream-terms",
        original_moa=True,
        supplemental=False,
        customer_redistributable=False,
    ),
    "arena-hard": BenchmarkDefinition(
        id="arena-hard",
        name="Arena-Hard-Auto",
        official_metric="style_controlled_pairwise_win_rate",
        source_url="https://github.com/lmarena/arena-hard-auto",
        protocol_adapter="arena_hard_auto",
        code_license="Apache-2.0",
        data_license="upstream-terms",
        original_moa=False,
        supplemental=True,
        customer_redistributable=False,
    ),
}


@dataclass(frozen=True)
class ImportedBenchmark:
    benchmark: BenchmarkDefinition
    source_path: str
    source_sha256: str
    cases: tuple[EvaluationCase, ...]


def load_benchmark_jsonl(
    path: str | Path,
    *,
    benchmark_id: str,
    prompt_field: str,
    id_field: str,
    category_field: str | None = None,
) -> ImportedBenchmark:
    """Normalize a pinned local JSONL file; never fetch benchmark data implicitly."""
    if benchmark_id not in BENCHMARKS:
        raise ValueError("unknown benchmark id")
    source = Path(path).resolve()
    raw = source.read_bytes()
    if len(raw) > 100_000_000:
        raise ValueError("benchmark source exceeds the 100 MB import limit")
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        prompt = item.get(prompt_field)
        identifier = item.get(id_field)
        if not isinstance(prompt, str) or not prompt.strip() or identifier is None:
            raise ValueError(f"line {line_number} is missing prompt or id")
        category = (
            str(item.get(category_field) or "general") if category_field else "general"
        )
        cases.append(
            EvaluationCase(
                id=f"{benchmark_id}:{identifier}",
                prompt=prompt,
                category=category,
                tags=("benchmark", benchmark_id),
                metadata={"sourceLine": line_number},
            )
        )
    if not cases:
        raise ValueError("benchmark source contains no cases")
    return ImportedBenchmark(
        benchmark=BENCHMARKS[benchmark_id],
        source_path=str(source),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
    )
