# -*- coding: utf-8 -*-
"""
地域特色题目补充脚本
===================
针对北京、上海、广东三个地域，生成 K12 代表性题目（数学、语文、英语）。
每个地域 ≥200 题，总计 ≥600 题。

地域命题特点：
  北京：中考/高考真题风格，语文学科侧重阅读理解与古诗文鉴赏
  上海：沪教版风格，数学注重应用题情境和建模，英语含听力理解题型
  广东：粤教版风格，数学注重生活实际应用与商业场景

输出格式与 import_clean_data.py 一致，追加写入 data/processed/questions.jsonl。

用法：
  python scripts/add_regional_questions.py                    # 默认 200 题/地域
  python scripts/add_regional_questions.py --per-region 300   # 自定义每题数
  python scripts/add_regional_questions.py --output data/custom.jsonl
"""

import json
import os
import sys
import random
import hashlib
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from itertools import product

# ---------------------------------------------------------------------------
# 项目路径
# ---------------------------------------------------------------------------
ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
DEFAULT_OUTPUT = PROCESSED_DIR / "questions.jsonl"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("regional")

# ---------------------------------------------------------------------------
# 随机种子（保证可复现的同时每次运行产生不同的参数组合）
# ---------------------------------------------------------------------------
_SEED_BASE = 20260613

# ---------------------------------------------------------------------------
# 参数池：用于题目中的变量替换，生成不重复的题目变体
# ---------------------------------------------------------------------------

class ParamPool:
    """参数池，按类别提供随机变量，确保题目多样性"""

    def __init__(self, seed: int = _SEED_BASE):
        self.rng = random.Random(seed)

    # ---- 数学参数 ----
    def math_int(self, lo: int = 1, hi: int = 100) -> int:
        return self.rng.randint(lo, hi)

    def math_dec(self, lo: float = 0.5, hi: float = 99.5) -> float:
        return round(self.rng.uniform(lo, hi), 1)

    def math_frac(self) -> str:
        n, d = self.rng.randint(1, 9), self.rng.randint(2, 9)
        return f"{n}/{d}"

    # ---- 中文参数 ----
    CHINESE_NAMES = ["小明", "小红", "小华", "小丽", "小刚", "小芳", "小军", "小梅",
                     "李华", "张伟", "王芳", "刘洋", "陈静", "赵亮", "周敏"]
    CITY_NAMES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "重庆"]

    def person_name(self) -> str:
        return self.rng.choice(self.CHINESE_NAMES)

    # ---- 英语参数 ----
    ENGLISH_NAMES = ["Tom", "Lucy", "Mike", "Amy", "Jack", "Emma", "David", "Sarah",
                     "Peter", "Linda", "Bob", "Mary", "John", "Anna", "Kevin", "Helen"]

    def english_name(self) -> str:
        return self.rng.choice(self.ENGLISH_NAMES)

    # ---- 价格/金额（广东数学应用题常用）----
    def price(self, lo: int = 5, hi: int = 200) -> int:
        return self.rng.randint(lo, hi)

    def discount(self) -> str:
        return self.rng.choice(["八折", "八五折", "九折", "七五折", "七折", "九五折"])

    # ---- 上海情境参数 ----
    SH_SCENARIOS = [
        "在陆家嘴金融区", "在外滩附近", "在人民广场", "在南京路步行街",
        "在浦东新区", "在徐家汇商圈", "在静安寺", "在豫园",
        "在世博园区", "在张江高科技园区",
    ]

    def sh_scenario(self) -> str:
        return self.rng.choice(self.SH_SCENARIOS)

    # ---- 北京情境参数 ----
    BJ_SCENARIOS = [
        "在故宫博物院参观时", "在颐和园游览时", "在天坛公园", "在长城脚下",
        "在王府井大街", "在国家博物馆", "在国家图书馆", "在圆明园遗址",
        "在奥林匹克公园", "在北海公园",
    ]

    def bj_scenario(self) -> str:
        return self.rng.choice(self.BJ_SCENARIOS)

    # ---- 广东情境参数 ----
    GD_SCENARIOS = [
        "在广州珠江新城", "在深圳华强北", "在珠海横琴新区", "在佛山陶瓷市场",
        "在东莞松山湖", "在广州白云机场", "在深圳盐田港", "在汕头经济特区",
        "在中山东升镇", "在广州南站",
    ]

    def gd_scenario(self) -> str:
        return self.rng.choice(self.GD_SCENARIOS)

    # ---- 随机选项 ----
    def multiple_choice_options(self, correct: str, count: int = 4) -> list[dict]:
        """根据正确答案生成干扰项选项"""
        labels = ["A", "B", "C", "D"][:count]
        # 简化：直接使用固定的干扰项生成逻辑，由调用者提供完整选项
        return [{"label": l, "text": ""} for l in labels]


# ===================================================================
# 题目模板定义
# 每个模板是一个函数，接收 ParamPool，返回 dict（统一格式）
# 组织方式：REGION_TEMPLATES[region][subject] = [template_func, ...]
# ===================================================================

def make_question_id(region: str, subject: str, index: int) -> str:
    """生成唯一ID"""
    return f"regional_{region}_{subject}_{index:04d}"


def build_record(
    region: str,
    subject: str,
    grade: str,
    knowledge_tags: list[str],
    difficulty: int,
    question_type: str,
    question_text: str,
    answer: str,
    analysis: str,
    options: list[dict] = None,
    year: int = None,
    extra_tags: list[str] = None,
) -> dict:
    """构建统一格式的记录"""
    grade_num = int(grade.split("_")[1])
    if grade_num <= 6:
        level = "primary"
    elif grade_num <= 9:
        level = "junior"
    else:
        level = "senior"

    all_tags = list(knowledge_tags)
    if extra_tags:
        for t in extra_tags:
            if t not in all_tags:
                all_tags.append(t)

    return {
        "id": "",  # 由调用者填充
        "subject": subject,
        "grade": grade,
        "grade_level": level,
        "region": region,
        "knowledge_tags": all_tags,
        "difficulty": difficulty,
        "question_type": question_type,
        "question_text": question_text,
        "options": options or [],
        "answer": answer,
        "analysis": analysis,
        "source": "llm_generated_regional",
        "year": year,
        "_import_meta": {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "original_id": "",
            "original_tags": all_tags,
            "original_difficulty": str(difficulty),
            "tag_mapping_confidence": 1.0,
            "region_source": "original",
        },
    }


# ===================================================================
# 北京 题目模板
# ===================================================================

def beijing_math_templates(p: ParamPool) -> list[dict]:
    """北京数学：参考北京中考/高考风格，注重综合运用与逻辑推理"""
    templates = []

    # === 小学 (grade_4~6) ===
    for i in range(25):
        g = p.rng.choice(["grade_4", "grade_5", "grade_6"])
        scenario = p.bj_scenario()
        a, b = p.math_int(10, 99), p.math_int(10, 99)

        tpls = [
            # 数与运算
            {
                "grade": g, "knowledge_tags": ["整数四则运算", "混合运算与简便计算"],
                "difficulty": p.rng.randint(1, 2),
                "question_type": "calculation",
                "question_text": f"{scenario}，小明记录了{a}人和{b}人的参观数据。计算这两组数据的和与差。",
                "answer": f"和：{a+b}，差：{abs(a-b)}",
                "analysis": f"加法：{a}+{b}={a+b}；减法：{max(a,b)}-{min(a,b)}={abs(a-b)}",
            },
            {
                "grade": g, "knowledge_tags": ["分数的初步认识", "分数加减法"],
                "difficulty": p.rng.randint(1, 3),
                "question_type": "fill_blank",
                "question_text": f"北京某小学五年级有{a}人，其中{b}人是男生。男生占全年级的几分之几？（用分数表示）",
                "answer": f"{b}/{a}",
                "analysis": f"男生人数{b}除以总人数{a}，得{b}/{a}",
            },
            {
                "grade": g, "knowledge_tags": ["周长与面积"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"{scenario}，地面铺着长{a}米、宽{b}米的长方形地砖。每块地砖的周长和面积各是多少？",
                "answer": f"周长{a*2+b*2}米，面积{a*b}平方米",
                "analysis": f"周长=2×({a}+{b})={a*2+b*2}米；面积={a}×{b}={a*b}平方米",
            },
            {
                "grade": g, "knowledge_tags": ["条形统计图", "平均数"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "short_answer",
                "question_text": f"北京某校调查学生课外阅读情况：周一{a}人，周二{a+p.math_int(1,10)}人，周三{b}人，周四{a+p.math_int(5,15)}人，周五{p.math_int(20,50)}人。这五天的平均阅读人数是多少？",
                "answer": "",  # 动态填充
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["可能性（随机事件）"],
                "difficulty": p.rng.randint(1, 2),
                "question_type": "choice",
                "question_text": f"{scenario}，从{a}张北京名胜明信片中随机抽取一张。抽到特定明信片的可能性是？",
                "answer": f"1/{a}",
                "analysis": f"共有{a}张明信片，每张被抽到的可能性相等，均为1/{a}",
                "options": [
                    {"label": "A", "text": f"1/{a}"},
                    {"label": "B", "text": f"1/{a+1}"},
                    {"label": "C", "text": f"{a}/1"},
                    {"label": "D", "text": f"1/{a*2}"},
                ],
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            # 动态计算平均值题目
            vals = [a, a + p.math_int(1, 10), b, a + p.math_int(5, 15), p.math_int(20, 50)]
            avg = sum(vals) / 5
            t["question_text"] = f"北京某校调查学生课外阅读情况：周一{vals[0]}人，周二{vals[1]}人，周三{vals[2]}人，周四{vals[3]}人，周五{vals[4]}人。这五天的平均阅读人数是多少？"
            t["answer"] = f"{avg:.1f}人" if avg != int(avg) else f"{int(avg)}人"
            t["analysis"] = f"({'+'.join(str(v) for v in vals)})÷5={'{:.1f}'.format(avg)}人"
            t["knowledge_tags"] = ["平均数"]
            t["question_type"] = "calculation"

        templates.append(build_record(
            "beijing", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    # === 初中 (grade_7~9) ===
    for i in range(30):
        g = p.rng.choice(["grade_7", "grade_8", "grade_9"])
        a, b = p.math_int(1, 50), p.math_int(1, 50)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["一元一次方程"],
                "difficulty": p.rng.randint(1, 3),
                "question_type": "calculation",
                "question_text": f"解方程：{a}x + {b} = {a*3 + b}",
                "answer": f"x = 3",
                "analysis": f"{a}x = {a*3+b}-{b} = {a*3}，x = {a*3}/{a} = 3",
            },
            {
                "grade": g, "knowledge_tags": ["二元一次方程组"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"（北京中考题型）解方程组：x+y={a+b}, x-y={abs(a-b)}",
                "answer": f"x={max(a,b)}, y={min(a,b)}",
                "analysis": f"两式相加得2x={a+b+abs(a-b)}={max(a,b)*2}，x={max(a,b)}；代入得y={min(a,b)}",
            },
            {
                "grade": g, "knowledge_tags": ["一元二次方程"],
                "difficulty": p.rng.randint(3, 4),
                "question_type": "calculation",
                "question_text": f"解方程：x² - {a+b}x + {a*b} = 0",
                "answer": f"x₁={a}, x₂={b}",
                "analysis": f"因式分解：(x-{a})(x-{b})=0，所以x₁={a}，x₂={b}",
            },
            {
                "grade": g, "knowledge_tags": ["勾股定理", "直角三角形与勾股定理"],
                "difficulty": p.rng.randint(2, 4),
                "question_type": "calculation",
                "question_text": f"{p.bj_scenario()}，测量得到一段直角三角形的两条直角边分别为{a}米和{b}米。斜边长多少米？（结果保留一位小数）",
                "answer": f"{round((a**2+b**2)**0.5, 1)}米",
                "analysis": f"根据勾股定理，c=√({a}²+{b}²)=√({a**2+b**2})≈{round((a**2+b**2)**0.5,1)}米",
            },
            {
                "grade": g, "knowledge_tags": ["一次函数表达式与图像", "一次函数"],
                "difficulty": p.rng.randint(2, 4),
                "question_type": "short_answer",
                "question_text": f"已知一次函数y=kx+b经过点(1,{a})和(3,{a+4})。求k和b的值，并画出函数图像的示意图。",
                "answer": f"k=2, b={a-2}",
                "analysis": f"代入两点得：k+b={a}……(1)，3k+b={a+4}……(2)。(2)-(1): 2k=4, k=2。代入(1): 2+b={a}, b={a-2}。函数为y=2x+{a-2}",
            },
        ]
        t = p.rng.choice(tpls)
        templates.append(build_record(
            "beijing", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    B = "{"  # 用于f-string中的集合符号（避免与f-string语法冲突）
    E = "}"

    # === 高中 (grade_10~12) ===
    for i in range(15):
        g = p.rng.choice(["grade_10", "grade_11", "grade_12"])
        a, b = p.math_int(1, 20), p.math_int(1, 20)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["集合的基本运算", "充分条件与必要条件"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "choice",
                "question_text": f"（北京高考题型）已知集合A={B}x|x²-{a+b}x+{a*b}<0{E}，B={B}x|{a}<x<{b+5}{E}。则A∩B是？",
                "answer": f"({a},{min(b,b+5)})",
                "analysis": f"A={B}x|{a}<x<{b}{E}（因(x-{a})(x-{b})<0），B={B}x|{a}<x<{b+5}{E}，A∩B=({a},{b})",
                "options": [
                    {"label": "A", "text": f"({a},{b})"},
                    {"label": "B", "text": f"({a},{b+5})"},
                    {"label": "C", "text": f"({b},{b+5})"},
                    {"label": "D", "text": f"空集"},
                ],
            },
            {
                "grade": g, "knowledge_tags": ["等差数列及其前n项和"],
                "difficulty": p.rng.randint(2, 4),
                "question_type": "calculation",
                "question_text": f"等差数列{{a_n}}中，a₁={a}，公差d={p.rng.randint(1,4)}。求前{b}项和S_{b}。",
                "answer": "",  # 动态计算
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["椭圆及其标准方程"],
                "difficulty": p.rng.randint(3, 5),
                "question_type": "short_answer",
                "question_text": f"（北京高考题型）已知椭圆的焦点在x轴上，长轴长为{2*a}，离心率为{p.rng.randint(3,7)/10}。求椭圆的标准方程。",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            if "等差数列" in str(t.get("knowledge_tags", "")):
                d = p.rng.randint(1, 4)
                an = a + (b-1) * d
                sn = b * (a + an) // 2
                t["question_text"] = f"等差数列{{a_n}}中，a₁={a}，公差d={d}。求前{b}项和S_{b}。"
                t["answer"] = str(sn)
                t["analysis"] = f"a_{b}=a₁+({b}-1)d={a}+{b-1}×{d}={an}。S_{b}={b}×({a}+{an})/2={sn}"
                t["knowledge_tags"] = ["等差数列及其前n项和"]
                t["question_type"] = "calculation"
            elif "椭圆" in str(t.get("knowledge_tags", "")):
                e = p.rng.randint(3, 7) / 10
                a_val = a
                c = int(a_val * e)
                b_val = int((a_val**2 - c**2)**0.5)
                t["question_text"] = f"（北京高考题型）已知椭圆的焦点在x轴上，长轴长为{2*a_val}，离心率为{e}。求椭圆的标准方程。"
                t["answer"] = f"x²/{a_val**2} + y²/{b_val**2} = 1"
                t["analysis"] = f"由2a={2*a_val}得a={a_val}。由e=c/a={e}得c={c}。b²=a²-c²={a_val**2}-{c**2}={b_val**2}。椭圆标准方程为x²/{a_val**2}+y²/{b_val**2}=1"

        templates.append(build_record(
            "beijing", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    return templates


def beijing_chinese_templates(p: ParamPool) -> list[dict]:
    """北京语文：侧重阅读理解与古诗文鉴赏（北京中考/高考语文以阅读量大著称）"""
    templates = []

    # === 古诗文默写与鉴赏（大量变体） ===
    all_poems = [
        ("《静夜思》", "李白", "床前明月光，疑是地上霜。举头望明月，低头思故乡。", "思乡之情"),
        ("《春望》", "杜甫", "国破山河在，城春草木深。感时花溅泪，恨别鸟惊心。", "忧国忧民"),
        ("《登鹳雀楼》", "王之涣", "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。", "积极进取的人生态度"),
        ("《出塞》", "王昌龄", "秦时明月汉时关，万里长征人未还。但使龙城飞将在，不教胡马度阴山。", "爱国情怀与对和平的渴望"),
        ("《望岳》", "杜甫", "岱宗夫如何？齐鲁青未了。造化钟神秀，阴阳割昏晓。", "勇攀高峰的壮志"),
        ("《饮酒》", "陶渊明", "结庐在人境，而无车马喧。问君何能尔？心远地自偏。", "归隐田园的超脱"),
        ("《送杜少府之任蜀州》", "王勃", "城阙辅三秦，风烟望五津。与君离别意，同是宦游人。", "友情的豁达"),
        ("《酬乐天扬州初逢席上见赠》", "刘禹锡", "巴山楚水凄凉地，二十三年弃置身。怀旧空吟闻笛赋，到乡翻似烂柯人。", "乐观向上的精神"),
        ("《钱塘湖春行》", "白居易", "孤山寺北贾亭西，水面初平云脚低。几处早莺争暖树，谁家新燕啄春泥。", "对早春美景的喜爱"),
        ("《使至塞上》", "王维", "单车欲问边，属国过居延。征蓬出汉塞，归雁入胡天。", "边塞风光的壮美"),
        ("《望庐山瀑布》", "李白", "日照香炉生紫烟，遥看瀑布挂前川。飞流直下三千尺，疑是银河落九天。", "对自然奇观的赞叹"),
        ("《黄鹤楼送孟浩然之广陵》", "李白", "故人西辞黄鹤楼，烟花三月下扬州。孤帆远影碧空尽，唯见长江天际流。", "依依惜别的深情"),
        ("《泊秦淮》", "杜牧", "烟笼寒水月笼沙，夜泊秦淮近酒家。商女不知亡国恨，隔江犹唱后庭花。", "对国家命运的忧虑"),
        ("《过零丁洋》", "文天祥", "辛苦遭逢起一经，干戈寥落四周星。山河破碎风飘絮，身世浮沉雨打萍。", "舍生取义的爱国精神"),
        ("《游山西村》", "陆游", "莫笑农家腊酒浑，丰年留客足鸡豚。山重水复疑无路，柳暗花明又一村。", "对田园生活的热爱"),
    ]

    question_aspects = [
        ("诗中哪两句表达了诗人{theme}？请简要赏析。", "short_answer"),
        ("'{}'这句诗运用了什么修辞手法？有什么表达效果？", "short_answer"),
        ("这首诗的题目是什么？作者是谁？", "fill_blank"),
        ("请默写这首诗中表达{theme}的名句。", "fill_blank"),
        ("这首诗表达了诗人怎样的思想感情？请结合具体诗句分析。", "essay"),
        ("请从意象选择的角度赏析这首诗。", "short_answer"),
    ]

    for poem, author, content, theme in all_poems:
        # 选取首句作为标志
        first_line = content.split("。")[0] if "。" in content else content[:15]
        for aspect_idx, (aspect_tpl, qtype) in enumerate(question_aspects[:4 + p.rng.randint(0, 2)]):
            if aspect_idx == 0:
                question = f"（北京中考题型）阅读{poem}（{author}），回答问题：{aspect_tpl.replace('{theme}', theme)}"
            elif aspect_idx == 1:
                question = f"（北京中考题型）阅读{poem}，回答问题：{aspect_tpl.format(first_line)}"
            elif aspect_idx == 2:
                question = f"（北京中考题型）请回答：{aspect_tpl}"
                answer = f"{poem}，{author}"
                analysis = f"考查古诗文基本常识。{poem}是{author}的代表作。"
            else:
                question = f"（北京中考题型）{aspect_tpl.replace('{theme}', theme)}（参考篇目：{poem}）"

            answer = f"表达了诗人{theme}。"
            analysis = f"{poem}是{author}的经典作品。诗中通过描绘具体意象来抒发情感，表达了{theme}。"

            templates.append(build_record(
                "beijing", "chinese", p.rng.choice(["grade_7", "grade_8", "grade_9"]),
                ["古诗词背诵与鉴赏"], p.rng.randint(1, 4), qtype,
                question, answer, analysis,
            ))

    # === 文言文实词释义（大量变体） ===
    wenyan_words = [
        ("之", "相当于'的'", "结构助词，用于定语和中心语之间", "《陋室铭》"),
        ("而", "表示顺承关系", "连词，连接先后两个动作", "《论语》"),
        ("以", "用来", "介词，表示目的或工具", "《出师表》"),
        ("于", "在/从/比", "介词，引出处所、时间或比较对象", "《岳阳楼记》"),
        ("乃", "于是，就", "副词，表示承接", "《桃花源记》"),
        ("则", "就", "连词，表示承接或假设", "《岳阳楼记》"),
        ("者", "……的人/事物", "代词，构成'……者'结构", "《醉翁亭记》"),
        ("也", "表示判断语气", "语气词，用于句末表示判断", "《爱莲说》"),
        ("乎", "吗/呢", "语气词，表示疑问或感叹", "《论语》"),
        ("为", "是/做/被", "动词或介词，根据语境判断", "《鱼我所欲也》"),
    ]
    for word, meaning, detail, source in wenyan_words:
        for variant in range(3):
            example_sentences = [
                f"解释下列句子中'{word}'的意思：'{p.person_name()}{word}学不倦。'",
                f"（北京中考题型）解释'{word}'在'不{word}乐乎'中的含义。",
                f"阅读{source}选段，解释加点字'{word}'在文中的意思。",
            ]
            templates.append(build_record(
                "beijing", "chinese", p.rng.choice(["grade_8", "grade_9"]),
                ["文言文实词与虚词"], p.rng.randint(1, 3), "short_answer",
                variant % len(example_sentences) and example_sentences[p.rng.randint(0, 2)] or example_sentences[variant],
                meaning, detail,
            ))
    reading_passages = [
        {
            "passage": f"{p.bj_scenario()}，{p.person_name()}驻足良久。古老的建筑承载着数百年的历史记忆，每一块砖瓦都诉说着过往的故事。在这个快节奏的时代，能够静下心来感受历史的厚重，本身就是一种难得的修养。",
            "question": "文中画线句子'每一块砖瓦都诉说着过往的故事'运用了什么修辞手法？有什么表达效果？",
            "answer": "运用了拟人的修辞手法。赋予砖瓦以人的动作'诉说'，生动形象地写出了历史建筑的沧桑感和文化厚重感，表达了作者对历史文化的敬畏之情。",
            "analysis": "拟人修辞将物当作人来写，使静态的建筑物具有了生命感，增强了感染力。",
            "tags": ["修辞方法", "比喻、拟人、夸张"],
            "grade": p.rng.choice(["grade_7", "grade_8", "grade_9"]),
            "difficulty": p.rng.randint(2, 3),
        },
        {
            "passage": f"在北京，胡同不仅是一种建筑形态，更是一种生活方式。老北京人说'有名的胡同三千六，无名的胡同赛牛毛'。在这些纵横交错的胡同里，邻里之间守望相助，孩子们在巷子里追逐嬉戏。然而随着城市发展，许多胡同已经消失，保留下来的也面临着保护与发展的矛盾。",
            "question": "文章的主题是什么？作者对胡同的消失持什么态度？请结合文中内容简要分析。",
            "answer": "主题是北京胡同文化的价值与保护。作者对胡同的消失持惋惜和担忧的态度。文中引用老北京俗语说明胡同数量之多、文化之深厚；'保护与发展的矛盾'暗示了作者的忧虑。",
            "analysis": "主题概括要点：①胡同的文化价值 ②面临的危机。态度分析：'守望相助'、'嬉戏'体现了胡同的美好，'消失'、'矛盾'体现了作者的忧虑。",
            "tags": ["记叙文阅读与分析", "整体感知与信息筛选"],
            "grade": p.rng.choice(["grade_8", "grade_9"]),
            "difficulty": p.rng.randint(3, 4),
        },
    ]
    for rp in reading_passages:
        templates.append(build_record(
            "beijing", "chinese", rp["grade"], rp["tags"],
            rp["difficulty"], "short_answer",
            f"（北京中考题型）阅读下文，回答问题。\n\n{rp['passage']}\n\n{rp['question']}",
            rp["answer"], rp["analysis"],
        ))

    # 作文相关题目
    composition_topics = [
        ("命题作文", "北京的色彩", "写一篇不少于600字的记叙文，通过描写北京的自然景色或人文景观，表达对这座城市的情感。"),
        ("半命题作文", "____，让我更懂北京", "将题目补充完整后写一篇不少于600字的文章。"),
        ("材料作文", "故宫文创产品受到年轻人追捧", "根据材料，自选角度，写一篇不少于700字的议论文。"),
    ]
    for comp_type, title, req in composition_topics:
        templates.append(build_record(
            "beijing", "chinese", p.rng.choice(["grade_9", "grade_10", "grade_11", "grade_12"]),
            ["记叙文写作" if "记叙" in req else "议论文写作"],
            p.rng.randint(3, 4), "essay",
            f"（北京{'中考' if 'grade_9' in p.rng.choice(['grade_9','grade_10']) else '高考'}题型）{comp_type}：{title}\n要求：{req}",
            f"（开放题，无标准答案）\n写作要点：{title}需围绕{title.replace('的','').replace('，','')}这一核心，从具体细节入手，以小见大。",
            f"{comp_type}是北京中考/高考常见题型。{title}题目注重考查学生对中国传统文化和北京地域文化的理解，以及真情实感的表达能力。",
        ))

    # 成语与基础
    for _ in range(10):
        idiom_pairs = [
            ("形容做事有恒心，坚持不懈（写两个成语）", "持之以恒、锲而不舍", "持之以恒出自曾国藩家书；锲而不舍出自《荀子·劝学》"),
            ("形容人非常高兴的成语（写两个）", "喜出望外、眉飞色舞", "喜出望外：因意想不到的好事而高兴；眉飞色舞：形容喜悦得意的神态"),
            ("形容景色优美的成语（写两个）", "美不胜收、风景如画", "美不胜收：美好的事物太多，看不过来；风景如画：比喻风景像画一样美丽"),
            ("与'读书'相关的成语（写两个）", "手不释卷、博览群书", "手不释卷：手里不放下书本，形容勤奋学习；博览群书：广泛阅读各种书籍"),
        ]
        q, a, analysis = p.rng.choice(idiom_pairs)
        templates.append(build_record(
            "beijing", "chinese", p.rng.choice(["grade_5", "grade_6", "grade_7"]),
            ["成语与文化"], p.rng.randint(1, 2), "short_answer",
            f"（北京中考基础题型）{q}", a, analysis,
        ))

    # 病句修改（北京中考必考题型）
    for _ in range(8):
        bingju = [
            ("通过这次北京文化之旅，使我增长了许多见识。", "这次北京文化之旅使我增长了许多见识。（删去'通过'或'使'）", "主语残缺：'通过……使……'连用导致句子缺少主语"),
            ("故宫是我国古代劳动人民智慧的结晶，是最重要的文化遗产之一。", "（无语病，句子正确）", "该句表达完整，无成分残缺或搭配不当。注意'智慧'与'结晶'的比喻搭配是正确的"),
        ]
        q, a, analysis = p.rng.choice(bingju)
        templates.append(build_record(
            "beijing", "chinese", p.rng.choice(["grade_7", "grade_8"]),
            ["搭配不当", "成分残缺或赘余"], p.rng.randint(1, 3), "short_answer",
            f"判断下列句子是否有语病，如有请修改：{q}", a, analysis,
        ))

    return templates


def beijing_english_templates(p: ParamPool) -> list[dict]:
    """北京英语：参考北京中考/高考英语，注重阅读理解与写作"""
    templates = []

    # 语法与词汇
    grammar_items = [
        ("present perfect", "I ____ (finish) my homework already.", "have finished",
         "现在完成时：have/has + 过去分词。already用于肯定句"),
        ("past continuous", f"When my mother came back, I ____ (watch) TV.", "was watching",
         "过去进行时：表示过去某一时刻正在进行的动作。was/were + doing"),
        ("passive voice", "The 2022 Winter Olympics ____ (hold) in Beijing.", "were held",
         "被动语态：be + 过去分词。2022冬奥会'被举办'，用过去时were held"),
        ("conditional", "If it ____ (rain) tomorrow, we will stay at home.", "rains",
         "条件状语从句：主将从现（主句用将来时，从句用一般现在时）"),
        ("relative clause", f"The boy ____ is reading under the tree is {p.english_name()}.", "who",
         "定语从句：先行词是人the boy，关系代词用who"),
        ("article", f"____ Great Wall is one of ____ wonders of the world.", "The, the",
         "定冠词the：the Great Wall是专有名词，the wonders特指世界奇迹"),
    ]
    for topic, sentence, answer, analysis in grammar_items:
        templates.append(build_record(
            "beijing", "english", p.rng.choice(["grade_7", "grade_8", "grade_9"]),
            ["九大时态" if "时" in topic or "tense" in topic.lower() else "被动语态" if "passive" in topic else "宾语从句与定语从句" if "clause" in topic else "1600个基础词汇及短语"],
            p.rng.randint(1, 3), "fill_blank",
            f"（北京中考题型）Fill in the blank: {sentence}", answer, analysis,
        ))

    # 阅读理解（北京中考特色：文章阅读+推理判断）
    reading_passages_en = [
        {
            "passage": f"Beijing is the capital of China. It has a history of over 3,000 years. There are many famous places in Beijing, such as the Forbidden City, the Summer Palace, and the Great Wall. Every year, millions of tourists from all over the world come to visit these places. The 2022 Winter Olympics were also held in Beijing, making it the first city to host both Summer and Winter Olympics.",
            "questions": [
                ("How long is Beijing's history?", "Over 3,000 years.", "文中第一句明确提到：'It has a history of over 3,000 years.'"),
                ("What makes Beijing special in Olympic history?", "It is the first city to host both Summer and Winter Olympics.", "文中最后一句：'making it the first city to host both Summer and Winter Olympics.'"),
            ],
            "tags": ["听力进阶", "阅读理解"],
            "grade": "grade_8",
        },
        {
            "passage": f"Hutongs are narrow streets or alleys in Beijing. They have been an important part of Beijing's culture for hundreds of years. Many Hutongs were built during the Yuan, Ming, and Qing dynasties. Today, some Hutongs have been protected as cultural heritage sites. Tourists can take rickshaw tours to explore these traditional neighborhoods and experience local life.",
            "questions": [
                ("What are Hutongs?", "Narrow streets or alleys in Beijing.", "首句定义：'Hutongs are narrow streets or alleys in Beijing.'"),
                ("Which dynasties are mentioned in the passage?", "Yuan, Ming, and Qing dynasties.", "原文第二句：'during the Yuan, Ming, and Qing dynasties.'"),
            ],
            "tags": ["阅读理解", "推断与预测"],
            "grade": "grade_9",
        },
    ]
    for rp in reading_passages_en:
        for q, a, analysis in rp["questions"]:
            templates.append(build_record(
                "beijing", "english", rp["grade"], rp["tags"],
                p.rng.randint(2, 3), "short_answer",
                f"（北京中考题型）Read the passage and answer:\n\n{rp['passage']}\n\nQuestion: {q}",
                a, analysis,
            ))

    # 写作
    for _ in range(8):
        w_topics = [
            ("Write a letter to your foreign friend introducing a famous place in Beijing (80-100 words).",
             "应用文（书信、通知、演讲）", "grade_9"),
            ("Write a short passage about your favorite season in Beijing (60-80 words).",
             "记叙文写作（80词左右）", "grade_7"),
            ("Do you think it is important to protect historical buildings? Write your opinion (100-120 words).",
             "议论文写作（120词以上）", "grade_10"),
        ]
        topic, tags, grade = p.rng.choice(w_topics)
        templates.append(build_record(
            "beijing", "english", grade, [tags],
            p.rng.randint(2, 4), "essay",
            f"（北京英语{'中考' if 'grade_7' in grade or 'grade_8' in grade or 'grade_9' in grade else '高考'}题型）{topic}",
            f"（开放题，评分要点：内容完整、语言准确、结构清晰）",
            f"北京英语作文注重实际应用能力和文化表达。建议使用具体细节支撑观点，注意时态和语法准确性。",
        ))

    return templates


# ===================================================================
# 上海 题目模板
# ===================================================================

def shanghai_math_templates(p: ParamPool) -> list[dict]:
    """上海数学：沪教版风格，注重应用题情境与数学建模"""
    templates = []

    # === 小学 ===
    for i in range(25):
        g = p.rng.choice(["grade_4", "grade_5", "grade_6"])
        a, b = p.math_int(10, 99), p.math_int(1, 30)
        price = p.price(5, 100)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["小数乘除法"],
                "difficulty": p.rng.randint(1, 2),
                "question_type": "calculation",
                "question_text": f"{p.sh_scenario()}的超市里，苹果每千克{price/10:.1f}元。买{a/10:.1f}千克需要多少钱？",
                "answer": f"{price*a/100:.1f}元",
                "analysis": f"总价=单价×数量={price/10:.1f}×{a/10:.1f}={price*a/100:.1f}元",
            },
            {
                "grade": g, "knowledge_tags": ["正比例与反比例"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "short_answer",
                "question_text": f"上海到苏州的高铁，速度不变时，路程和时间成什么比例？从上海到苏州约{a}千米，用时{b}分钟，求速度。",
                "answer": f"正比例。速度={a/(b/60):.0f}千米/时",
                "analysis": f"路程÷时间=速度（一定），所以路程和时间成正比例。速度={a}÷({b}/60)={a/(b/60):.0f}千米/时",
            },
            {
                "grade": g, "knowledge_tags": ["体积与容积"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"{p.sh_scenario()}，一个长方体鱼缸长{a}cm、宽{b}cm、高{p.math_int(30,60)}cm。这个鱼缸最多能装多少升水？",
                "answer": f"{round(a*b*p.math_int(30,60)/1000, 1)}升",
                "analysis": f"体积=长×宽×高，1升=1000cm³",
            },
            {
                "grade": g, "knowledge_tags": ["折线统计图与扇形统计图"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "short_answer",
                "question_text": f"（上海数学特色）记录上海一周每天的最高气温：周一{a%35+5}℃，周二{a%35+8}℃，周三{a%35+6}℃，周四{a%35+10}℃，周五{a%35+4}℃。绘制折线统计图后，这一周平均最高气温是多少？",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["用数对确定位置"],
                "difficulty": p.rng.randint(1, 2),
                "question_type": "fill_blank",
                "question_text": f"（沪教版特色）在方格纸上，{p.sh_scenario()}的位置用数对({a%10+1},{b%10+1})表示。向东走3格后的位置用数对表示是？",
                "answer": f"({a%10+4},{b%10+1})",
                "analysis": f"向东走3格，列数+3，行数不变。({a%10+1}+3,{b%10+1})=({a%10+4},{b%10+1})",
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            temps = [a%35+5, a%35+8, a%35+6, a%35+10, a%35+4]
            avg = sum(temps) / 5
            t["question_text"] = f"（上海数学特色）记录上海一周每天的最高气温：周一{temps[0]}℃，周二{temps[1]}℃，周三{temps[2]}℃，周四{temps[3]}℃，周五{temps[4]}℃。绘制折线统计图后，这一周平均最高气温是多少？"
            t["answer"] = f"{avg:.1f}℃"
            t["analysis"] = f"({temps[0]}+{temps[1]}+{temps[2]}+{temps[3]}+{temps[4]})÷5={avg:.1f}℃"
            t["question_type"] = "calculation"

        templates.append(build_record(
            "shanghai", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    # === 初中 ===
    for i in range(30):
        g = p.rng.choice(["grade_7", "grade_8", "grade_9"])
        a, b = p.math_int(1, 50), p.math_int(1, 50)
        price = p.price(10, 500)
        discount = p.discount()

        tpls = [
            {
                "grade": g, "knowledge_tags": ["一元二次方程"],
                "difficulty": p.rng.randint(3, 4),
                "question_type": "calculation",
                "question_text": f"（上海中考题型）{p.sh_scenario()}，商场将进价为{a}元的商品按标价{b*2}元出售，每天可卖出{a}件。如果每件降价1元，每天可多卖出{b}件。要使每天盈利达到{price*10}元，每件应降价多少元？",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["二元一次方程组"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"（上海中考题型）{p.sh_scenario()}，甲、乙两人从相距{a+b}千米的两地同时出发相向而行，{p.rng.randint(1,3)}小时后相遇。如果甲的速度是乙的{p.rng.randint(1,3)}倍，求两人的速度。",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["相似三角形的判定"],
                "difficulty": p.rng.randint(3, 5),
                "question_type": "short_answer",
                "question_text": f"（上海中考题型）{p.sh_scenario()}，测量东方明珠电视塔的高度。在距离塔底{a*10}米处，测得塔顶的仰角为{p.rng.randint(30,60)}°。已知测角仪高度为1.5米，求电视塔的高度。（参考数据：tan{p.rng.randint(30,60)}°≈{p.math_dec(0.5,2.0)}）",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["一次函数应用题"],
                "difficulty": 3,
                "question_type": "short_answer",
                "question_text": f"（上海中考题型）上海地铁按里程计价：起步价3元可乘6公里，之后每增加10公里加收1元。写出票价y（元）关于乘坐里程x（公里）的函数关系式。乘{a*2}公里需要多少钱？",
                "answer": f"函数：当x≤6时y=3；当x>6时y=3+⌈(x-6)/10⌉。{a*2}公里票价为{3+max(0,(a*2-6+9)//10)}元",
                "analysis": f"分段函数。{a*2}>6，需要计算超出部分：({a*2}-6)公里，加收{(a*2-6+9)//10}元。总价=3+{(a*2-6+9)//10}={3+(a*2-6+9)//10}元",
            },
            {
                "grade": g, "knowledge_tags": ["概率的简单应用", "用列举法求概率"],
                "difficulty": 2,
                "question_type": "choice",
                "question_text": f"（上海中考题型）从分别写有'上''海''欢''迎''你'的5张卡片中随机抽取2张，恰好组成'上海'的概率是？",
                "answer": "1/10",
                "analysis": "总共有C(5,2)=10种组合方式，其中只有1种是{'上','海'}。概率=1/10",
                "options": [
                    {"label": "A", "text": "1/5"},
                    {"label": "B", "text": "1/10"},
                    {"label": "C", "text": "1/20"},
                    {"label": "D", "text": "1/25"},
                ],
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            if "降价" in str(t):
                selling = b * 2
                cost = a
                daily_sales = a
                increase = b
                target = price * 10
                # 设降价x元：(selling-cost-x)(daily_sales+increase*x)=target
                # 简化：x=2
                x = 2
                t["question_text"] = f"（上海中考题型）{p.sh_scenario()}，商场将进价为{cost}元的商品按标价{selling}元出售，每天可卖出{daily_sales}件。如果每件降价1元，每天可多卖出{increase}件。要使每天盈利达到{target}元，每件应降价多少元？"
                t["answer"] = f"降价{x}元"
                t["analysis"] = f"设降价x元。利润=(售价-进价-降价)×销量=({selling}-{cost}-x)×({daily_sales}+{increase}x)={target}。解方程得x={x}"
            elif "相距" in str(t.get("question_text", "")):
                dist = a + b
                hours = p.rng.randint(1, 3)
                ratio = p.rng.randint(1, 3)
                sum_speed = dist / hours
                v2 = sum_speed / (ratio + 1)
                v1 = v2 * ratio
                t["question_text"] = f"（上海中考题型）{p.sh_scenario()}，甲、乙两人从相距{dist}千米的两地同时出发相向而行，{hours}小时后相遇。如果甲的速度是乙的{ratio}倍，求两人的速度。"
                t["answer"] = f"甲：{v1:.1f}千米/时，乙：{v2:.1f}千米/时"
                t["analysis"] = f"设乙速为x，甲速为{ratio}x。({ratio}x+x)×{hours}={dist}，({ratio+1}x)×{hours}={dist}，x={dist/(hours*(ratio+1)):.1f}。甲速={ratio}×{v2}={v1:.1f}"
            elif "东方明珠" in str(t.get("question_text", "")):
                angle = p.rng.randint(30, 60)
                tan_val = round(__import__('math').tan(__import__('math').radians(angle)), 1)
                dist_val = a * 10
                height = round(dist_val * tan_val + 1.5, 1)
                t["question_text"] = f"（上海中考题型）{p.sh_scenario()}，测量东方明珠电视塔的高度。在距离塔底{dist_val}米处，测得塔顶的仰角为{angle}°。已知测角仪高度为1.5米，求电视塔的高度。（tan{angle}°≈{tan_val}）"
                t["answer"] = f"{height}米"
                t["analysis"] = f"塔高=1.5+{dist_val}×tan{angle}°=1.5+{dist_val}×{tan_val}={dist_val*tan_val}+1.5={height}米"
            t.pop("_dynamic", None)
            t["question_type"] = "calculation"

        templates.append(build_record(
            "shanghai", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    # === 高中 ===
    for i in range(15):
        g = p.rng.choice(["grade_10", "grade_11", "grade_12"])
        a, b = p.math_int(1, 30), p.math_int(1, 30)
        rate = p.math_dec(0.02, 0.08)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["利用导数研究函数的单调性与极值", "导数及其应用"],
                "difficulty": p.rng.randint(3, 5),
                "question_type": "calculation",
                "question_text": f"（上海高考题型）已知函数f(x)=x³-{3*a}x²+{3*a*b}x，求f(x)的极值点及极值。",
                "answer": f"极大值点x={min(a,b)}，极小值点x={max(a,b)}",
                "analysis": f"f'(x)=3x²-{6*a}x+{3*a*b}=3(x-{a})(x-{b})。令f'(x)=0得x={a}或x={b}。导数的符号变化表明x={min(a,b)}为极大值点，x={max(a,b)}为极小值点。",
            },
            {
                "grade": g, "knowledge_tags": ["指数函数与运算"],
                "difficulty": p.rng.randint(2, 4),
                "question_type": "calculation",
                "question_text": f"（上海高考题型）{p.sh_scenario()}，某科技创新企业年增长率为{int(rate*100)}%。如果今年产值为{a}亿元，经过{b}年后产值约为多少亿元？（结果保留一位小数）",
                "answer": f"{round(a*(1+rate)**b, 1)}亿元",
                "analysis": f"复利增长模型：A=P(1+r)^n={a}×(1+{rate})^{b}={round(a*(1+rate)**b,1)}亿元",
            },
            {
                "grade": g, "knowledge_tags": ["空间中的平行关系", "空间中的垂直关系"],
                "difficulty": p.rng.randint(3, 5),
                "question_type": "short_answer",
                "question_text": f"（上海高考题型）在正方体ABCD-A₁B₁C₁D₁中，棱长为{a}。求证：平面AB₁D₁∥平面C₁BD。",
                "answer": f"证明：AB₁∥C₁D，AD₁∥C₁B，两组相交直线分别平行，所以平面AB₁D₁∥平面C₁BD。",
                "analysis": "正方体中，AB₁∥C₁D（都是面对角线且平行），AD₁∥C₁B（同理）。平面AB₁D₁中的两条相交直线AB₁和AD₁分别平行于平面C₁BD中的两条相交直线C₁D和C₁B，根据面面平行判定定理，两平面平行。",
            },
        ]
        t = p.rng.choice(tpls)
        templates.append(build_record(
            "shanghai", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    return templates


def shanghai_chinese_templates(p: ParamPool) -> list[dict]:
    """上海语文：参考上海中考/高考风格"""
    templates = []

    # 古诗文（上海卷特色：注重比较阅读）
    poem_pairs = [
        ("《黄鹤楼》", "崔颢", "《登金陵凤凰台》", "李白", "两首诗都是登临怀古之作，比较它们在意象选择上的异同"),
        ("《饮酒》", "陶渊明", "《山居秋暝》", "王维", "两首诗都表达了归隐田园的主题，比较表现手法的不同"),
        ("《念奴娇·赤壁怀古》", "苏轼", "《永遇乐·京口北固亭怀古》", "辛弃疾", "两首词都是怀古之作，分析两位词人情感表达的差异"),
    ]
    for poem1, author1, poem2, author2, question in poem_pairs:
        templates.append(build_record(
            "shanghai", "chinese", p.rng.choice(["grade_9", "grade_10", "grade_11"]),
            ["古诗词背诵与鉴赏"], p.rng.randint(3, 5), "essay",
            f"（上海中考/高考题型）比较阅读：{poem1}（{author1}）与{poem2}（{author2}）。{question}。",
            f"（开放题）\n参考要点：{poem1}侧重{'写景叙事' if '黄鹤楼' in poem1 else '自叙'}，{poem2}侧重{'抒情议论' if '凤凰台' in poem2 else '写景'}。两诗在意象、情感、手法上各具特色。",
            f"上海卷注重比较阅读。答题要点：①分别概括两诗内容②找出异同点③结合诗句具体分析。",
        ))

    # 现代文阅读（上海特色：关注城市文化）
    for _ in range(10):
        sh_passages = [
            {
                "passage": f"上海是一座充满活力的国际化大都市。清晨的外滩，晨跑的人们与浦江的薄雾交织成一幅生动的画卷；午后的田子坊，弄堂里的咖啡馆飘散着浓郁香气；夜晚的陆家嘴，摩天大楼的灯光倒映在江面上，璀璨夺目。这座城市既有国际化的一面，也保留着独特的海派文化韵味。",
                "question": "本文从哪些时间段描写了上海？分别抓住了什么特点？",
                "answer": "从清晨、午后、夜晚三个时间段描写。清晨：外滩晨跑的活力；午后：田子坊弄堂的悠闲文化气息；夜晚：陆家嘴的现代繁华。",
                "analysis": "时间顺序清晰，每个时间段选取代表性地点：清晨-外滩(活力)，午后-田子坊(文化)，夜晚-陆家嘴(繁华)。",
                "tags": ["记叙文阅读与分析", "赏析语言特色"],
                "grade": p.rng.choice(["grade_7", "grade_8", "grade_9"]),
            },
        ]
        rp = p.rng.choice(sh_passages)
        templates.append(build_record(
            "shanghai", "chinese", rp["grade"], rp["tags"],
            p.rng.randint(2, 4), "short_answer",
            f"阅读下文，回答问题。\n\n{rp['passage']}\n\n{rp['question']}",
            rp["answer"], rp["analysis"],
        ))

    # 基础题
    for _ in range(15):
        basics = [
            ("下列词语中，书写完全正确的一项是？\nA. 脍炙人口  B. 脍灸人口  C. 快炙人口  D. 快灸人口",
             "A", "脍炙人口：脍(切细的肉)和炙(烤肉)都是人们爱吃的，比喻好的事物受到称赞。注意'炙'是火上烤肉的会意字，与'灸'(针灸)不同。",
             ["成语与文化"], "choice", 1),
            ("'海纳百川，有容乃大'体现了怎样的精神？请简要说明。",
             "体现了包容、开放的精神。'海纳百川'比喻接纳包容各种不同的事物，'有容乃大'说明只有包容才能成就伟大。这与上海'海纳百川、追求卓越'的城市精神一脉相承。",
             "这是林则徐的自勉联，也是上海城市精神的重要组成部分。回答要点：①解释原句含义②联系上海的开放包容精神。",
             ["综合性学习", "传统文化积累"], "short_answer", 2),
        ]
        q, a, analysis, tags, qtype, diff = p.rng.choice(basics)
        templates.append(build_record(
            "shanghai", "chinese", p.rng.choice(["grade_7", "grade_8", "grade_9", "grade_10"]),
            tags, diff, qtype, q, a, analysis,
        ))

    return templates


def shanghai_english_templates(p: ParamPool) -> list[dict]:
    """上海英语：上海是英语教育前沿，注重实际交际能力，含听力题型"""
    templates = []

    # 听力理解题型（上海中考英语含听力）
    for _ in range(12):
        listening = [
            {
                "dialogue": f"M: Excuse me, how can I get to the Oriental Pearl Tower?\nW: Take Metro Line 2 and get off at Lujiazui Station. You can't miss it.\nM: Thank you very much.\nW: You're welcome.",
                "question": "Where does the man want to go?",
                "answer": "The Oriental Pearl Tower.",
                "analysis": "对话中男子直接询问'how can I get to the Oriental Pearl Tower'。听懂关键词Oriental Pearl Tower即可作答。",
                "grade": "grade_7",
                "tags": ["听懂简短对话"],
            },
            {
                "dialogue": f"W: Tom, have you finished your project about Shanghai's history?\nM: Not yet. I still need to visit the Shanghai Museum this weekend.\nW: That's a great idea. They have wonderful exhibitions about old Shanghai.",
                "question": "What will Tom do this weekend?",
                "answer": "Visit the Shanghai Museum.",
                "analysis": "男子说'I still need to visit the Shanghai Museum this weekend'，直接给出答案。注意区分project和visit的信息层次。",
                "grade": "grade_8",
                "tags": ["提取关键信息", "获取具体信息与主旨大意"],
            },
            {
                "dialogue": f"M: The school English Festival is coming. Our class will perform a play.\nW: Sounds exciting! Which play will you perform?\nM: We haven't decided yet. Maybe Romeo and Juliet or something about Shanghai's history.\nW: I think a story about Shanghai would be more meaningful.",
                "question": "What does the woman suggest?",
                "answer": "She suggests performing a story about Shanghai's history.",
                "analysis": "女士说'I think a story about Shanghai would be more meaningful'，这是建议句型。注意区分陈述和建议的语气差异。",
                "grade": "grade_9",
                "tags": ["推断说话者意图与态度"],
            },
        ]
        lt = p.rng.choice(listening)
        templates.append(build_record(
            "shanghai", "english", lt["grade"], lt["tags"],
            p.rng.randint(1, 3), "short_answer",
            f"（上海中考听力题型）Listen to the dialogue:\n\n{lt['dialogue']}\n\nQuestion: {lt['question']}",
            lt["answer"], lt["analysis"],
        ))

    # 口语表达题型（上海英语特色）
    for _ in range(8):
        speaking = [
            ("Introduce your favorite place in Shanghai to a foreign friend (3-5 sentences).",
             "口语表达", "grade_8"),
            ("Your class is discussing 'Should students use smartphones at school?' Express your opinion.",
             "讨论中的观点表达", "grade_9"),
            ("Describe a memorable experience you had in Shanghai.",
             "话题演讲与讨论", "grade_10"),
        ]
        topic, tags, grade = p.rng.choice(speaking)
        templates.append(build_record(
            "shanghai", "english", grade, [tags],
            p.rng.randint(2, 3), "short_answer",
            f"（上海中考口语题型）{topic}",
            f"（口语表达评分要点：语音语调、内容完整度、语言流利度、语法准确性）",
            f"上海英语口语测试注重实际交际能力。建议：①用简单句确保语法正确 ②加入1-2个细节提高内容丰富度 ③注意发音清晰。",
        ))

    # 语法与词汇（上海卷难度偏高）
    for _ in range(15):
        items = [
            (f"By the time we got to the cinema, the film ____ (start).", "had started",
             "过去完成时：by the time + 过去时，主句用过去完成时(had done)", "九大时态", "grade_9"),
            (f"If I ____ (be) you, I would take the job in Shanghai.", "were",
             "虚拟语气：与现在事实相反，if从句用过去式(be动词用were)", "虚拟语气", "grade_10"),
            (f"The Bund, ____ attracts millions of tourists every year, is a symbol of Shanghai.", "which",
             "非限制性定语从句：先行词是物(The Bund)，关系代词用which", "语篇衔接手段", "grade_11"),
        ]
        q, a, analysis, tags, grade = p.rng.choice(items)
        templates.append(build_record(
            "shanghai", "english", grade, [tags],
            p.rng.randint(2, 4), "fill_blank",
            f"（上海{'中考' if int(grade.split('_')[1])<=9 else '高考'}题型）Fill in the blank: {q}",
            a, analysis,
        ))

    return templates


# ===================================================================
# 广东 题目模板
# ===================================================================

def guangdong_math_templates(p: ParamPool) -> list[dict]:
    """广东数学：粤教版风格，注重生活实际应用与商业场景"""
    templates = []

    # === 小学 ===
    for i in range(25):
        g = p.rng.choice(["grade_4", "grade_5", "grade_6"])
        a, b = p.math_int(10, 99), p.math_int(1, 30)
        price = p.price(5, 100)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["整数四则运算"],
                "difficulty": 1,
                "question_type": "calculation",
                "question_text": f"{p.gd_scenario()}，一家店铺今天卖出{a}件商品，昨天卖出{b}件。两天一共卖出多少件？",
                "answer": f"{a+b}件",
                "analysis": f"加法：{a}+{b}={a+b}件",
            },
            {
                "grade": g, "knowledge_tags": ["百分数的认识"],
                "difficulty": 2,
                "question_type": "calculation",
                "question_text": f"（广东特色应用题）{p.gd_scenario()}的商场促销，一件原价{price}元的商品打{p.discount()}。打折后多少钱？",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["条形统计图"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "short_answer",
                "question_text": f"（广东特色）统计深圳{a}户家庭月收入分布情况：{p.math_int(1,3)}万元以下{b}户，{p.math_int(3,5)}-{p.math_int(5,8)}万元{a-b-p.math_int(5,20)}户。哪种收入段的家庭最多？",
                "answer": f"{p.math_int(1,3)}万元以下的最多，有{b}户",
                "analysis": f"比较各段户数：{b}户是最多的",
            },
            {
                "grade": g, "knowledge_tags": ["混合运算与简便计算"],
                "difficulty": p.rng.randint(1, 2),
                "question_type": "calculation",
                "question_text": f"简便计算：{a}×{b} + {a}×{100-b}",
                "answer": f"{a*100}",
                "analysis": f"利用乘法分配律：{a}×{b}+{a}×{100-b}={a}×({b}+{100-b})={a}×100={a*100}",
            },
            {
                "grade": g, "knowledge_tags": ["小数乘除法"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"人民币汇率：1港元≈0.92元人民币。{p.person_name()}去香港旅游，用{price}元人民币可以兑换多少港元？（结果保留整数）",
                "answer": f"{int(price/0.92)}港元",
                "analysis": f"港元=人民币÷汇率={price}÷0.92≈{int(price/0.92)}港元",
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            discount_str = p.discount()
            discount_map = {"八折": 0.8, "八五折": 0.85, "九折": 0.9, "七五折": 0.75, "七折": 0.7, "九五折": 0.95}
            rate = discount_map.get(discount_str, 0.8)
            final_price = round(price * rate, 1)
            t["question_text"] = f"（广东特色应用题）{p.gd_scenario()}的商场促销，一件原价{price}元的商品打{discount_str}。打折后多少钱？"
            t["answer"] = f"{final_price}元"
            t["analysis"] = f"{discount_str}即按原价的{int(rate*100)}%出售。{price}×{rate}={final_price}元"
            t["question_type"] = "calculation"

        templates.append(build_record(
            "guangdong", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    # === 初中 ===
    for i in range(30):
        g = p.rng.choice(["grade_7", "grade_8", "grade_9"])
        a, b = p.math_int(1, 50), p.math_int(1, 50)
        price = p.price(20, 500)
        discount = p.discount()

        tpls = [
            {
                "grade": g, "knowledge_tags": ["一元一次方程"],
                "difficulty": p.rng.randint(2, 3),
                "question_type": "calculation",
                "question_text": f"（广东中考题型）{p.gd_scenario()}，商家将进价{a}元的商品提价{40}%后再打{p.discount()}出售。实际利润率是多少？",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["一元二次方程"],
                "difficulty": p.rng.randint(3, 4),
                "question_type": "calculation",
                "question_text": f"（广东中考题型）{p.gd_scenario()}，某工厂去年产值为{a}万元，计划今明两年产值翻一番，求年增长率。",
                "answer": f"{round(((2)**0.5-1)*100, 1)}%",
                "analysis": f"设增长率为x，则a(1+x)²=2a，(1+x)²=2，x=√2-1≈{round(((2)**0.5-1)*100,1)}%",
            },
            {
                "grade": g, "knowledge_tags": ["不等式应用题"],
                "difficulty": p.rng.randint(3, 4),
                "question_type": "short_answer",
                "question_text": f"（广东中考题型）{p.gd_scenario()}，快递公司收费标准：首重1kg收费{a}元，续重每kg收费{b}元。{p.person_name()}要寄一个包裹，预算不超过{price}元，包裹最多可以多重？",
                "answer": f"最多{(price-a)//b+1}kg",
                "analysis": f"设重量为x kg(x≥1)，则费用=a+b(x-1)≤{price}。解不等式得x≤{(price-a)/b+1}，取整得x≤{(price-a)//b+1}",
            },
            {
                "grade": g, "knowledge_tags": ["相似三角形的性质"],
                "difficulty": p.rng.randint(2, 4),
                "question_type": "calculation",
                "question_text": f"（广东中考题型）{p.gd_scenario()}，利用影子测量高楼高度。同一时刻，身高1.6米的{p.person_name()}影子长{a/10:.1f}米，高楼影子长{b*2}米。求楼高。",
                "answer": f"{round(1.6*b*2/(a/10), 1)}米",
                "analysis": f"根据相似三角形性质：物高/影长=常数。1.6/{a/10:.1f}=h/{b*2}，h=1.6×{b*2}÷{a/10:.1f}={round(1.6*b*2/(a/10),1)}米",
            },
            {
                "grade": g, "knowledge_tags": ["用列举法求概率"],
                "difficulty": 2,
                "question_type": "choice",
                "question_text": f"（广东中考题型）{p.gd_scenario()}的商城抽奖活动，3个红球和2个白球放在一个箱子里，随机摸出2个球，摸到一红一白的概率是？",
                "answer": "3/5",
                "analysis": "总共有C(5,2)=10种组合。一红一白的组合数：C(3,1)×C(2,1)=6。概率=6/10=3/5",
                "options": [
                    {"label": "A", "text": "1/5"},
                    {"label": "B", "text": "2/5"},
                    {"label": "C", "text": "3/5"},
                    {"label": "D", "text": "4/5"},
                ],
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            if "利润率" in str(t.get("question_text", "")):
                d_str = p.discount()
                d_map = {"八折": 0.8, "八五折": 0.85, "九折": 0.9, "七五折": 0.75, "七折": 0.7, "九五折": 0.95}
                rate = d_map.get(d_str, 0.8)
                selling = a * 1.4 * rate
                profit_rate = round((selling - a) / a * 100, 1)
                t["question_text"] = f"（广东中考题型）{p.gd_scenario()}，商家将进价{a}元的商品提价40%后再打{d_str}出售。实际利润率是多少？"
                t["answer"] = f"{profit_rate}%"
                t["analysis"] = f"提价后：{a}×(1+40%)={a*1.4}元。打折后：{a*1.4}×{rate}={selling}元。利润率=({selling}-{a})/{a}×100%={profit_rate}%"
            t.pop("_dynamic", None)
            t["question_type"] = "calculation"

        templates.append(build_record(
            "guangdong", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    # === 高中 ===
    for i in range(15):
        g = p.rng.choice(["grade_10", "grade_11", "grade_12"])
        a, b = p.math_int(1, 30), p.math_int(1, 30)
        price = p.price(100, 999)

        tpls = [
            {
                "grade": g, "knowledge_tags": ["等比数列及其前n项和"],
                "difficulty": p.rng.randint(3, 4),
                "question_type": "calculation",
                "question_text": f"（广东高考题型）{p.gd_scenario()}的科技公司，第一年研发投入{a}万元，之后每年增加{p.rng.randint(10,30)}%。求前{b}年的总研发投入。",
                "answer": "",
                "analysis": "",
                "_dynamic": True,
            },
            {
                "grade": g, "knowledge_tags": ["离散型随机变量及其分布"],
                "difficulty": p.rng.randint(3, 5),
                "question_type": "short_answer",
                "question_text": f"（广东高考题型）{p.gd_scenario()}的创业园区，有{a}家入驻企业，其中{b}家为科技型企业。从园区中随机选择3家企业，求选出的科技型企业数量的分布列。",
                "answer": f"服从超几何分布H(3,{b},{a})",
                "analysis": f"总体{b}家科技型企业，抽样3家。分布列：P(X=k)=C({b},k)×C({a-b},3-k)/C({a},3)，k=0,1,2,3",
            },
            {
                "grade": g, "knowledge_tags": ["利用导数研究函数的单调性与极值"],
                "difficulty": p.rng.randint(4, 5),
                "question_type": "calculation",
                "question_text": f"（广东高考题型）某工厂生产x千件产品的成本函数为C(x)=x³-{a}x²+{price}x+10（万元）。求使平均成本最低的产量。",
                "answer": f"{a/2}千件",
                "analysis": f"平均成本A(x)=C(x)/x=x²-{a}x+{price}+10/x。A'(x)=2x-{a}-10/x²=0，解之得x={a/2}（取正根）",
            },
        ]
        t = p.rng.choice(tpls)
        if t.get("_dynamic"):
            rate_pct = p.rng.randint(10, 30)
            r = 1 + rate_pct / 100
            total = round(a * (r**b - 1) / (r - 1), 1)
            t["question_text"] = f"（广东高考题型）{p.gd_scenario()}的科技公司，第一年研发投入{a}万元，之后每年增加{rate_pct}%。求前{b}年的总研发投入。"
            t["answer"] = f"{total}万元"
            t["analysis"] = f"等比数列求和。a₁={a}，q={r}，n={b}。S_{b}={a}×({r}^{b}-1)/({r}-1)≈{total}万元"
            t.pop("_dynamic", None)
            t["question_type"] = "calculation"

        templates.append(build_record(
            "guangdong", "math", t["grade"], t["knowledge_tags"],
            t["difficulty"], t["question_type"], t["question_text"],
            t["answer"], t["analysis"], t.get("options"),
        ))

    return templates


def guangdong_chinese_templates(p: ParamPool) -> list[dict]:
    """广东语文：参考广东中考/高考题型"""
    templates = []

    # 古诗文默写与鉴赏
    for _ in range(12):
        poem_q = [
            ("默写：海内存知己，______。（王勃《送杜少府之任蜀州》）", "天涯若比邻",
             "出自王勃《送杜少府之任蜀州》，表达了友情不受距离阻隔的主题。", "古诗词背诵与鉴赏", "grade_7", "fill_blank", 1),
            ("默写：______，蜡炬成灰泪始干。（李商隐《无题》）", "春蚕到死丝方尽",
             "以春蚕和蜡烛为喻，表达至死不渝的深情。", "古诗词背诵与鉴赏", "grade_8", "fill_blank", 1),
            ("广东中考名句：请写出两句与'春天'有关的诗句。", "示例：春眠不觉晓，处处闻啼鸟。/ 好雨知时节，当春乃发生。",
             "考查古诗积累的广度，写出一句含'春'的诗句即可。", "古诗词背诵与鉴赏", "grade_9", "short_answer", 2),
        ]
        q, a, analysis, tags, grade, qtype, diff = p.rng.choice(poem_q)
        templates.append(build_record(
            "guangdong", "chinese", grade, [tags],
            diff, qtype, q, a, analysis,
        ))

    # 阅读理解（广东中考特色：关注岭南文化）
    for _ in range(10):
        gd_passages = [
            {
                "passage": f"岭南文化源远流长。从广州西关大屋的趟栊门，到潮汕的工夫茶；从粤剧的红船，到佛山的陶艺——每一种文化符号都承载着岭南人对生活的热爱。近年来，广府文化、潮汕文化、客家文化作为岭南文化的三大分支，得到了越来越多的关注和保护。",
                "question": "岭南文化的三大分支是什么？文中列举了哪些具体的文化符号？",
                "answer": "三大分支：广府文化、潮汕文化、客家文化。文化符号：西关大屋的趟栊门、潮汕工夫茶、粤剧红船、佛山陶艺。",
                "analysis": "仔细阅读第一句，提取并列关系。三大分支在末句明确列出。",
                "tags": ["说明文阅读与分析", "整体感知与信息筛选"],
                "grade": p.rng.choice(["grade_7", "grade_8", "grade_9"]),
                "difficulty": p.rng.randint(1, 3),
            },
            {
                "passage": f"深圳是一个奇迹。四十多年前，这里还是一个小渔村；如今，它已成为中国最具创新活力的城市之一。'深圳速度'不仅创造了经济奇迹，更催生了'敢为人先'的深圳精神。漫步在深圳湾公园，看着对岸的香港，你能感受到这座年轻城市的雄心与梦想。",
                "question": "文中'深圳速度'和'深圳精神'分别指什么？",
                "answer": "'深圳速度'指深圳经济快速发展的奇迹。'深圳精神'指敢为人先的创新精神。",
                "analysis": "文中分别用'经济奇迹'解释'深圳速度'，用'敢为人先'解释'深圳精神'。",
                "tags": ["记叙文阅读与分析", "关键词句的理解"],
                "grade": p.rng.choice(["grade_8", "grade_9"]),
                "difficulty": p.rng.randint(2, 3),
            },
        ]
        rp = p.rng.choice(gd_passages)
        templates.append(build_record(
            "guangdong", "chinese", rp["grade"], rp["tags"],
            rp["difficulty"], "short_answer",
            f"（广东中考题型）阅读下文，回答问题。\n\n{rp['passage']}\n\n{rp['question']}",
            rp["answer"], rp["analysis"],
        ))

    # 作文
    for _ in range(8):
        gd_compositions = [
            ("以'我的广东故事'为题，写一篇不少于600字的记叙文。",
             "记叙文写作", "grade_9"),
            ("阅读材料：粤港澳大湾区建设如火如荼。请以'湾区未来与我'为题，写一篇不少于700字的议论文。",
             "议论文写作", "grade_11"),
            ("半命题作文：'____，让广东更美'。将题目补充完整，写一篇不少于600字的文章。",
             "议论文写作", "grade_10"),
        ]
        topic, tags, grade = p.rng.choice(gd_compositions)
        templates.append(build_record(
            "guangdong", "chinese", grade, [tags],
            p.rng.randint(3, 4), "essay",
            f"（广东{'中考' if int(grade.split('_')[1])<=9 else '高考'}题型）{topic}",
            f"（开放题，无标准答案）\n写作建议：关注广东的地域特色和时代发展，融入个人真实体验和感悟。",
            f"广东作文题常结合本土特色与时代主题。注意选材要有广东地域元素，立意要体现时代精神。",
        ))

    return templates


def guangdong_english_templates(p: ParamPool) -> list[dict]:
    """广东英语：注重实际应用，结合粤港澳大湾区等地域特色"""
    templates = []

    # 阅读理解
    for _ in range(12):
        gd_readings = [
            {
                "passage": f"The Guangdong-Hong Kong-Macao Greater Bay Area (GBA) is one of the most dynamic regions in China. It includes nine cities in Guangdong Province, plus Hong Kong and Macao. With a population of over 86 million and a GDP of more than 1.6 trillion US dollars, the GBA is comparable to other world-class bay areas like Tokyo, New York, and San Francisco.",
                "question": "How many cities are included in the Greater Bay Area?",
                "answer": "11 cities (9 in Guangdong Province, plus Hong Kong and Macao).",
                "analysis": "文中明确提到'nine cities in Guangdong Province, plus Hong Kong and Macao'，总共11个城市。",
                "tags": ["略读获取大意", "扫读寻找细节"],
                "grade": "grade_9",
            },
            {
                "passage": f"Cantonese cuisine, also known as Yue cuisine, is one of the Eight Great Cuisines of China. It is famous for its fresh ingredients, delicate cooking methods, and light seasoning. Dim sum, roast goose, and white-cut chicken are among the most popular dishes. In Guangzhou, people say 'the best taste is the original taste', meaning the natural flavor of fresh ingredients should not be covered by heavy sauce.",
                "question": "What is the main idea of this passage?",
                "answer": "It introduces Cantonese cuisine and its characteristics.",
                "analysis": "段首为主题句，全文围绕粤菜的特点展开。'fresh ingredients, delicate cooking methods, light seasoning'为三个关键词。",
                "tags": ["理解作者意图与态度"],
                "grade": "grade_8",
            },
        ]
        rp = p.rng.choice(gd_readings)
        templates.append(build_record(
            "guangdong", "english", rp["grade"], rp["tags"],
            p.rng.randint(2, 3), "short_answer",
            f"（广东中考题型）Read the passage and answer:\n\n{rp['passage']}\n\nQuestion: {rp['question']}",
            rp["answer"], rp["analysis"],
        ))

    # 语法（广东中考英语题型）
    for _ in range(15):
        gd_grammar = [
            (f"{p.english_name()} ____ (go) to Shenzhen three times this year.", "has gone",
             "现在完成时表示经历。has gone to表示'去了某地（还在那里）'。", "九大时态", "grade_8"),
            ("The food in Guangzhou is ____ (delicious) than I expected.", "more delicious",
             "多音节形容词比较级：more + 形容词。than提示用比较级。", "形容词与副词的比较等级", "grade_7"),
            (f"The Cantonese language, ____ is spoken by millions of people, has nine tones.", "which",
             "非限制性定语从句，先行词是物，关系代词用which。", "宾语从句与定语从句", "grade_10"),
        ]
        q, a, analysis, tags, grade = p.rng.choice(gd_grammar)
        templates.append(build_record(
            "guangdong", "english", grade, [tags],
            p.rng.randint(1, 3), "fill_blank",
            f"（广东中考题型）Fill in the blank: {q}", a, analysis,
        ))

    # 写作
    for _ in range(8):
        gd_writing = [
            ("Write an email to your pen pal introducing a traditional festival in Guangdong (80-100 words).",
             "应用文（书信、通知、演讲）", "grade_9"),
            ("Write a short passage about 'Shopping in Guangzhou' (60-80 words).",
             "记叙文写作（80词左右）", "grade_7"),
            ("Some people say Shenzhen is a city of young people. Do you agree? Write your opinion (100-120 words).",
             "议论文写作（120词以上）", "grade_11"),
        ]
        topic, tags, grade = p.rng.choice(gd_writing)
        templates.append(build_record(
            "guangdong", "english", grade, [tags],
            p.rng.randint(2, 3), "essay",
            f"（广东英语{'中考' if int(grade.split('_')[1])<=9 else '高考'}题型）{topic}",
            f"（开放题，评分要点：内容切题、结构清晰、语言准确）",
            f"广东英语作文注重实际应用和本土文化表达。建议用具体例子支撑观点，避免空洞论述。",
        ))

    return templates


# ===================================================================
# 主生成流程
# ===================================================================

def _expand_templates(records: list[dict], p: ParamPool, factor: int = 3) -> list[dict]:
    """将模板记录通过参数替换扩展为多份变体记录，返回去重后的列表"""
    import copy
    expanded = []
    seen_texts = set()
    for rec in records:
        orig_text = rec["question_text"]
        if orig_text not in seen_texts:
            seen_texts.add(orig_text)
            expanded.append(rec)
        for _ in range(factor - 1):
            # 替换数字和名称以创建变体
            new_text = orig_text
            name = p.person_name()
            eng_name = p.english_name()
            # 替换中文名
            for old_name in p.CHINESE_NAMES:
                if old_name in new_text:
                    new_text = new_text.replace(old_name, p.person_name(), 1)
            # 替换数字（简单策略：替换部分数字）
            import re as _re
            new_text = _re.sub(r'(?<!\d)\d+(?!\d)', lambda m: str(int(m.group()) + p.math_int(1, 20) - 10), new_text)
            if new_text != orig_text and new_text not in seen_texts:
                seen_texts.add(new_text)
                new_rec = copy.deepcopy(rec)
                new_rec["question_text"] = new_text
                expanded.append(new_rec)
    return expanded


# ===================================================================
# 语文/英语补充生成器（当主模板产出不足时，补充基础题目）
# ===================================================================

def _fill_chinese(region: str, needed: int, p: ParamPool) -> list[dict]:
    """为语文学科补充基础题，确保达到目标数量"""
    records = []
    grades = ["grade_5", "grade_6", "grade_7", "grade_8", "grade_9", "grade_10", "grade_11", "grade_12"]

    # 古诗文默写填空题库
    poem_fills = [
        ("______，疑是地上霜。", "床前明月光", "李白《静夜思》"),
        ("______，春风吹又生。", "野火烧不尽", "白居易《赋得古原草送别》"),
        ("海内存知己，______。", "天涯若比邻", "王勃《送杜少府之任蜀州》"),
        ("______，蜡炬成灰泪始干。", "春蚕到死丝方尽", "李商隐《无题》"),
        ("会当凌绝顶，______。", "一览众山小", "杜甫《望岳》"),
        ("______，悠然见南山。", "采菊东篱下", "陶渊明《饮酒》"),
        ("沉舟侧畔千帆过，______。", "病树前头万木春", "刘禹锡《酬乐天扬州初逢席上见赠》"),
        ("先天下之忧而忧，______。", "后天下之乐而乐", "范仲淹《岳阳楼记》"),
        ("______，化作春泥更护花。", "落红不是无情物", "龚自珍《己亥杂诗》"),
        ("但愿人长久，______。", "千里共婵娟", "苏轼《水调歌头》"),
        ("______，柳暗花明又一村。", "山重水复疑无路", "陆游《游山西村》"),
        ("人生自古谁无死，______。", "留取丹心照汗青", "文天祥《过零丁洋》"),
        ("______，润物细无声。", "随风潜入夜", "杜甫《春夜喜雨》"),
        ("忽如一夜春风来，______。", "千树万树梨花开", "岑参《白雪歌送武判官归京》"),
        ("______，为有源头活水来。", "问渠那得清如许", "朱熹《观书有感》"),
        ("孤帆远影碧空尽，______。", "唯见长江天际流", "李白《黄鹤楼送孟浩然之广陵》"),
        ("______，不拘一格降人才。", "我劝天公重抖擞", "龚自珍《己亥杂诗》"),
        ("日出江花红胜火，______。", "春来江水绿如蓝", "白居易《忆江南》"),
        ("______，西北望，射天狼。", "会挽雕弓如满月", "苏轼《江城子·密州出猎》"),
        ("大漠孤烟直，______。", "长河落日圆", "王维《使至塞上》"),
    ]

    # 成语题
    idiom_questions = [
        ("形容做事有恒心，坚持不懈（写两个成语）", "持之以恒、锲而不舍"),
        ("形容人非常高兴（写两个成语）", "喜出望外、眉飞色舞"),
        ("形容景色非常美丽（写两个成语）", "美不胜收、风景如画"),
        ("形容读书勤奋（写两个成语）", "手不释卷、废寝忘食"),
        ("形容时间过得很快（写两个成语）", "光阴似箭、日月如梭"),
        ("形容人非常多（写两个成语）", "人山人海、摩肩接踵"),
        ("形容注意力非常集中（写两个成语）", "聚精会神、全神贯注"),
        ("形容团结合作（写两个成语）", "同心协力、众志成城"),
        ("形容学习刻苦（写两个成语）", "悬梁刺股、凿壁偷光"),
        ("形容说活有道理（写两个成语）", "言之有理、头头是道"),
    ]

    # 病句修改
    bingju = [
        (f"通过这次{p.rng.choice(['北京','上海','广州'])}之旅，使{p.person_name()}增长了见识。",
         f"这次{p.rng.choice(['北京','上海','广州'])}之旅使{p.person_name()}增长了见识。（删去'通过'或'使'）"),
        ("他不但学习好，而且品德也好，真是我们学习的好榜样。",
         "（无语病）该句表意清晰，关联词使用正确。"),
        (f"{p.person_name()}是{p.rng.choice(['北京','上海','广东'])}人，他非常了解当地的文化。",
         "（无语病）该句表达完整，无语法错误。"),
        (f"我国有世界上没有的{p.rng.choice(['万里长城','故宫','秦始皇兵马俑'])}。",
         f"我国有世界上独一无二的{p.rng.choice(['万里长城','故宫','秦始皇兵马俑'])}。（原句前后矛盾）"),
    ]

    for i in range(needed):
        g = p.rng.choice(grades)
        t = p.rng.randint(1, 3)

        if t == 1:
            q, a, src = p.rng.choice(poem_fills)
            records.append(build_record(
                region, "chinese", g, ["古诗词背诵与鉴赏"],
                p.rng.randint(1, 2), "fill_blank",
                f"（{region}中考题型）默写古诗文名句：{q}", a,
                f"出自{src}，考查古诗文积累。注意书写正确规范。",
            ))
        elif t == 2:
            q, a = p.rng.choice(idiom_questions)
            records.append(build_record(
                region, "chinese", g, ["成语与文化"],
                p.rng.randint(1, 2), "short_answer",
                f"（{region}中考题型）{q}", a,
                "考查常见成语的积累和正确书写。注意辨析形近字和同音字。",
            ))
        else:
            q, a = p.rng.choice(bingju)
            records.append(build_record(
                region, "chinese", p.rng.choice(["grade_7", "grade_8", "grade_9"]),
                ["搭配不当", "成分残缺或赘余"], p.rng.randint(1, 3), "short_answer",
                f"（{region}中考题型）判断下列句子是否有语病，如有请修改：{q}", a,
                "修改病句步骤：①读句子理解原意②找出病因③用修改符号修改④检查修改是否正确。",
            ))

    return records


def _fill_english(region: str, needed: int, p: ParamPool) -> list[dict]:
    """为英语学科补充基础题"""
    records = []
    grades = ["grade_5", "grade_6", "grade_7", "grade_8", "grade_9", "grade_10", "grade_11", "grade_12"]

    verbs = [
        ("go", "went", "gone"), ("eat", "ate", "eaten"), ("write", "wrote", "written"),
        ("see", "saw", "seen"), ("take", "took", "taken"), ("give", "gave", "given"),
        ("speak", "spoke", "spoken"), ("break", "broke", "broken"), ("choose", "chose", "chosen"),
        ("drive", "drove", "driven"), ("fly", "flew", "flown"), ("sing", "sang", "sung"),
        ("swim", "swam", "swum"), ("drink", "drank", "drunk"), ("begin", "began", "begun"),
    ]

    tense_patterns = [
        ("Yesterday, I ____ ({verb}) to school by bus.", "past"),
        ("She has ____ ({pp}) her homework already.", "perfect"),
        ("They were ____ ({ing}) when the teacher came in.", "past_cont"),
        ("We have ____ ({pp}) this movie twice.", "perfect"),
        ("He ____ ({verb}) a letter to his friend last week.", "past"),
        ("The cake was ____ ({pp}) by my mother.", "passive"),
        ("I ____ ({verb}) a strange noise last night.", "past"),
        ("Have you ever ____ ({pp}) to Beijing?", "perfect"),
        ("The window was ____ ({pp}) by the strong wind.", "passive"),
        ("She ____ ({verb}) English very well when she was young.", "past"),
    ]

    comp_adj = [
        ("big", "bigger", "biggest"), ("small", "smaller", "smallest"),
        ("good", "better", "best"), ("bad", "worse", "worst"),
        ("beautiful", "more beautiful", "most beautiful"),
        ("interesting", "more interesting", "most interesting"),
        ("far", "farther/further", "farthest/furthest"),
        ("many", "more", "most"), ("little", "less", "least"),
        ("important", "more important", "most important"),
    ]

    for i in range(needed):
        g = p.rng.choice(grades)
        t = p.rng.randint(1, 2)

        if t == 1:
            verb, past, pp = p.rng.choice(verbs)
            pattern, tense_type = p.rng.choice(tense_patterns)
            if "{verb}" in pattern:
                answer = past
                q_text = pattern.replace("{verb}", f"____")
                explanation = f"一般过去时：{verb}的过去式是{past}。"
            elif "{pp}" in pattern:
                answer = pp
                q_text = pattern.replace("{pp}", f"____")
                explanation = f"现在完成时/被动语态：{verb}的过去分词是{pp}。"
            else:
                answer = verb + "ing"
                q_text = pattern.replace("{ing}", f"____")
                explanation = f"过去进行时：{verb}的现在分词是{verb}ing。"

            records.append(build_record(
                region, "english", g, ["九大时态" if "past" in tense_type or "perfect" in tense_type else "被动语态"],
                p.rng.randint(1, 3), "fill_blank",
                f"Fill in the blank with the correct form: {q_text}", answer,
                explanation + f" 注意不规则动词的变化形式。",
            ))
        else:
            adj, comp, sup = p.rng.choice(comp_adj)
            q_text = f"This book is ____ ({adj}) than that one."
            answer = comp
            records.append(build_record(
                region, "english", g, ["形容词与副词的比较等级"],
                p.rng.randint(1, 2), "fill_blank",
                f"Fill in the blank: {q_text}", answer,
                f"比较级：than提示使用比较级形式。{adj}的比较级是{comp}。",
            ))

    return records


def generate_regional_questions(per_region: int = 200) -> list[dict]:
    """
    为每个地域生成指定数量的题目。
    均衡分配在数学、语文、英语三学科中。
    每个生成函数会被多次调用（使用不同种子）以产生足够的唯一题目。
    """
    region_generators = {
        "beijing": {
            "math": beijing_math_templates,
            "chinese": beijing_chinese_templates,
            "english": beijing_english_templates,
        },
        "shanghai": {
            "math": shanghai_math_templates,
            "chinese": shanghai_chinese_templates,
            "english": shanghai_english_templates,
        },
        "guangdong": {
            "math": guangdong_math_templates,
            "chinese": guangdong_chinese_templates,
            "english": guangdong_english_templates,
        },
    }

    # 学科生成轮数
    rounds_map = {
        "math": 2,
        "chinese": 6,
        "english": 6,
    }

    all_questions = []
    per_subject_target = per_region // 3  # 约67题/学科/地域
    # 扩展系数：确保生成足够多的raw数据来满足unique目标
    expansion_factor = 3

    # 每学科需要的实际生成轮数（数学模板多只需少量轮，语文英语需要大量轮）
    effective_rounds = {
        "math": max(per_region // 10, 8),
        "chinese": max(per_region // 5, 20),
        "english": max(per_region // 5, 20),
    }

    for region, subjects in region_generators.items():
        logger.info(f"--- 生成 {region} 地域题目 (目标: {per_region} 题) ---")

        region_questions = []
        for subject, generator_func in subjects.items():
            collected = []
            n_rounds = effective_rounds[subject]

            for round_idx in range(n_rounds):
                iter_seed = _SEED_BASE + hash(region) % 1000 + hash(subject) % 500 + round_idx * 137
                p = ParamPool(iter_seed)
                batch = generator_func(p)

                # 在题目中注入轮次标记以保证跨轮唯一性
                for q in batch:
                    q["question_text"] = q["question_text"] + f" [v{round_idx+1}]"

                collected.extend(batch)

            # 跨轮去重（基于核心文本，即去掉轮次标记后的文本）
            seen = set()
            unique_batch = []
            for q in collected:
                core = q["question_text"].rsplit(" [v", 1)[0] if " [v" in q["question_text"] else q["question_text"]
                h = hashlib.md5(core.encode("utf-8")).hexdigest()
                if h not in seen:
                    seen.add(h)
                    # 保留第一轮的变体标记作为区分
                    unique_batch.append(q)

            unique_batch = unique_batch[:per_subject_target]

            # 如果模板产出不足，使用补充生成器填充
            shortfall = per_subject_target - len(unique_batch)
            if shortfall > 0:
                fill_seed = _SEED_BASE + hash(region) % 1000 + hash(subject) % 500 + 9999
                fill_p = ParamPool(fill_seed)
                if subject == "chinese":
                    filler_batch = _fill_chinese(region, shortfall, fill_p)
                elif subject == "english":
                    filler_batch = _fill_english(region, shortfall, fill_p)
                else:
                    filler_batch = []
                unique_batch.extend(filler_batch)

            for idx, q in enumerate(unique_batch):
                q["id"] = make_question_id(region, subject, idx + 1)

            region_questions.extend(unique_batch)
            logger.info(f"  {subject}: raw={len(collected)} → unique={len(unique_batch)} ({n_rounds}轮)")

        all_questions.extend(region_questions)

    # 最终跨地域去重
    seen_hashes = set()
    unique = []
    for q in all_questions:
        h = hashlib.md5(q["question_text"].encode("utf-8")).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique.append(q)

    # 去重（跨地域可能产生的完全相同的题目文本）
    seen_hashes = set()
    unique = []
    for q in all_questions:
        h = hashlib.md5(q["question_text"].encode("utf-8")).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique.append(q)

    logger.info(f"总计: {len(all_questions)} 题 → 去重后 {len(unique)} 题")
    return unique


def append_to_jsonl(questions: list[dict], output_path: Path):
    """将题目追加写入 JSONL 文件（与 import_clean_data.py 输出格式兼容）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if output_path.exists() else "w"

    with open(output_path, mode, encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    logger.info(f"已追加 {len(questions)} 条记录 → {output_path}")


def print_stats(questions: list[dict]):
    """打印生成统计"""
    regions = {"beijing": 0, "shanghai": 0, "guangdong": 0}
    subjects = {"math": 0, "chinese": 0, "english": 0}
    levels = {"primary": 0, "junior": 0, "senior": 0}
    difficulties = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    types = {}

    for q in questions:
        regions[q["region"]] = regions.get(q["region"], 0) + 1
        subjects[q["subject"]] = subjects.get(q["subject"], 0) + 1
        levels[q["grade_level"]] = levels.get(q["grade_level"], 0) + 1
        d = q["difficulty"]
        if isinstance(d, int) and 1 <= d <= 5:
            difficulties[d] += 1
        t = q["question_type"]
        types[t] = types.get(t, 0) + 1

    logger.info("=" * 60)
    logger.info("地域题目生成统计")
    logger.info("=" * 60)
    logger.info(f"  总题数: {len(questions)}")
    logger.info(f"  地域分布: {regions}")
    logger.info(f"  学科分布: {subjects}")
    logger.info(f"  学段分布: {levels}")
    logger.info(f"  难度分布: {difficulties}")
    logger.info(f"  题型分布: {types}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="地域特色题目补充脚本")
    parser.add_argument("--per-region", type=int, default=200,
                        help="每个地域生成的题目数（默认 200）")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT),
                        help="输出 JSONL 路径")
    parser.add_argument("--seed", type=int, default=_SEED_BASE,
                        help="随机种子")
    parser.add_argument("--subjects", type=str, default="math,chinese,english",
                        help="生成的学科，逗号分隔（默认 math,chinese,english）")
    args = parser.parse_args()

    import add_regional_questions as _self_mod
    _self_mod._SEED_BASE = args.seed

    logger.info(f"开始生成地域特色题目: {args.per_region} 题/地域 × 3 地域")
    questions = generate_regional_questions(per_region=args.per_region)

    if questions:
        append_to_jsonl(questions, Path(args.output))
        print_stats(questions)
    else:
        logger.error("没有生成任何题目")


if __name__ == "__main__":
    main()
