# -*- coding: utf-8 -*-
"""
RAG 评估运行脚本 (run_evals.py)
===============================
运行完整 RAG 评估流程，生成 HTML 评估报告。

流程:
  1. 加载/构建 Golden Set（测试数据集）
  2. 对每条样本执行检索和生成质量评估
  3. 聚合统计各项指标
  4. 生成 HTML 报告

用法:
  # 完整流程（构建 Golden Set + 评估 + 报告）
  python tests/rag_evaluation/run_evals.py

  # 使用已有 Golden Set 快速评估
  python tests/rag_evaluation/run_evals.py --golden-set tests/rag_evaluation/golden_set.json

  # 生成 CI 友好输出
  python tests/rag_evaluation/run_evals.py --ci
"""

import json
import os
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

from tests.rag_evaluation.evaluator import RAGEvaluator
from tests.rag_evaluation.test_dataset_builder import TestDatasetBuilder, load_golden_set, TARGET_SIZE

logger = logging.getLogger("rag_evaluation.run_evals")

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
GOLDEN_SET_PATH = ROOT / "tests" / "rag_evaluation" / "golden_set.json"
REPORT_DIR = ROOT / "test_reports"
REPORT_PATH = REPORT_DIR / "rag_report.html"

# ---------------------------------------------------------------------------
# 阈值设置
# ---------------------------------------------------------------------------
PASS_THRESHOLDS = {
    "context_recall": 0.55,
    "context_precision": 0.55,
    "faithfulness": 0.55,
    "answer_relevancy": 0.50,
    "composite_score": 0.55,
}


# ===================================================================
# 评估流程
# ===================================================================

def run_evaluation(
    golden_set: list[dict] = None,
    use_ragas: bool = False,
    golden_set_path: Path = None,
) -> dict:
    """
    运行完整评估流程。

    返回:
        {
            "timestamp": "...",
            "total_samples": N,
            "aggregate": {...},
            "pass_rate": {...},
            "by_subject": {...},
            "details": [...],
        }
    """
    logger.info("=" * 60)
    logger.info("RAG 评估流程启动")
    logger.info("=" * 60)

    # ---- 1. 加载 Golden Set ----
    if golden_set is None:
        if golden_set_path and golden_set_path.exists():
            golden_set = load_golden_set(golden_set_path)
        elif GOLDEN_SET_PATH.exists():
            golden_set = load_golden_set(GOLDEN_SET_PATH)

    if not golden_set:
        logger.info("Golden Set 不存在，开始构建...")
        builder = TestDatasetBuilder()
        golden_set = builder.build(TARGET_SIZE)
        builder.save(golden_set)

    logger.info(f"Golden Set: {len(golden_set)} 条")

    # ---- 2. 初始化评估器 ----
    evaluator = RAGEvaluator(use_ragas=use_ragas)
    eval_mode = "Ragas" if evaluator.use_ragas else "启发式"
    logger.info(f"评估模式: {eval_mode}")

    # ---- 3. 逐条评估 ----
    results = []
    start_time = time.time()

    for i, sample in enumerate(golden_set):
        if (i + 1) % 20 == 0:
            elapsed = time.time() - start_time
            logger.info(f"  进度: {i+1}/{len(golden_set)} (耗时 {elapsed:.1f}s)")

        try:
            result = evaluator.evaluate_full(
                query=sample["query"],
                answer=sample.get("answer", ""),
                retrieved_docs=sample.get("retrieved_docs", []),
                ground_truth_docs=sample.get("ground_truth_docs", []),
                contexts=sample.get("retrieved_docs", []),  # 默认与 retrieved 相同
            )
            result["sample_id"] = sample.get("id", "")
            result["subject"] = sample.get("subject", "unknown")
            result["grade"] = sample.get("grade", "")
            results.append(result)
        except Exception as e:
            logger.warning(f"样本 {sample.get('id', '?')} 评估失败: {e}")
            results.append({
                "sample_id": sample.get("id", ""),
                "subject": sample.get("subject", "unknown"),
                "retrieval": {"context_recall": 0, "context_precision": 0, "mode": "error"},
                "generation": {"faithfulness": 0, "answer_relevancy": 0, "mode": "error"},
                "composite_score": 0,
                "error": str(e),
            })

    total_time = time.time() - start_time
    logger.info(f"评估完成: {len(results)} 条 / {total_time:.1f}s")

    # ---- 4. 聚合统计 ----
    aggregate, pass_rate, by_subject = _aggregate(results)

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "eval_mode": eval_mode,
        "total_samples": len(results),
        "elapsed_seconds": round(total_time, 1),
        "aggregate": aggregate,
        "pass_rate": pass_rate,
        "by_subject": by_subject,
        "details": results,
    }


def _aggregate(results: list[dict]) -> tuple[dict, dict, dict]:
    """聚合评估结果为统计值"""
    metrics = ["context_recall", "context_precision", "faithfulness", "answer_relevancy", "composite_score"]
    n = len(results)

    # 聚合均值
    aggregate = {}
    for metric in metrics:
        values = []
        for r in results:
            if metric in ("context_recall", "context_precision"):
                values.append(r.get("retrieval", {}).get(metric, 0))
            elif metric in ("faithfulness", "answer_relevancy"):
                values.append(r.get("generation", {}).get(metric, 0))
            else:
                values.append(r.get(metric, 0))
        aggregate[metric] = round(sum(values) / n, 4) if n > 0 else 0

    # 达标率
    pass_rate = {}
    for metric, threshold in PASS_THRESHOLDS.items():
        count = 0
        for r in results:
            score = r.get(metric, 0)
            if metric in ("context_recall", "context_precision"):
                score = r.get("retrieval", {}).get(metric, 0)
            elif metric in ("faithfulness", "answer_relevancy"):
                score = r.get("generation", {}).get(metric, 0)
            if score >= threshold:
                count += 1
        pass_rate[metric] = round(count / n, 4) if n > 0 else 0

    # 按学科统计
    by_subject = defaultdict(lambda: {"count": 0, "composite_avg": 0.0, "scores": []})
    for r in results:
        subj = r.get("subject", "unknown")
        by_subject[subj]["count"] += 1
        by_subject[subj]["scores"].append(r.get("composite_score", 0))
    for subj, data in by_subject.items():
        data["composite_avg"] = round(sum(data["scores"]) / len(data["scores"]), 4) if data["scores"] else 0
        del data["scores"]

    return aggregate, pass_rate, dict(by_subject)


# ===================================================================
# HTML 报告生成
# ===================================================================

def generate_html_report(eval_result: dict, output_path: Path = None) -> Path:
    """生成 HTML 评估报告"""
    if output_path is None:
        output_path = REPORT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)

    agg = eval_result.get("aggregate", {})
    pr = eval_result.get("pass_rate", {})
    by_subj = eval_result.get("by_subject", {})

    # 颜色判定
    def color(score: float, threshold: float = 0.6) -> str:
        if score >= 0.8:
            return "#27ae60"
        elif score >= threshold:
            return "#f39c12"
        else:
            return "#e74c3c"

    def grade(score: float) -> str:
        if score >= 0.8:
            return "A"
        elif score >= 0.6:
            return "B"
        elif score >= 0.4:
            return "C"
        else:
            return "D"

    def bar_html(label: str, score: float, threshold: float) -> str:
        pct = int(score * 100)
        c = color(score, threshold)
        return f"""
        <div class="metric-bar">
            <div class="metric-label">{label}</div>
            <div class="bar-track">
                <div class="bar-fill" style="width:{pct}%;background:{c}"></div>
            </div>
            <div class="metric-value" style="color:{c}">{score:.3f}</div>
            <div class="metric-threshold">(阈值: {threshold:.2f})</div>
        </div>"""

    bars = ""
    for metric, threshold in PASS_THRESHOLDS.items():
        label_map = {
            "context_recall": "上下文召回率",
            "context_precision": "上下文精确率",
            "faithfulness": "忠实度",
            "answer_relevancy": "答案相关性",
            "composite_score": "综合得分",
        }
        score = agg.get(metric, 0)
        bars += bar_html(label_map.get(metric, metric), score, threshold)

    # 学科分布表
    subject_rows = ""
    for subj, data in sorted(by_subj.items()):
        g = grade(data.get("composite_avg", 0))
        subject_rows += f"""
        <tr>
            <td>{subj}</td>
            <td>{data.get('count', 0)}</td>
            <td style="color:{color(data.get('composite_avg', 0))}">{data.get('composite_avg', 0):.3f}</td>
            <td><span class="grade grade-{g.lower()}">{g}</span></td>
        </tr>"""

    # 达标率概览卡
    pass_cards = ""
    for metric, threshold in PASS_THRESHOLDS.items():
        rate = pr.get(metric, 0)
        pct = int(rate * 100)
        c = color(rate, 0.7)
        label_map = {
            "context_recall": "检索召回", "context_precision": "检索精准",
            "faithfulness": "答案忠实", "answer_relevancy": "答案相关",
            "composite_score": "综合评价",
        }
        pass_cards += f"""
        <div class="pass-card">
            <div class="pass-rate" style="color:{c}">{pct}%</div>
            <div class="pass-label">{label_map.get(metric, metric)}</div>
            <div class="pass-sub">达标率 (≥{threshold:.2f})</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>RAG 评估报告</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f6fa; color: #2c3e50; padding: 24px; }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header {{ background: linear-gradient(135deg, #1a5276, #2980b9); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
    .header h1 {{ font-size: 24px; margin-bottom: 8px; }}
    .header .meta {{ font-size: 13px; opacity: 0.85; }}

    .section {{ background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
    .section h2 {{ font-size: 18px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #eee; }}

    /* 达标率卡片 */
    .pass-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
    .pass-card {{ text-align: center; padding: 16px 8px; background: #f8f9fa; border-radius: 8px; }}
    .pass-rate {{ font-size: 28px; font-weight: bold; margin-bottom: 4px; }}
    .pass-label {{ font-size: 13px; color: #555; }}
    .pass-sub {{ font-size: 11px; color: #999; margin-top: 4px; }}

    /* 进度条 */
    .metric-bar {{ display: flex; align-items: center; margin: 12px 0; gap: 12px; }}
    .metric-label {{ width: 120px; font-size: 14px; font-weight: 500; text-align: right; }}
    .bar-track {{ flex: 1; height: 24px; background: #eee; border-radius: 12px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 12px; transition: width 0.3s; }}
    .metric-value {{ width: 60px; font-weight: bold; font-size: 14px; text-align: center; }}
    .metric-threshold {{ width: 100px; font-size: 12px; color: #999; }}

    /* 表格 */
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }}
    th {{ background: #f8f9fa; font-weight: 600; }}

    /* 等级 */
    .grade {{ padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
    .grade-a {{ background: #d5f5e3; color: #27ae60; }}
    .grade-b {{ background: #fef9e7; color: #f39c12; }}
    .grade-c {{ background: #fadbd8; color: #e74c3c; }}
    .grade-d {{ background: #f5b7b1; color: #c0392b; }}

    .footer {{ text-align: center; margin-top: 32px; padding: 16px; font-size: 12px; color: #999; }}
</style>
</head>
<body>
<div class="container">

    <div class="header">
        <h1>RAG 检索与生成质量评估报告</h1>
        <div class="meta">
            评估时间: {eval_result.get('timestamp', '')} |
            样本数: {eval_result.get('total_samples', 0)} |
            评估模式: {eval_result.get('eval_mode', 'unknown')} |
            耗时: {eval_result.get('elapsed_seconds', 0)}s
        </div>
    </div>

    <div class="section">
        <h2>指标达标率</h2>
        <div class="pass-grid">
            {pass_cards}
        </div>
    </div>

    <div class="section">
        <h2>综合指标得分</h2>
        {bars}
    </div>

    <div class="section">
        <h2>按学科统计</h2>
        <table>
            <thead>
                <tr>
                    <th>学科</th>
                    <th>样本数</th>
                    <th>综合得分</th>
                    <th>评级</th>
                </tr>
            </thead>
            <tbody>
                {subject_rows}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>评估说明</h2>
        <table>
            <tr><td>context_recall</td><td>上下文召回率：检索到的信息是否全面覆盖了 ground truth</td></tr>
            <tr><td>context_precision</td><td>上下文精确率：检索到的信息是否精准（少噪声）</td></tr>
            <tr><td>faithfulness</td><td>忠实度：生成的答案是否忠实于检索到的上下文</td></tr>
            <tr><td>answer_relevancy</td><td>答案相关性：答案与问题的相关程度</td></tr>
            <tr><td>composite_score</td><td>综合得分：检索(40%) + 生成(60%) 加权</td></tr>
        </table>
    </div>

    <div class="footer">
        智能试卷生成系统 · RAG 评估模块 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>

</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"HTML 报告已生成: {output_path}")
    return output_path


# ===================================================================
# CI 集成
# ===================================================================

def evaluate_for_ci(eval_result: dict) -> tuple[bool, str]:
    """
    为 CI 管道提供通过/失败判定。

    返回:
        (passed, message)
    """
    aggregate = eval_result.get("aggregate", {})

    # 硬性阈值：综合得分必须 ≥ 0.45
    composite = aggregate.get("composite_score", 0)
    if composite < 0.45:
        return False, f"综合得分 {composite:.3f} < 阈值 0.45 — FAIL"

    # 任何单指标 < 0.35 视为失败
    for metric in ["context_recall", "context_precision", "faithfulness"]:
        if aggregate.get(metric, 0) < 0.35:
            return False, f"{metric} = {aggregate[metric]:.3f} < 阈值 0.35 — FAIL"

    # 达标率检查
    pass_rate = eval_result.get("pass_rate", {})
    overall_pass = pass_rate.get("composite_score", 0)
    if overall_pass < 0.5:
        return False, f"综合达标率 {overall_pass:.1%} < 50% — FAIL"

    return True, f"综合得分 {composite:.3f}，达标率 {overall_pass:.1%} — PASS"


# ===================================================================
# JSON 报告（机器可读）
# ===================================================================

def save_json_report(eval_result: dict, output_path: Path = None) -> Path:
    if output_path is None:
        output_path = REPORT_DIR / "rag_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 去除 details（太大），保留聚合数据
    summary = {k: v for k, v in eval_result.items() if k != "details"}
    summary["detail_count"] = len(eval_result.get("details", []))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info(f"JSON 报告已保存: {output_path}")
    return output_path


# ===================================================================
# 主入口
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="RAG 评估运行脚本")
    parser.add_argument("--golden-set", type=str, help="Golden Set JSON 路径")
    parser.add_argument("--use-ragas", action="store_true", help="尝试使用 Ragas 框架评估")
    parser.add_argument("--ci", action="store_true", help="CI 模式：非零退出码表示失败")
    parser.add_argument("--output", type=str, help="HTML 报告输出路径")
    parser.add_argument("--samples", type=int, default=None, help="评估样本数限制")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    # 加载 Golden Set
    golden_set = None
    if args.golden_set:
        gs_path = Path(args.golden_set)
        if gs_path.exists():
            golden_set = load_golden_set(gs_path)
            logger.info(f"加载 Golden Set: {gs_path} ({len(golden_set)} 条)")

    if args.samples and golden_set:
        golden_set = golden_set[:args.samples]

    # 运行评估
    result = run_evaluation(golden_set, use_ragas=args.use_ragas)

    # 输出报告
    html_path = Path(args.output) if args.output else REPORT_PATH
    generate_html_report(result, html_path)

    # JSON 报告
    save_json_report(result)

    # 控制台摘要
    agg = result["aggregate"]
    pr = result["pass_rate"]
    print("\n" + "=" * 50)
    print("RAG 评估结果摘要")
    print("=" * 50)
    print(f"  样本数: {result['total_samples']}")
    print(f"  评估模式: {result['eval_mode']}")
    print(f"  耗时: {result['elapsed_seconds']}s")
    print(f"  综合得分: {agg.get('composite_score', 0):.3f}")
    print(f"  检索召回率: {agg.get('context_recall', 0):.3f} (达标 {pr.get('context_recall', 0):.0%})")
    print(f"  检索精确率: {agg.get('context_precision', 0):.3f} (达标 {pr.get('context_precision', 0):.0%})")
    print(f"  忠实度:     {agg.get('faithfulness', 0):.3f} (达标 {pr.get('faithfulness', 0):.0%})")
    print(f"  答案相关性: {agg.get('answer_relevancy', 0):.3f} (达标 {pr.get('answer_relevancy', 0):.0%})")
    print(f"  报告: {html_path}")
    print("=" * 50)

    if args.ci:
        passed, msg = evaluate_for_ci(result)
        print(f"\nCI 判定: {'PASS' if passed else 'FAIL'} — {msg}")
        return 0 if passed else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
