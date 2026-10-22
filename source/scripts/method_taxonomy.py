"""申论写作方法族谱：按写作技法归族，而不是按名称字面相似度聚类。

候选方法卡的名称几乎一一不同，但它们描述的写作动作大量重合——"引权威原话
支撑论点""引权威论断开篇""引用政策文件佐证"在申论教学里是同一技法：**引证**。
字面聚类到 0.60 就收敛，剩下的孤卡不是"没有同类"，而是"同类换了说法"。

所以这里给出的是方法论分类，不是字符串规则：

- 第一维是**写作环节**：结构 / 开头 / 结尾 / 分论点 / 论证 / 对策 / 语言 /
  概念 / 段落。它决定一种技法"用在文章哪里"。
- 第二维是**手法**：并列、递进、对照、引证、举例、数据、设问、辩证、分主体、
  分机制……它决定"怎么用"。

两维交叉即一个方法族，族名用申论教学里的通用叫法（如"引用权威论证"）。同族
的多张卡合并成一条，族名作为合并后的方法名。

族表顺序即关键词兜底时的优先级：越具体的技法越靠前，笼统的"结构"类垫底。
模型归族（``scripts/classify_methods.py``）优先使用，关键词只作离线兜底与
模型结果的交叉校验。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    """One writing technique: where it is used, how it works, what it is called."""

    id: str
    name: str
    stage: str
    desc: str
    keys: tuple = ()


FAMILIES = (
    # ---- 开头：决定读者第一眼看到什么 ----
    Family('opening.authority', '引权威论述开篇', '开头',
           '用领导人讲话、政策原文、权威论断起笔，先立理论依据再引出中心论点',
           ('开篇立论', '论断开篇', '论述开篇', '引权威开篇', '权威开篇')),
    Family('opening.classic', '引经据典开篇', '开头',
           '用名言、古语、俗语、典故起笔，借经典文本的分量引出论题',
           ('引古语', '引名言', '引俗语', '引经典开篇', '经典开篇', '引语开篇')),
    Family('opening.question', '设问开篇', '开头',
           '以一问或连续设问起笔，用问题制造悬念并带出论述主线',
           ('设问开篇', '设问导入', '设问引出', '设问起笔', '设问切入', '设问破题')),
    Family('opening.background', '背景形势开篇', '开头',
           '先交代时代背景、形势任务、现实语境，再从背景中引出论题',
           ('背景开篇', '形势开篇', '背景导入', '形势导入', '背景引出', '交代背景')),
    Family('opening.case', '案例现象开篇', '开头',
           '以具体事件、场景、现象切入，由个例引出普遍问题',
           ('案例开篇', '事例开篇', '故事开篇', '现象开篇', '场景开篇', '个案开篇')),
    Family('opening.data', '数据开篇', '开头',
           '以统计数据、数量对比起笔，用事实的分量为论点开路',
           ('数据开篇', '数字开篇', '数据起笔', '统计开篇')),
    Family('opening.concept', '概念界定开篇', '开头',
           '先界定核心概念的内涵外延，把讨论对象说清楚再立论',
           ('界定开篇', '释义开篇', '概念开篇', '内涵开篇')),
    Family('opening.refute', '驳论开篇', '开头',
           '先摆出错误观点或认识偏差，加以批驳，破字当头立在其中',
           ('先破后立', '破立', '驳论开篇', '批驳开篇', '驳斥错误', '先立后破')),

    # ---- 结尾：决定文章收在哪里 ----
    Family('ending.summary', '总结升华结尾', '结尾',
           '收束全文要点并提升一层，落到规律、价值、意义上',
           ('结尾总结', '总结升华', '收束升华', '升华结尾', '提炼升华', '结尾点题')),
    Family('ending.call', '号召行动结尾', '结尾',
           '以行动要求、号召、动员收尾，把论述落到"做什么"',
           ('号召', '行动要求', '动员', '结尾提要求', '收束号召')),
    Family('ending.echo', '首尾呼应结尾', '结尾',
           '结尾回扣开头的意象、设问或判断，使全篇闭环',
           ('首尾呼应', '首尾照应', '回扣开头', '呼应开头', '首尾联系')),
    Family('ending.outlook', '展望未来结尾', '结尾',
           '以趋势判断、前景展望收束，把视线引向将来',
           ('展望', '前景', '未来展望', '结尾展望')),

    # ---- 对策：决定文章"怎么办"部分怎么写 ----
    Family('measure.subject', '分主体提对策', '对策',
           '按党委政府、企业、社会组织、公众等责任主体分工写对策，各就其位',
           ('多主体', '主体协同', '按主体', '主体定位', '责任主体', '主体分工',
            '各主体', '主体机制')),
    Family('measure.mechanism', '分机制环节提对策', '对策',
           '按决策、执行、保障、监督等机制环节拆分对策层次',
           ('机制环节', '按机制', '环节分层', '流程环节', '按环节', '产业链环节',
            '识别与整治')),
    Family('measure.concrete', '对策具体化', '对策',
           '每条对策写清主体、手段、目的、标准，落到可检查的抓手，不写口号',
           ('具体抓手', '细化', '具体化', '抓手', '动作', '条目化', '可操作')),
    Family('measure.closure', '闭环机制对策', '对策',
           '把对策组织成"制度—执行—监督—反馈"的闭环，强调衔接与回环',
           ('闭环', '全流程', '链条', '监督环节', '反馈机制')),
    Family('measure.layer', '分层级提对策', '对策',
           '按宏观—中观—微观，或思想—制度—行动分层写对策',
           ('分层提对策', '分层施策', '分层落实', '三层', '分层级', '思想制度行动',
            '认识理论举措')),
    Family('measure.problem_fit', '问题对应对策', '对策',
           '逐条列出问题，再逐条给出对应措施，一问一策、问题与对策咬合',
           ('问题对策对应', '对策对应', '问题短板导出', '问题倒逼', '对症', '一问题一对策',
            '问题依托对策', '聚焦问题')),
    Family('measure.dimension', '分维度提对策', '对策',
           '按领域、维度、方面分类列举对策，各类之间不交叉',
           ('分维度', '按维度', '分领域', '分类施策', '分类列举', '分方面')),

    # ---- 分论点：决定主体部分怎么立骨 ----
    Family('thesis.uniform', '统一句式领起分论点', '分论点',
           '用同构句式领起各分论点——判断句、动宾句、要字句、目标句，形式整齐',
           ('统一句式', '同构', '句式领起', '判断句领起', '动宾句', '要字句',
            '目标领起', '序词', '序数词')),
    Family('thesis.relation', '关系辨析立论', '分论点',
           '抓住一对核心关系（守正与创新、危与机、快与慢）辨析其辩证统一',
           ('关系定位', '成对关系', '关系范畴', '核心关系', '关系辨析', '双向关系')),
    Family('thesis.keyword', '核心词贯穿全篇', '分论点',
           '提炼一个核心词或核心意象，在全篇反复回扣，形成主线',
           ('核心词', '关键词贯穿', '核心意象', '主线', '贯穿全文', '反复回扣')),
    Family('thesis.parallel', '并列分论点提炼', '分论点',
           '提炼几个地位相当、角度并列的分论点，横向铺开主体',
           ('并列分论点', '并列分点', '分论点并列', '分方面并列', '并列支撑', '多角度并列')),
    Family('thesis.progressive', '递进分论点提炼', '分论点',
           '分论点之间层层推进，后一层以前一层为前提',
           ('递进分论点', '分论点递进', '分层递进', '递进推进', '层层递进')),
    Family('thesis.lead', '段首观点句领起', '分论点',
           '段首用一句话立论，再展开例证或分析，段意一目了然',
           ('段首立论', '首句立论', '观点句领起', '段首中心句', '领起段落', '段首句')),

    # ---- 论证：决定"为什么成立" ----
    Family('argument.authority', '引用权威论证', '论证',
           '引用权威论述、政策文件、领导人讲话作论据，借权威的分量支撑判断',
           ('引权威', '权威论述', '权威论断', '权威原话', '权威文献', '权威评价',
            '引政策', '政策依据', '制度依据', '引制度', '引述权威', '引用权威',
            '权威引述', '引据定调')),
    Family('argument.classic', '引用经典论证', '论证',
           '引用名言、古语、俗语、典故、经典文本作论据',
           ('引名言', '引古语', '引俗语', '引经典', '引用名言', '引经据典', '经典文本')),
    Family('argument.example', '举例论证', '论证',
           '用具体事例、个案、典型做法支撑观点，事理结合',
           ('举例', '事例', '实例', '案例', '个案', '事迹', '例证')),
    Family('argument.data', '数据论证', '论证',
           '用统计数据、数量对比、具体数字支撑判断，以数服人',
           ('数据', '数字', '统计', '量化')),
    Family('argument.contrast', '对比论证', '论证',
           '正反、今昔、内外、纵横对比，在差异中见出观点',
           ('对比', '对照', '反差', '横向纵向', '今昔', '古今对照', '前后对比',
            '纵向比较', '内外对照', '比较')),
    Family('argument.dialectic', '辩证分析论证', '论证',
           '两面看问题，辨析边界与分寸，防止绝对化、片面化',
           ('辩证', '两方面', '两面', '双向', '边界', '分寸', '统筹', '矛盾',
            '防止片面', '长短结合', '刚柔')),
    Family('argument.cause', '因果论证', '论证',
           '由现象追原因、由原因推结果，用因果链把道理推到底',
           ('因果', '归因', '追溯', '根源', '动因', '成因', '由表及里', '溯源',
            '推演', '推导')),
    Family('argument.metaphor', '比喻论证', '论证',
           '用比喻、类比、具体载体把抽象道理讲得可感',
           ('比喻', '类比', '喻', '借具体事物', '具象')),
    Family('argument.hypothesis', '假设反证', '论证',
           '假设相反情形推出荒谬结果，反证观点的必要性',
           ('假设', '反证', '预设极端', '倘若', '如果不然')),
    Family('argument.theory', '理论阐释论证', '论证',
           '用理论、原理、政策的逻辑讲清"为什么应当如此"',
           ('理论阐释', '阐释', '阐明', '理论分析', '理论体系', '释义', '原理', '内涵阐释')),
    Family('argument.history', '历史论证', '论证',
           '用史实、历史经验、演进过程作论据，从时间纵深取信',
           ('历史', '史实', '史论', '古及今', '历史阶段', '历史脉络', '历史经验',
            '时序', '演进')),
    Family('argument.refute', '驳论', '论证',
           '先摆错误观点与认识偏差，再逐层批驳，破中立论',
           ('驳', '辨误', '纠偏', '批驳', '错误观点', '偏差', '反方观点')),
    Family('argument.evaluate', '多方评价佐证', '论证',
           '借外部评价、多方观点、群众感受、成绩数据佐证论点',
           ('评价', '多方', '多元观点', '外部', '反响', '群众感受')),

    # ---- 语言：决定句子怎么写得有力 ----
    Family('language.question', '设问推进', '语言',
           '用连续设问推进段落与论证，一问一答层层剥开',
           ('设问', '追问', '问句', '三问')),
    Family('language.parallel', '排比强化', '语言',
           '用排比句、排比段落强化气势与节奏',
           ('排比', '排偶')),
    Family('language.short', '短句概括', '语言',
           '用短句、动宾短语、单字抓手概括要点，干净利落',
           ('短句', '动词短语', '单字', '概括要点', '清单式', '短语')),
    Family('language.antithesis', '对举对仗', '语言',
           '用对举句、对仗句揭示双重意义，句式对称',
           ('对举', '对仗', '对称', '双关', '整句')),
    Family('language.detail', '细节具象化', '语言',
           '用场景、细节、动作把抽象品质写实，让读者看见',
           ('细节', '场景', '动作', '具象化', '刻画', '描写')),

    # ---- 概念：决定讨论对象清不清楚 ----
    Family('concept.define', '概念界定', '概念',
           '界定核心概念的内涵外延，拆解关键词，划清适用范围',
           ('界定', '定义', '拆解', '辨析概念', '区分概念', '划清')),
    Family('concept.explain', '阐释概念内涵', '概念',
           '把概念的内涵、价值、意义讲清楚，让读者明白它指什么、为什么重要',
           ('阐释', '阐明', '说明', '讲清', '揭示内涵')),
    Family('concept.dialectic', '辩证界定概念', '概念',
           '用正反、边界、比较的方式界定概念，划清适用范围',
           ('辩证界定', '边界限定', '正反界定', '划清界限', '限定')),

    # ---- 段落：决定一段之内怎么组织 ----
    Family('paragraph.pattern', '段落展开范式', '段落',
           '固定段落结构：观点—例证—回扣，或引述—分析—举措',
           ('成段', '构段', '展开段落', '段落展开', '段内', '三段推进', '四步展开')),

    # ---- 结构：全文骨架，最笼统，垫底 ----
    Family('structure.total_part', '总分总结构', '结构',
           '总起观点—分条展开—总结收束，首尾包住主体',
           ('总分总', '总分', '分总', '总起分述', '总述', '先总后分')),
    Family('structure.parallel', '并列式结构', '结构',
           '几个层次横向并列，地位相当，无先后推进',
           ('并列', '平行', '横向')),
    Family('structure.progressive', '递进式结构', '结构',
           '层层深入，后一层以前一层为前提：是什么—为什么—怎么办',
           ('递进', '层层深入', '由小到大', '由表及里', '三问', '逐步深入')),
    Family('structure.contrast', '对照式结构', '结构',
           '全文以正反、今昔、内外两组对照铺开',
           ('对照', '正反', '今昔', '前后')),
    Family('structure.problem_solution', '问题对策式结构', '结构',
           '提出问题—分析原因—给出对策，主线清晰',
           ('问题导向', '问题—', '问题-', '提出问题', '问题依托')),
    Family('structure.timeline', '时间脉络式结构', '结构',
           '按时间、阶段、历史—现实—未来组织全文',
           ('时间线', '时间脉络', '时序', '时间顺序', '阶段', '历史现实未来', '按时间')),
    Family('structure.dialectic', '辩证分析式结构', '结构',
           '围绕成对范畴（危与机、长与短、破与立）展开两面分析',
           ('辩证分析', '辩证关系', '两面分析', '成对范畴')),
)

FAMILY_BY_ID = {family.id: family for family in FAMILIES}

# 环节词：一个方法名先落到"用在文章哪一部分"，再在该环节内挑手法。顺序即
# 优先级——"总分总结构谋篇"同时含结构词与总分词，按结构判才不错位。
STAGE_KEYS = {
    '结构': ('结构', '布局', '框架', '谋篇', '行文', '脉络', '架构', '骨架', '层次'),
    '开头': ('开篇', '开头', '起笔', '破题', '导入', '引题', '开题', '铺垫'),
    '结尾': ('结尾', '收束', '收官', '卒章', '落笔'),
    '概念': ('概念', '界定', '内涵', '定义', '释义'),
    '对策': ('对策', '举措', '措施', '施策', '部署', '办法', '建议', '抓手'),
    '分论点': ('分论点', '分点', '领起', '统领', '小标题', '观点句', '段首', '中心句', '段旨'),
    '论证': ('论证', '论据', '佐证', '支撑', '印证', '阐释', '阐明', '说理', '分析', '剖析'),
    '语言': ('排比', '设问', '句式', '对仗', '对举', '短句', '短语', '修辞'),
    '段落': ('段落', '构段', '成段', '段内'),
}

# 环节内的手法判定顺序：具体手法在前，笼统的"理论阐释""分维度"垫底，
# 否则"以细节故事阐释抽象概念"会被"阐释"吞进理论阐释族。
STAGE_PRIORITY = {
    '论证': ('argument.example', 'argument.data', 'argument.authority', 'argument.classic',
             'argument.contrast', 'argument.refute', 'argument.hypothesis', 'argument.cause',
             'argument.metaphor', 'argument.history', 'argument.evaluate',
             'argument.dialectic', 'argument.theory'),
    '对策': ('measure.subject', 'measure.mechanism', 'measure.closure', 'measure.problem_fit',
             'measure.concrete', 'measure.layer', 'measure.dimension'),
    '分论点': ('thesis.uniform', 'thesis.relation', 'thesis.keyword', 'thesis.parallel',
               'thesis.progressive', 'thesis.lead'),
    '开头': ('opening.authority', 'opening.classic', 'opening.question', 'opening.refute',
             'opening.background', 'opening.case', 'opening.data', 'opening.concept'),
    '结尾': ('ending.echo', 'ending.summary', 'ending.call', 'ending.outlook'),
    '概念': ('concept.dialectic', 'concept.define', 'concept.explain'),
    '语言': ('language.question', 'language.parallel', 'language.antithesis',
             'language.detail', 'language.short'),
    '结构': ('structure.total_part', 'structure.timeline', 'structure.problem_solution',
             'structure.progressive', 'structure.parallel', 'structure.contrast',
             'structure.dialectic'),
    '段落': ('paragraph.pattern',),
}


# 族内同义表达：方法卡的真实措辞远比族名丰富（"个案提炼""案例群导入"
# 都是举例），这些词补在族定义之外，便于单独调整而不动族表。
EXTRA_KEYS = {
    'opening.authority': ('权威文献', '权威论断', '引据开篇', '引述权威'),
    'opening.case': ('案例', '切入', '现象', '故事'),
    'opening.background': ('背景', '形势', '语境', '大势'),
    'opening.refute': ('纠偏', '错误观点', '误区'),
    'ending.summary': ('收束', '升华', '主旨', '点题'),
    'ending.echo': ('首尾呼应', '首尾照应', '回扣开头', '呼应开头', '结尾回扣', '结句回扣'),
    'ending.call': ('号召', '行动', '担当'),
    'measure.subject': ('协同', '主体', '各方', '分工'),
    'measure.dimension': ('维度', '方面', '分项', '分类'),
    'measure.layer': ('分层', '层级', '三层', '宏观', '中观', '微观'),
    'measure.concrete': ('具体', '细化', '举措', '落地', '操作'),
    'measure.problem_fit': ('针对', '对应', '对症', '问题短板', '问需'),
    'measure.mechanism': ('机制', '环节', '链条', '流程'),
    'measure.closure': ('闭环', '全周期', '首尾'),
    'thesis.lead': ('段首', '中心句', '段旨', '首句', '立段旨'),
    'thesis.parallel': ('并列', '多维', '平列', '多角度'),
    'thesis.uniform': ('句式', '对仗', '动宾', '判断句', '要字句', '序号', '领起', '小标题'),
    'thesis.keyword': ('贯穿', '核心意象', '主线', '回扣'),
    'thesis.relation': ('关系', '统筹', '范畴', '两方面定位'),
    'thesis.progressive': ('递进', '层层'),
    'argument.example': ('个案', '事迹', '案例群', '例证', '典型', '情境'),
    'argument.contrast': ('前后', '转变', '反差', '纵向', '横向', '对照', '比对', '比较'),
    'argument.cause': ('原因', '归因', '溯源', '成因', '动因', '推演', '后果'),
    'argument.metaphor': ('比喻', '类比', '意象', '借物', '喻'),
    'argument.history': ('古及今', '历史', '史实', '演进', '阶段'),
    'argument.authority': ('权威', '文件', '引据', '政策', '讲话'),
    'argument.evaluate': ('互证', '评价', '反响', '佐证', '多方'),
    'argument.theory': ('理论', '机理', '逻辑', '原理'),
    'argument.refute': ('先破', '后立', '批驳', '辨误', '纠偏'),
    'argument.data': ('数据', '数字', '统计', '量化'),
    'language.short': ('短句', '凝练', '概括', '短语'),
    'language.antithesis': ('对仗', '对举', '对照句式', '整句'),
    'language.detail': ('细节', '场景', '具象', '刻画'),
    'language.question': ('设问', '追问', '问句'),
    'language.parallel': ('排比', '排偶'),
    'structure.progressive': ('递进', '层层'),
    'structure.timeline': ('时间', '阶段', '纵向', '演进'),
    'structure.parallel': ('并列', '铺陈', '横向'),
    'structure.total_part': ('总起', '分述', '收束', '总论', '总括'),
    'structure.dialectic': ('辩证', '统筹', '两面'),
    'structure.problem_solution': ('问题', '对策', '问题导向'),
    'structure.contrast': ('对照', '正反', '今昔', '前后'),
    'concept.dialectic': ('辨析', '否定', '边界', '正反', '分寸', '防绝对化'),
    'concept.define': ('深化', '外延', '拆解', '划清'),
    'concept.explain': ('解释', '阐明'),
    'paragraph.pattern': ('成段', '段落', '段内', '展开段'),
}


def _keys(family):
    return family.keys + EXTRA_KEYS.get(family.id, ())


# 名称里没有任何环节词时（"借比喻化抽象为具体"），按手法从具体到笼统试：
# 先认比喻、数据、举例这类明确技法，再认语言、概念、分论点，最后才轮到
# 对策与结构这些几乎每篇文章都有的泛化动作。用环节顺序兜底会让"具体"
# 这类泛词把比喻卡抢进对策族。
GLOBAL_ORDER = (
    'argument.metaphor', 'argument.data', 'argument.example', 'argument.authority',
    'argument.classic', 'argument.contrast', 'argument.cause', 'argument.hypothesis',
    'argument.history', 'argument.refute', 'argument.evaluate', 'argument.dialectic',
    'argument.theory',
    'language.question', 'language.parallel', 'language.antithesis', 'language.detail',
    'language.short',
    'concept.dialectic', 'concept.define', 'concept.explain',
    'thesis.uniform', 'thesis.relation', 'thesis.keyword', 'thesis.parallel',
    'thesis.progressive', 'thesis.lead',
    'opening.authority', 'opening.classic', 'opening.question', 'opening.background',
    'opening.case', 'opening.data', 'opening.concept', 'opening.refute',
    'ending.echo', 'ending.summary', 'ending.call', 'ending.outlook',
    'measure.problem_fit', 'measure.mechanism', 'measure.closure', 'measure.subject',
    'measure.layer', 'measure.concrete', 'measure.dimension',
    'structure.total_part', 'structure.timeline', 'structure.problem_solution',
    'structure.progressive', 'structure.parallel', 'structure.contrast',
    'structure.dialectic',
    'paragraph.pattern',
)

_GLOBAL_CACHE = []


def _global():
    if not _GLOBAL_CACHE:
        rank = {family_id: index for index, family_id in enumerate(GLOBAL_ORDER)}
        _GLOBAL_CACHE.extend(sorted(FAMILIES, key=lambda f: rank.get(f.id, len(rank))))
    return _GLOBAL_CACHE


def detect_stage(name):
    """The article part a method name belongs to, or '' when no stage word appears."""
    text = str(name or '')
    for stage, keys in STAGE_KEYS.items():
        if any(key in text for key in keys):
            return stage
    return ''


def _ordered(stage):
    order = STAGE_PRIORITY.get(stage) or ()
    rank = {family_id: index for index, family_id in enumerate(order)}
    return sorted((f for f in FAMILIES if f.stage == stage),
                  key=lambda f: rank.get(f.id, len(rank)))


def family_ids():
    return tuple(family.id for family in FAMILIES)


def family_name(family_id):
    family = FAMILY_BY_ID.get(family_id)
    return family.name if family else ''


def classify_by_keywords(name, purpose=''):
    """Offline fallback: stage first, then the technique within that stage.

    Never crosses stages: a name that says "对策" is a measure card even when it
    also contains "辩证". When no technique in that stage matches, the card stays
    unclassified rather than being forced into a neighbouring family.

    ``purpose`` is the problem the card claims to solve ("解决对策空泛、层次混乱
    的问题"). It often names the article part that the method name leaves implicit,
    so it is used to detect the stage but never to pick the technique.
    """
    text = str(name or '').strip()
    if not text:
        return None
    stage = detect_stage(text)
    if stage:
        # 名称自己点明了环节，就不许跨环节——"刚柔并济提对策"不该进论证族。
        for family in _ordered(stage):
            if any(key in text for key in _keys(family)):
                return family
        return None
    # 名称没点环节（"引权威论述定调"）：先用 purpose 猜环节，再全局兜底。
    hint = detect_stage(purpose)
    pools = [_ordered(hint)] if hint else []
    pools.append(_global())
    for pool in pools:
        for family in pool:
            if any(key in text for key in _keys(family)):
                return family
    return None


def prompt_payload(items, batch_size=60):
    """Batches of ``{'key', 'name', 'purpose'}`` for the model, plus the family table."""
    table = [{'id': f.id, 'name': f.name, 'stage': f.stage, 'desc': f.desc} for f in FAMILIES]
    batches = []
    for start in range(0, len(items), batch_size):
        batches.append({'families': table, 'items': items[start:start + batch_size]})
    return batches


def parse_assignments(data, valid_ids=None):
    """Normalise a model reply into ``{key: family_id}``; unknown ids are dropped."""
    valid = set(valid_ids or family_ids())
    out = {}
    for row in (data or {}).get('assignments') or []:
        if not isinstance(row, dict):
            continue
        try:
            key = int(row.get('key'))
        except (TypeError, ValueError):
            continue
        family = str(row.get('family') or '').strip()
        if family in valid:
            out[key] = family
    return out
