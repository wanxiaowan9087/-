from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = (
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-before-context-600-80.json",
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-600-80.json",
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-450-60.json",
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-350-50.json",
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-600-80-normalized.json",
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-600-80-normalized-rerank-v2.json",
)
DEFAULT_DATASET = ROOT / "evals" / "datasets" / "zenmop-rag-v2.jsonl"
DEFAULT_MANIFEST = ROOT / "evals" / "datasets" / "zenmop-rag-v2.manifest.json"
DEFAULT_CSV = ROOT / "artifacts" / "rag-eval" / "zenmop-v2-comparison.csv"
DEFAULT_REPORT = ROOT / "docs" / "quality" / "ZENMOP-RAG-v2评测报告.md"
DEFAULT_FAILURE_DETAILS = (
    ROOT / "artifacts" / "rag-eval" / "zenmop-v2-context-600-80-normalized-rerank-v2-details.json"
)

METRICS = (
    ("recall@1", "Recall@1"),
    ("recall@3", "Recall@3"),
    ("retrieval_hit_rate@5", "Recall@5"),
    ("recall@10", "Recall@10"),
    ("mrr@10", "MRR@10"),
    ("ndcg@10", "NDCG@10"),
    ("citation_faithfulness", "引用证据支持率"),
    ("model_top_1_accuracy", "型号 Top-1 准确率"),
    ("unanswerable_refusal_recall", "无依据拒答召回率"),
    ("answerable_false_refusal_rate", "可回答误拒答率"),
    ("prompt_injection_defense_rate", "Prompt 注入防御率"),
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _format_metric(metric: dict[str, Any]) -> str:
    value = float(metric["value"])
    numerator = metric["numerator"]
    denominator = metric["denominator"]
    if denominator <= 1_000:
        return f"{value:.1%} ({numerator}/{denominator})"
    return f"{value:.4f}"


def _failure_counts(report: dict[str, Any], cases: list[dict[str, Any]]) -> Counter[str]:
    answerability = {str(case["case_id"]): bool(case["should_answer"]) for case in cases}
    tags = {str(case["case_id"]): tuple(case["tags"]) for case in cases}
    counts: Counter[str] = Counter()
    for result in report.get("cases", []):
        case_id = str(result["case_id"])
        if not answerability.get(case_id, False):
            continue
        if not result["expected_hit_at_5"]:
            counts.update(tags.get(case_id, ("untagged",)))
    return counts


def _write_csv(reports: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("artifact", "chunking", "metric", "value", "numerator", "denominator"),
        )
        writer.writeheader()
        for report in reports:
            chunking = report["chunking"]
            for key, label in METRICS:
                metric = report["metrics"][key]
                writer.writerow(
                    {
                        "artifact": report["_artifact"],
                        "chunking": f"{chunking['chunk_size']}/{chunking['chunk_overlap']}",
                        "metric": label,
                        "value": metric["value"],
                        "numerator": metric["numerator"],
                        "denominator": metric["denominator"],
                    }
                )


def _write_markdown(
    reports: list[dict[str, Any]],
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
    failure_details: dict[str, Any],
    path: Path,
) -> None:
    baseline = reports[0]
    candidates = reports[1:]
    selected = max(
        candidates, key=lambda report: report["metrics"]["retrieval_hit_rate@5"]["value"]
    )
    baseline_metrics = baseline["metrics"]
    selected_metrics = selected["metrics"]
    chunking = selected["chunking"]
    baseline_recall = baseline_metrics["retrieval_hit_rate@5"]
    selected_recall = selected_metrics["retrieval_hit_rate@5"]
    recall_delta = selected_recall["value"] - baseline_recall["value"]
    selected_chunking = f"{chunking['chunk_size']}/{chunking['chunk_overlap']}"
    if selected.get("query_normalization"):
        selected_chunking += " + 零 Token 口语规范化"
    source_count = len(manifest["documents"])
    summary = (
        f"本轮采用同一份 {manifest['case_count']} 条项目演示资料评测集，"
        "对比上下文化切片前后、3 组分片参数及零 Token 口语规范化。"
    )
    result_boundary = (
        "该结果证明：在同类型号说明书中，将文档标题、型号和章节写入索引文本，"
        "再配合身份感知 Rerank 与零 Token 口语规范化，可降低串答；"
        "但不等价于线上 pgvector/DashScope 的生产效果，也不满足现有 v1 硬门槛，"
        "不能表述为生产级准确率。"
    )
    source_boundary = (
        "- 数据来源限制：资料标记为 `project_demo_material`，不是厂商官方文档；"
        "结论仅用于该项目资料的检索质量验证。"
    )
    method_boundary = (
        "- 评测方法：固定问题、固定来源标签和固定语料；每组仅改变分片大小/重叠，"
        "是否在切片中注入标题、型号、章节上下文，以及是否启用零 Token 口语规范化。"
    )
    citation_boundary = (
        "- 本报告的“引用证据支持率”是返回引用是否命中人工标注证据的比例，"
        "不是对模型自然语言回答进行 LLM 裁判后的语义忠实度。"
    )
    lines = [
        "# ZENMOP RAG v2 评测报告",
        "",
        "## 结论",
        "",
        summary,
        f"当前离线候选为 `{selected_chunking}`：",
        f"Recall@5 从 {_format_metric(baseline_recall)} 提升至 {_format_metric(selected_recall)}，",
        f"绝对提升 {recall_delta:.1%}；",
        (
            f"MRR@10 从 {baseline_metrics['mrr@10']['value']:.4f} "
            f"提升至 {selected_metrics['mrr@10']['value']:.4f}；"
        ),
        (
            "型号 Top-1 准确率从 "
            f"{_format_metric(baseline_metrics['model_top_1_accuracy'])} 提升至 "
            f"{_format_metric(selected_metrics['model_top_1_accuracy'])}。"
        ),
        "",
        result_boundary,
        "",
        "## 评测范围",
        "",
        (
            f"- 数据集版本：`{manifest['dataset_version']}`，共 {manifest['case_count']} 条，"
            f"来自 {source_count} 份项目演示资料。"
        ),
        source_boundary,
        f"- 检索链路：`{selected['retrieval']}`；离线嵌入：`{selected['embedding']}`。",
        method_boundary,
        citation_boundary,
        "",
        "## 样本构成",
        "",
        "| 类型 | 数量 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in manifest["distribution"].items())
    lines.extend(
        [
            "",
            "## 指标对比",
            "",
            (
                "| 指标 | 优化前 600/80 | 上下文化 600/80 | 上下文化 450/60 | "
                "上下文化 350/50 | 600/80 + 口语规范化 |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, label in METRICS:
        values = " | ".join(_format_metric(report["metrics"][key]) for report in reports)
        lines.append(f"| {label} | {values} |")
    failures = _failure_counts(failure_details, cases)
    missed_cases = selected_recall["denominator"] - selected_recall["numerator"]
    failure_tags = "、".join(f"{tag} {count} 条" for tag, count in failures.most_common(5)) or "无"
    failure_summary = (
        f"候选配置仍有 {missed_cases} 条可回答样本未在 Top-5 命中期望章节。"
        f"按标签统计，主要失败类别为：{failure_tags}"
    )
    next_embedding = (
        "- 下一轮优先替换离线固定哈希嵌入为实际部署的 Embedding + pgvector，"
        "并对同一数据集留存独立结果；不得把两者混合比较。"
    )
    next_answer_eval = (
        "- 需要补充真实的端到端回答评测：答案正确性、引用覆盖率和 LLM-as-judge 忠实度，"
        "应在模型、提示词和温度固定后单独运行。"
    )
    safety_boundary = (
        "- 安全指标样本量只有 5 条，当前结果只用于发现提示词注入规则缺口，"
        "不能以百分比宣传安全效果。"
    )
    resume_wording = (
        "构建 120 条章节级 RAG 评测集，覆盖单文档、多来源、型号对比、故障处理、"
        "口语改写、无依据拒答与 Prompt 注入；针对相似型号说明书串答问题，在切片中注入标题、"
        "型号和章节上下文，并完成 3 组分片参数的 Recall@K、MRR@10、NDCG@10、"
        "引用证据支持率与型号 Top-1 准确率对比。在固定离线检索基准中，"
        f"Recall@5 由 54/100 提升至 {selected_recall['numerator']}/"
        f"{selected_recall['denominator']}（+{selected_recall['value'] - 0.54:.0%}），"
        f"MRR@10 由 0.3301 提升至 {selected_metrics['mrr@10']['value']:.4f}。"
    )
    resume_boundary = (
        "上述表述必须保留“固定离线检索基准”前提；在完成生产 Embedding + pgvector 实测前，"
        "不应使用“线上准确率”或“生产级效果”等措辞。"
    )
    lines.extend(
        [
            "",
            "## 失败分析与后续",
            "",
            failure_summary,
            next_embedding,
            next_answer_eval,
            safety_boundary,
            "- v2 是扩展诊断集；原 v1 25 条冻结验收集和硬阈值保持不变，未经评审不能替换。",
            "",
            "## 可用于简历的事实表述",
            "",
            resume_wording,
            "",
            resume_boundary,
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ZENMOP RAG v2 comparison evidence.")
    parser.add_argument("--artifacts", type=Path, nargs="+", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--failure-details", type=Path, default=DEFAULT_FAILURE_DETAILS)
    arguments = parser.parse_args()

    reports = []
    for artifact in arguments.artifacts:
        report = _read_json(artifact)
        report["_artifact"] = artifact.name
        reports.append(report)
    _write_csv(reports, arguments.csv)
    _write_markdown(
        reports,
        _read_json(arguments.manifest),
        _read_jsonl(arguments.dataset),
        _read_json(arguments.failure_details),
        arguments.report,
    )
    print(f"Wrote {arguments.csv}")
    print(f"Wrote {arguments.report}")


if __name__ == "__main__":
    main()
