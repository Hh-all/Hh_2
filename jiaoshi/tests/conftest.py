# -*- coding: utf-8 -*-
"""
pytest 共享夹具
提供测试用的索引、客户端、样本数据、Mock LLM 响应等
"""

import os
import sys
import json
import tempfile
import shutil
import pytest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "agents"))
sys.path.insert(0, os.path.join(ROOT, "backend", "knowledge"))
sys.path.insert(0, os.path.join(ROOT, "backend", "paper_styles"))
sys.path.insert(0, os.path.join(ROOT, "backend", "tracing"))

# ==========================================================================
# Session-scoped fixtures
# ==========================================================================

@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return ROOT


@pytest.fixture(scope="session")
def training_data(project_root):
    """加载训练数据"""
    path = os.path.join(project_root, "data", "training_data.json")
    if not os.path.exists(path):
        pytest.skip("training_data.json 不存在，先运行 scripts/build_mock_training_data.py")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def knowledge_graph(project_root):
    """加载知识点图谱"""
    path = os.path.join(project_root, "data", "knowledge_graph.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==========================================================================
# RAG / Searcher fixtures
# ==========================================================================

@pytest.fixture(scope="session")
def rag_ready():
    """确保 RAG 索引已就绪"""
    from rag_searcher import init_searcher
    try:
        init_searcher()
        return True
    except Exception as e:
        pytest.skip(f"RAG 初始化失败: {e}")


@pytest.fixture(scope="session")
def chroma_available():
    """检测 ChromaDB 是否可用"""
    try:
        import chromadb
        return True
    except ImportError:
        return False


# ==========================================================================
# Sample data fixtures
# ==========================================================================

@pytest.fixture
def sample_questions():
    """标准样本题目列表"""
    return [
        {
            "type": "choice",
            "text": "方程 2x + 3 = 7 的解是（　）",
            "options": ["A. x = 1", "B. x = 2", "C. x = 3", "D. x = 4"],
            "answer": "B",
            "analysis": "2x = 4, x = 2"
        },
        {
            "type": "fill_blank",
            "text": "三角形的内角和为 ______ 度。",
            "answer": "180",
            "analysis": "三角形内角和定理"
        },
        {
            "type": "short_answer",
            "text": "解方程 3x - 5 = 10，并写出步骤。",
            "answer": "x = 5",
            "analysis": "移项得 3x = 15, x = 5"
        },
        {
            "type": "choice",
            "text": "下列哪个是质数（　）",
            "options": ["A. 1", "B. 2", "C. 4", "D. 6"],
            "answer": "B",
            "analysis": "2是最小的质数"
        },
        {
            "type": "fill_blank",
            "text": "若 a + b = 10, a - b = 2, 则 a = ______。",
            "answer": "6",
            "analysis": "两式相加得 2a = 12, a = 6"
        },
    ]


@pytest.fixture
def sample_metadata():
    """标准样本元数据"""
    return {
        "title": "北京市2026年中考数学模拟卷（一）",
        "subject": "数学",
        "grade": "八年级",
        "region": "北京",
        "total_score": 100,
        "duration_minutes": 90,
        "difficulty": 3,
        "knowledge_points": ["方程", "几何"],
    }


# ==========================================================================
# 三地域测试场景
# ==========================================================================

@pytest.fixture(params=[
    {"subject": "math", "grade": "grade_2", "region": "beijing",
     "knowledge_points": ["整数四则运算"], "difficulty": 1, "question_count": 3,
     "label": "小学二年级数学（北京卷）"},
])
def beijing_primary_scenario(request):
    """北京小学数学场景"""
    return request.param


@pytest.fixture(params=[
    {"subject": "chinese", "grade": "grade_8", "region": "shanghai",
     "knowledge_points": ["记叙文阅读与分析"], "difficulty": 3, "question_count": 5,
     "label": "初中八年级语文（上海卷）"},
])
def shanghai_junior_scenario(request):
    """上海初中语文场景"""
    return request.param


@pytest.fixture(params=[
    {"subject": "english", "grade": "grade_12", "region": "guangdong",
     "knowledge_points": ["议论文写作（120词以上）"], "difficulty": 4, "question_count": 5,
     "label": "高中三年级英语（广东卷）"},
])
def guangdong_senior_scenario(request):
    """广东高中英语场景"""
    return request.param


@pytest.fixture
def all_three_scenarios():
    """三地域完整测试场景列表"""
    return [
        {"subject": "math", "grade": "grade_2", "region": "beijing",
         "knowledge_points": ["整数四则运算"], "difficulty": 1, "question_count": 3,
         "label": "小学二年级数学（北京卷）"},
        {"subject": "chinese", "grade": "grade_8", "region": "shanghai",
         "knowledge_points": ["记叙文阅读与分析"], "difficulty": 3, "question_count": 5,
         "label": "初中八年级语文（上海卷）"},
        {"subject": "english", "grade": "grade_12", "region": "guangdong",
         "knowledge_points": ["议论文写作（120词以上）"], "difficulty": 4, "question_count": 5,
         "label": "高中三年级英语（广东卷）"},
    ]


# ==========================================================================
# Mock LLM fixtures
# ==========================================================================

class MockLLMClient:
    """模拟 LLM 客户端，返回预设响应"""

    def __init__(self, responses: list[dict] = None):
        self.responses = responses or []
        self.call_count = 0
        self.call_history = []

    def set_response(self, response: dict):
        self.responses = [response]

    def add_response(self, response: dict):
        self.responses.append(response)

    class messages:
        @staticmethod
        def create(model=None, max_tokens=None, system=None, messages=None, **kwargs):
            # 通过实例方法调用
            pass


class MockLLMResponse:
    """模拟 LLM 响应对象"""

    def __init__(self, text: str):
        self.content = [MockContent(text)]


class MockContent:
    def __init__(self, text: str):
        self.text = text


@pytest.fixture
def mock_llm_client():
    """返回模拟 LLM 客户端"""
    return MockLLMClient()


@pytest.fixture
def mock_llm_response_math():
    """模拟数学题 LLM 响应"""
    return json.dumps({
        "questions": [
            {
                "type": "choice",
                "text": "计算：25 + 37 = ?",
                "options": ["A. 52", "B. 62", "C. 72", "D. 82"],
                "answer": "B",
                "analysis": "25 + 37 = 62"
            },
            {
                "type": "fill_blank",
                "text": "100 - 48 = ______",
                "answer": "52",
                "analysis": "100 - 48 = 52"
            },
            {
                "type": "short_answer",
                "text": "小明有36颗糖，分给4个小朋友，每人分到几颗？",
                "answer": "9颗",
                "analysis": "36 ÷ 4 = 9"
            },
        ]
    })


@pytest.fixture
def mock_llm_response_chinese():
    """模拟语文题 LLM 响应"""
    return json.dumps({
        "questions": [
            {
                "type": "short_answer",
                "text": "阅读下文，回答问题。\n春天来了，万物复苏。\n问题：这段文字描写了什么季节？",
                "answer": "春天",
                "analysis": "文中明确提到'春天来了'"
            },
        ]
    })


@pytest.fixture
def mock_llm_response_english():
    """模拟英语题 LLM 响应"""
    return json.dumps({
        "questions": [
            {
                "type": "choice",
                "text": "She ____ to school every day.",
                "options": ["A. go", "B. goes", "C. going", "D. gone"],
                "answer": "B",
                "analysis": "主语She是第三人称单数，动词用goes"
            },
        ]
    })


# ==========================================================================
# 临时向量数据库 fixture
# ==========================================================================

@pytest.fixture
def temp_vector_store():
    """创建临时向量存储（内存模式）"""
    from rag_indexer import InMemoryVectorStore
    store = InMemoryVectorStore()
    return store


@pytest.fixture
def populated_temp_store(temp_vector_store, sample_questions):
    """填充了测试数据的临时向量存储"""
    ids = [f"test_q_{i}" for i in range(len(sample_questions))]
    embeddings = None  # ChromaDB 自动生成
    metadatas = []
    documents = []
    for q in sample_questions:
        meta = {
            "question_text": q.get("text", ""),
            "answer": q.get("answer", ""),
            "analysis": q.get("analysis", ""),
            "subject": "math",
            "grade": "grade_7",
            "region": "beijing",
            "difficulty": 2,
            "knowledge_tags": "方程, 基础",
        }
        metadatas.append(meta)
        documents.append(q.get("text", ""))
    temp_vector_store.add(ids=ids, metadatas=metadatas, documents=documents)
    return temp_vector_store


# ==========================================================================
# Flask 测试客户端
# ==========================================================================

@pytest.fixture(scope="session")
def flask_client():
    """Flask 测试客户端"""
    from server import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ==========================================================================
# 集成测试 fixtures
# ==========================================================================

@pytest.fixture
def paper_request_beijing_math():
    """北京数学试卷请求"""
    return {
        "subject": "math",
        "grade": "grade_2",
        "grade_level": "primary",
        "region": "beijing",
        "knowledge_points": ["整数四则运算"],
        "difficulty": 1,
        "question_count": 3,
        "title": "北京市二年级数学模拟试卷",
    }


@pytest.fixture
def paper_request_shanghai_chinese():
    """上海语文试卷请求"""
    return {
        "subject": "chinese",
        "grade": "grade_8",
        "grade_level": "junior",
        "region": "shanghai",
        "knowledge_points": ["记叙文阅读与分析"],
        "difficulty": 3,
        "question_count": 5,
        "title": "上海市八年级语文模拟试卷",
    }


@pytest.fixture
def paper_request_guangdong_english():
    """广东英语试卷请求"""
    return {
        "subject": "english",
        "grade": "grade_12",
        "grade_level": "senior",
        "region": "guangdong",
        "knowledge_points": ["议论文写作（120词以上）"],
        "difficulty": 4,
        "question_count": 5,
        "title": "广东省高三英语模拟试卷",
    }


@pytest.fixture
def assembled_paper_fixture():
    """标准组装完成的试卷结构"""
    return {
        "paper_meta": {
            "title": "北京市九年级数学模拟试卷",
            "total_score": 100,
            "question_count": 3,
        },
        "questions": [
            {
                "sequence_number": 1,
                "section_order": 1,
                "score": 3,
                "question": {
                    "id": "q1", "subject": "math", "grade": "grade_9",
                    "grade_level": "junior", "region": "beijing",
                    "knowledge_tags": ["一元二次方程"],
                    "difficulty": 2, "question_type": "choice",
                    "question_text": "方程x²-4=0的解是？",
                    "options": [{"label": "A", "text": "±2"}, {"label": "B", "text": "2"},
                                {"label": "C", "text": "-2"}, {"label": "D", "text": "4"}],
                    "answer": "A", "analysis": "x²=4, x=±2", "source": "test",
                },
            },
        ],
    }


@pytest.fixture
def golden_set_samples():
    """精简的 golden set 样本（用于集成测试）"""
    return [
        {
            "id": "gs_001",
            "query": "请解释一元二次方程的概念",
            "answer": "使用求根公式 x=(-b±√(b²-4ac))/2a",
            "retrieved_docs": ["一元二次方程ax²+bx+c=0的求根公式"],
            "ground_truth_docs": ["求根公式：x=(-b±√(b²-4ac))/(2a)"],
            "subject": "math",
            "grade": "grade_9",
            "knowledge_tags": ["一元二次方程"],
        },
    ]


# ==========================================================================
# 临时目录 fixtures
# ==========================================================================

@pytest.fixture
def temp_dir():
    """创建临时目录并在测试后清理"""
    d = tempfile.mkdtemp(prefix="jiaoshi_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def temp_paper_request(temp_dir):
    """在临时目录中创建 paper_request.json"""
    request = {
        "_meta": {"generated_at": "2026-01-01T00:00:00", "generated_by": "ParameterParserAgent"},
        "request": {
            "subject": "math", "grade": "grade_9", "grade_level": "junior",
            "region": "beijing", "knowledge_points": ["一元二次方程"],
            "difficulty": 3, "question_count": 3, "question_types": ["choice", "calculation"],
            "title": "测试试卷",
        },
    }
    path = temp_dir / "paper_request.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False)
    return path
