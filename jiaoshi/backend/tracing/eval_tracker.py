# -*- coding: utf-8 -*-
"""
评估追踪器 (EvalTracker)
========================
将 Ragas 评估结果自动上报到 LangSmith，实现评估可视化。

核心能力：
  - upload_eval_results:  将 RAG 评估结果批量上报
  - create_experiment:    在 LangSmith 中创建对照实验
  - compare_experiments:  对比两次评估结果的差异

上报数据结构（LangSmith Feedback）：
  - score:     指标分数 (0-1)
  - key:       指标名称 (faithfulness / context_recall 等)
  - comment:   评估说明
  - run_id:    关联的 Trace Run ID

用法:
    from backend.tracing.eval_tracker import EvalTracker

    tracker = EvalTracker()
    tracker.upload_eval_results(eval_data, run_id="...")
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("tracing.eval_tracker")


class EvalTracker:
    """
    评估追踪器，将 Ragas 评估结果上报到 LangSmith。

    用法:
        tracker = EvalTracker()
        tracker.upload_eval_results(
            eval_data={"aggregate": {...}, "details": [...]},
            experiment_name="baseline-v1",
        )
    """

    def __init__(self):
        self._langsmith_available = self._check_langsmith()
        self._local_log_dir = ROOT / "logs" / "evals"
        self._local_log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 检测
    # ------------------------------------------------------------------

    @staticmethod
    def _check_langsmith() -> bool:
        """检测 LangSmith 是否可用"""
        try:
            from langsmith import Client
            api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
            if api_key:
                return True
            env_path = ROOT / "backend" / ".env"
            if env_path.exists() and "LANGCHAIN_API_KEY" in env_path.read_text(encoding="utf-8"):
                return True
            return False
        except ImportError:
            return False

    def _get_client(self):
        """获取 LangSmith 客户端"""
        try:
            from langsmith import Client
            return Client()
        except Exception as e:
            logger.warning(f"LangSmith 客户端初始化失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def upload_eval_results(
        self,
        eval_data: dict,
        experiment_name: str = None,
        run_id: str = None,
        dataset_name: str = "rag-evaluation",
    ) -> dict:
        """
        将评估结果上报到 LangSmith。

        参数:
            eval_data:        评估结果 dict（来自 RAGEvaluator.evaluate_batch()）
            experiment_name:  实验名称
            run_id:           关联的 Trace Run ID
            dataset_name:     LangSmith Dataset 名称

        返回:
            {"success": True/False, "uploaded_to": "langsmith" | "local"}
        """
        experiment_name = experiment_name or f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        aggregate = eval_data.get("aggregate", {})
        pass_rate = eval_data.get("pass_rate", {})
        details = eval_data.get("details", [])
        total_samples = eval_data.get("total_samples", len(details))

        if self._langsmith_available:
            return self._upload_to_langsmith(aggregate, pass_rate, details, experiment_name, run_id, dataset_name)
        else:
            return self._save_local(aggregate, pass_rate, total_samples, experiment_name)

    def create_experiment(
        self,
        experiment_name: str,
        eval_results: list[dict],
        metadata: dict = None,
    ) -> Optional[str]:
        """
        在 LangSmith 中创建对照实验。

        参数:
            experiment_name: 实验名称
            eval_results:    多个 eval_data 的列表（每次评估的结果）
            metadata:        实验元数据

        返回:
            实验 ID，失败返回 None
        """
        if not self._langsmith_available:
            logger.warning("LangSmith 不可用，实验创建仅记录到本地")
            self._save_local_experiment(experiment_name, eval_results, metadata)
            return None

        client = self._get_client()
        if not client:
            return None

        try:
            experiment = client.create_experiment(
                name=experiment_name,
                metadata=metadata or {},
            )
            logger.info(f"LangSmith 实验已创建: {experiment_name} (id={experiment.id})")
            return experiment.id
        except Exception as e:
            logger.error(f"创建实验失败: {e}")
            return None

    def compare_experiments(
        self,
        experiment_ids: list[str],
    ) -> dict:
        """
        对比多个实验的结果。

        返回:
            {"comparison": [...], "best_experiment": "..."}
        """
        if not self._langsmith_available:
            return {"comparison": [], "best_experiment": "", "note": "LangSmith 不可用"}

        client = self._get_client()
        if not client:
            return {"comparison": [], "best_experiment": ""}

        results = []
        try:
            for exp_id in experiment_ids:
                exp = client.read_experiment(exp_id)
                results.append({
                    "id": exp_id,
                    "name": exp.name,
                    "results": exp.results if hasattr(exp, 'results') else {},
                })

            # 找出最佳实验（按 average_score）
            best = max(results, key=lambda r: r.get("results", {}).get("average_score", 0)) if results else {}
            return {"comparison": results, "best_experiment": best.get("name", "")}
        except Exception as e:
            logger.error(f"对比实验失败: {e}")
            return {"comparison": [], "best_experiment": ""}

    # ------------------------------------------------------------------
    # LangSmith 上报
    # ------------------------------------------------------------------

    def _upload_to_langsmith(
        self,
        aggregate: dict,
        pass_rate: dict,
        details: list,
        experiment_name: str,
        run_id: str,
        dataset_name: str,
    ) -> dict:
        """将评估结果上报到 LangSmith"""
        client = self._get_client()
        if not client:
            return self._save_local(aggregate, pass_rate, len(details), experiment_name)

        uploaded = 0
        errors = 0

        try:
            # 1. 确保 Dataset 存在
            try:
                dataset = client.read_dataset(dataset_name=dataset_name)
            except Exception:
                dataset = client.create_dataset(dataset_name=dataset_name)

            # 2. 为每个聚合指标创建 Feedback
            metric_labels = {
                "context_recall": "上下文召回率",
                "context_precision": "上下文精确率",
                "faithfulness": "忠实度",
                "answer_relevancy": "答案相关性",
                "composite_score": "综合得分",
            }

            for metric, score in aggregate.items():
                try:
                    client.create_feedback(
                        run_id=run_id,
                        key=metric,
                        score=float(score),
                        comment=f"[{experiment_name}] {metric_labels.get(metric, metric)}: {score:.4f}",
                    )
                    uploaded += 1
                except Exception:
                    # 使用 trace-less feedback（无需 run_id）
                    try:
                        client.create_feedback(
                            key=f"{metric}_aggregate",
                            score=float(score),
                            comment=f"[{experiment_name}] {metric_labels.get(metric, metric)}: {score:.4f}",
                        )
                        uploaded += 1
                    except Exception:
                        errors += 1

            # 3. 上报达标率
            for metric, rate in pass_rate.items():
                try:
                    client.create_feedback(
                        key=f"{metric}_pass_rate",
                        score=float(rate),
                        comment=f"[{experiment_name}] {metric} 达标率: {rate:.1%}",
                    )
                    uploaded += 1
                except Exception:
                    errors += 1

            logger.info(f"LangSmith 上报完成: {uploaded} 成功, {errors} 失败")
            return {"success": True, "uploaded_to": "langsmith", "uploaded_count": uploaded, "error_count": errors}

        except Exception as e:
            logger.error(f"LangSmith 上报失败: {e}")
            return self._save_local(aggregate, pass_rate, len(details), experiment_name)

    # ------------------------------------------------------------------
    # 本地回退
    # ------------------------------------------------------------------

    def _save_local(self, aggregate: dict, pass_rate: dict, total_samples: int,
                     experiment_name: str) -> dict:
        """将评估结果保存到本地日志"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "experiment_name": experiment_name,
            "total_samples": total_samples,
            "aggregate": aggregate,
            "pass_rate": pass_rate,
        }
        log_file = self._local_log_dir / f"eval_{experiment_name}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        logger.info(f"评估结果已保存到本地: {log_file}")
        return {"success": True, "uploaded_to": "local", "local_path": str(log_file)}

    def _save_local_experiment(self, name: str, results: list, metadata: dict = None):
        """保存实验到本地"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "experiment_name": name,
            "metadata": metadata or {},
            "eval_runs": len(results),
        }
        log_file = self._local_log_dir / f"experiment_{name}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        logger.info(f"实验记录已保存到本地: {log_file}")

    def get_dashboard_url(self) -> Optional[str]:
        """获取 LangSmith 控制台 URL"""
        if not self._langsmith_available:
            return None
        project = os.environ.get("LANGCHAIN_PROJECT", "exam-paper-generator")
        return f"https://smith.langchain.com/o/{self._get_org_id()}/projects/{project}"

    def _get_org_id(self) -> str:
        """获取组织 ID"""
        try:
            from langsmith import Client
            client = Client()
            # 通过 API 获取当前组织
            return os.environ.get("LANGCHAIN_ORG_ID", "default")
        except Exception:
            return "default"


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def upload_eval_results(eval_data: dict, experiment_name: str = None, run_id: str = None) -> dict:
    """便捷函数：上报评估结果"""
    tracker = EvalTracker()
    return tracker.upload_eval_results(eval_data, experiment_name, run_id)


def get_dashboard_url() -> Optional[str]:
    """便捷函数：获取 LangSmith 控制台 URL"""
    tracker = EvalTracker()
    return tracker.get_dashboard_url()


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    tracker = EvalTracker()

    print("=" * 60)
    print("评估追踪器状态")
    print("=" * 60)
    print(f"  LangSmith 可用: {tracker._langsmith_available}")

    dashboard = tracker.get_dashboard_url()
    print(f"  控制台 URL: {dashboard or '(未配置 — 设置 LANGCHAIN_API_KEY)'}")

    # 测试本地回退
    mock_eval = {
        "aggregate": {
            "context_recall": 0.78,
            "context_precision": 0.65,
            "faithfulness": 0.82,
            "answer_relevancy": 0.71,
            "composite_score": 0.75,
        },
        "pass_rate": {
            "context_recall": 0.85,
            "context_precision": 0.72,
            "faithfulness": 0.90,
            "answer_relevancy": 0.80,
            "composite_score": 0.80,
        },
        "total_samples": 50,
        "details": [],
    }

    result = tracker.upload_eval_results(mock_eval, experiment_name="baseline-v1")
    print(f"\n上报结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
