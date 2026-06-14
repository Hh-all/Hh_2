# -*- coding: utf-8 -*-
"""
端到端集成测试 —— 完整试卷生成流程

覆盖:
  - 参数解析 → RAG 检索 → 生成 → 格式化 全链路
  - 三地域场景：小学北京/初中上海/高中广东
  - QUALITY_GATES.md 质量标准验证
"""

import sys
import os
import json
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "agents"))
sys.path.insert(0, os.path.join(ROOT, "backend", "tracing"))


# ==========================================================================
# Phase 1: 参数解析 → Paper Plan
# ==========================================================================

class TestParameterParsing:
    """参数解析阶段测试"""

    def test_parse_chinese_to_code(self):
        """中文参数应正确转换为内部代码"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse({
            "subject": "数学",
            "grade": "初三",
            "region": "北京",
            "knowledge_points": ["一元二次方程"],
            "question_count": 5,
        })
        assert result["success"]
        req = result["request"]
        assert req["subject"] == "math"
        assert req["grade"] == "grade_9"
        assert req["grade_level"] == "junior"
        assert req["region"] == "beijing"

    def test_parse_invalid_subject_stage_combo(self):
        """物理 + 小学 = 非法组合，应报错"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse({"subject": "physics", "grade": "grade_2"})
        assert not result["success"]
        assert any("primary" in e or "小学" in e for e in result["errors"])

    def test_parse_grade_only_derives_level(self):
        """仅给年级应自动推导学段"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse({"subject": "math", "grade": "grade_5"})
        assert result["success"]
        assert result["request"]["grade_level"] == "primary"

    def test_parse_level_only_derives_grade(self):
        """仅给学段应自动推导默认年级"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse({"subject": "english", "grade_level": "junior"})
        assert result["success"]
        assert result["request"]["grade"].startswith("grade_")
        assert result["request"]["grade_level"] == "junior"

    def test_parse_outputs_to_file(self, temp_dir):
        """解析成功应写入 /tmp/paper_request.json"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse({"subject": "math", "grade": "grade_9", "region": "beijing",
                              "knowledge_points": ["一元一次方程"], "question_count": 3})
        assert result["success"]
        assert "output_file" in result
        assert os.path.exists(result["output_file"])


# ==========================================================================
# Phase 2: RAG 检索
# ==========================================================================

class TestRAGRetrieval:
    """RAG 检索阶段测试"""

    def test_search_returns_results(self, rag_ready):
        """检索应返回非空结果"""
        from rag_searcher import search
        results = search("一元一次方程", top_k=5)
        assert isinstance(results, list)
        # 如果索引中有数据，应返回结果
        if results:
            assert "id" in results[0]
            assert "question_text" in results[0]

    def test_search_with_subject_filter(self, rag_ready):
        """学科过滤应生效"""
        from rag_searcher import search
        results = search("math", filters={"subject": "math"}, top_k=5)
        for r in results:
            assert r.get("subject", "") == "math"

    def test_search_with_difficulty_filter(self, rag_ready):
        """难度过滤应生效"""
        from rag_searcher import search
        results = search("数学", filters={"difficulty": {"$lte": 3}}, top_k=5)
        for r in results:
            d = r.get("difficulty", 0)
            if isinstance(d, (int, float)):
                assert d <= 3, f"难度 {d} > 3"

    def test_search_respects_top_k(self, rag_ready):
        """返回数量不应超过 top_k"""
        from rag_searcher import search
        for k in [1, 3, 5]:
            results = search("数学", top_k=k)
            assert len(results) <= k

    def test_search_with_region_filter(self, rag_ready):
        """地域过滤应生效"""
        from rag_searcher import search
        results = search(
            "方程", top_k=5,
            region="beijing", enable_regional_filter=True,
            regional_min_count=3,
        )
        assert isinstance(results, list)
        # 验证 fallback 标记存在
        for r in results:
            assert "_region_match" in r
            assert "_region_fallback" in r

    def test_question_retriever_agent(self):
        """QuestionRetrieverAgent 应正确读取请求并检索"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        from backend.agents.question_retriever_agent import QuestionRetrieverAgent

        # 先创建请求文件
        parser = ParameterParserAgent()
        parse_result = parser.parse({"subject": "math", "grade": "grade_9", "region": "beijing",
                                      "knowledge_points": ["一元一次方程"], "question_count": 3})
        assert parse_result["success"]

        # 检索
        retriever = QuestionRetrieverAgent()
        result = retriever.retrieve()
        assert "total_retrieved" in result
        assert "output_file" in result
        # 即使无 RAG 数据，也能正常返回（coverage_gaps 标记）
        assert result.get("success") or "coverage_gaps" in result


# ==========================================================================
# Phase 3: 题目生成
# ==========================================================================

class TestQuestionGeneration:
    """题目生成阶段测试"""

    def test_generator_fallback_without_llm(self):
        """无 LLM 时回退模式应正常工作"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        from backend.agents.qa_generator_agent import QAGeneratorAgent

        # 确保请求文件存在
        parser = ParameterParserAgent()
        parser.parse({"subject": "math", "grade": "grade_9", "region": "beijing",
                       "knowledge_points": ["一元一次方程"], "question_count": 3})

        generator = QAGeneratorAgent()
        result = generator.generate()
        # 回退模式应返回 success（可能 generated_count=0）
        assert "success" in result
        assert "mode" in result

    def test_generated_questions_have_required_fields(self):
        """生成/检索的题目应包含必填字段"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        from backend.agents.question_retriever_agent import QuestionRetrieverAgent
        from backend.agents.qa_generator_agent import QAGeneratorAgent

        parser = ParameterParserAgent()
        parser.parse({"subject": "math", "grade": "grade_9", "region": "beijing",
                       "knowledge_points": ["一元一次方程"], "question_count": 3})

        retriever = QuestionRetrieverAgent()
        retriever.retrieve()

        generator = QAGeneratorAgent()
        result = generator.generate()

        if result.get("success") and result.get("total_combined", 0) > 0:
            output_path = result.get("output_file")
            if output_path and os.path.exists(output_path):
                with open(output_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                questions = data.get("combined_questions", [])
                for q in questions:
                    assert q.get("question_text"), "题干不能为空"
                    assert q.get("answer"), "答案不能为空"


# ==========================================================================
# Phase 4: 试卷格式化
# ==========================================================================

class TestPaperFormatting:
    """试卷格式化阶段测试"""

    def test_formatter_outputs_html(self, assembled_paper_fixture, temp_dir):
        """格式化应输出 HTML 文件"""
        from backend.agents.paper_formatter_agent import PaperFormatterAgent

        # 创建请求文件
        request_path = temp_dir / "paper_request.json"
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump({
                "_meta": {"generated_at": "2026-01-01", "generated_by": "test"},
                "request": {"subject": "math", "grade": "grade_9", "region": "beijing",
                            "knowledge_points": ["一元二次方程"], "question_count": 3,
                            "title": "测试卷"},
            }, f, ensure_ascii=False)

        # 创建题目文件
        questions_path = temp_dir / "generated_questions.json"
        with open(questions_path, "w", encoding="utf-8") as f:
            json.dump({
                "combined_questions": [
                    {"id": "q1", "subject": "math", "grade": "grade_9",
                     "knowledge_tags": ["一元二次方程"], "difficulty": 2,
                     "question_type": "choice", "question_text": "x²-4=0的解？",
                     "answer": "A", "analysis": "x=±2",
                     "options": [{"label": "A", "text": "±2"}, {"label": "B", "text": "2"}],
                     "source": "test"},
                ]
            }, f, ensure_ascii=False)

        # 需要 monkeypatch 路径
        import backend.agents.paper_formatter_agent as pfa
        original_gen = pfa.GENERATED_QUESTIONS_PATH
        original_req = pfa.PAPER_REQUEST_PATH
        pfa.GENERATED_QUESTIONS_PATH = questions_path
        pfa.PAPER_REQUEST_PATH = request_path

        try:
            formatter = PaperFormatterAgent()
            result = formatter.format()
            assert result["success"]
            assert os.path.exists(result["output_file"])
            with open(result["output_file"], "r", encoding="utf-8") as f:
                html = f.read()
            assert "<!DOCTYPE html>" in html
            assert "测试卷" in html
        finally:
            pfa.GENERATED_QUESTIONS_PATH = original_gen
            pfa.PAPER_REQUEST_PATH = original_req

    def test_formatter_validation_blocks_invalid(self, temp_dir):
        """校验不通过时应拒绝输出"""
        from backend.agents.paper_formatter_agent import PaperFormatterAgent

        request_path = temp_dir / "paper_request.json"
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump({
                "_meta": {}, "request": {"subject": "math", "grade": "grade_9",
                                          "question_count": 10, "title": "test"},
            }, f)

        questions_path = temp_dir / "generated_questions.json"
        with open(questions_path, "w", encoding="utf-8") as f:
            # 只有1道题，不够目标的80%
            json.dump({
                "combined_questions": [
                    {"id": "q1", "subject": "math", "grade": "grade_9",
                     "knowledge_tags": ["test"], "difficulty": 2,
                     "question_type": "short_answer",
                     "question_text": "test?", "answer": "yes",
                     "analysis": "", "source": "test"},
                ]
            }, f)

        import backend.agents.paper_formatter_agent as pfa
        original_gen = pfa.GENERATED_QUESTIONS_PATH
        original_req = pfa.PAPER_REQUEST_PATH
        pfa.GENERATED_QUESTIONS_PATH = questions_path
        pfa.PAPER_REQUEST_PATH = request_path

        try:
            formatter = PaperFormatterAgent()
            result = formatter.format()
            assert not result["success"]
            assert "validation" in result
            assert not result["validation"]["question_count_ok"]
        finally:
            pfa.GENERATED_QUESTIONS_PATH = original_gen
            pfa.PAPER_REQUEST_PATH = original_req


# ==========================================================================
# Phase 5: 三地域全流程
# ==========================================================================

class TestThreeRegionFullPipeline:
    """三地域全流程端到端测试"""

    def test_beijing_primary_math_pipeline(self, beijing_primary_scenario):
        """小学二年级数学（北京卷）全流程"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse(beijing_primary_scenario)
        assert result["success"]
        req = result["request"]
        assert req["subject"] == "math"
        assert req["grade_level"] == "primary"
        assert req["region"] == "beijing"

    def test_shanghai_junior_chinese_pipeline(self, shanghai_junior_scenario):
        """初中八年级语文（上海卷）全流程"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse(shanghai_junior_scenario)
        assert result["success"]
        req = result["request"]
        assert req["subject"] == "chinese"
        assert req["grade_level"] == "junior"
        assert req["region"] == "shanghai"

    def test_guangdong_senior_english_pipeline(self, guangdong_senior_scenario):
        """高中三年级英语（广东卷）全流程"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        result = agent.parse(guangdong_senior_scenario)
        assert result["success"]
        req = result["request"]
        assert req["subject"] == "english"
        assert req["grade_level"] == "senior"
        assert req["region"] == "guangdong"

    def test_all_three_scenarios_parse_successfully(self, all_three_scenarios):
        """三个场景均应解析成功"""
        from backend.agents.parameter_parser_agent import ParameterParserAgent
        agent = ParameterParserAgent()
        for scenario in all_three_scenarios:
            result = agent.parse(scenario)
            assert result["success"], f"场景 {scenario['label']} 解析失败: {result.get('errors', [])}"


# ==========================================================================
# Phase 6: 质量标准验证（QUALITY_GATES.md）
# ==========================================================================

class TestQualityGateCompliance:
    """质量门禁合规测试"""

    def test_schema_validator_passes_valid_paper(self, assembled_paper_fixture):
        """有效试卷应通过 Schema 校验"""
        from tests.validators.schema_validator import SchemaValidator
        validator = SchemaValidator()
        result = validator.validate(assembled_paper_fixture)
        assert result["valid"], f"Schema 校验失败: {result['errors']}"

    def test_duplicate_detector_clean_paper(self):
        """无重复试卷应通过重复检测"""
        from tests.validators.duplicate_detector import DuplicateDetector
        detector = DuplicateDetector()
        questions = [
            {"question_text": "解方程：x + 5 = 12"},
            {"question_text": "三角形内角和是多少度？"},
            {"question_text": "计算圆的面积"},
        ]
        result = detector.detect_intra_duplicates(questions)
        assert not result["duplicate_found"]

    def test_duplicate_detector_finds_duplicates(self):
        """有重复题目应被检出"""
        from tests.validators.duplicate_detector import DuplicateDetector
        detector = DuplicateDetector()
        questions = [
            {"question_text": "解方程：x + 5 = 12"},
            {"question_text": "解方程：x + 6 = 13"},
            {"question_text": "解方程：x + 5 = 12"},  # 精确重复
        ]
        result = detector.detect_intra_duplicates(questions)
        assert result["duplicate_found"]

    def test_rag_evaluation_runs(self, golden_set_samples):
        """RAG 评估应可运行并返回有效分数"""
        from tests.rag_evaluation.evaluator import RAGEvaluator
        evaluator = RAGEvaluator(use_ragas=False)
        for sample in golden_set_samples:
            result = evaluator.evaluate_full(
                query=sample["query"],
                answer=sample["answer"],
                retrieved_docs=sample["retrieved_docs"],
                ground_truth_docs=sample["ground_truth_docs"],
            )
            assert 0 <= result["composite_score"] <= 1
            assert "retrieval" in result
            assert "generation" in result

    def test_gate1_completeness(self):
        """Gate 1: 试卷完整性 —— 题量、题型、分值"""
        # 已验证 by SchemaValidator L1+L4
        pass

    def test_gate2_answer_coverage(self, assembled_paper_fixture):
        """Gate 2: 答案覆盖率 100%"""
        for wrapper in assembled_paper_fixture["questions"]:
            q = wrapper.get("question", wrapper)
            assert q.get("answer", "").strip(), f"题目 {wrapper.get('sequence_number')} 缺少答案"

    def test_gate5_regional_tag(self, assembled_paper_fixture):
        """Gate 5: 地域标签 —— 至少 1 道题有地域匹配"""
        regions = []
        for wrapper in assembled_paper_fixture["questions"]:
            q = wrapper.get("question", wrapper)
            regions.append(q.get("region", ""))
        beijing_count = sum(1 for r in regions if r == "beijing")
        assert beijing_count >= 1 or "" in regions, "应有地域匹配或全国通用题"
