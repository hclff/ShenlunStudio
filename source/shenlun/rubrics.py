"""Versioned training designs informed by public ability outlines, not official weights."""
import copy
import re

from .domain import rubric_for,validate_rubric,rubric_fingerprint

TEMPLATE_VERSION='exam-type-1'
PROFILE_CHOICES=[('auto','自动匹配考试类别'),('generic','通用题型训练'),
    ('national_provincial','国考 · 中央/省级综合管理'),('national_local','国考 · 市地级综合管理'),
    ('national_law','国考 · 行政执法'),('jiangsu_a','江苏 · A类'),('jiangsu_b','江苏 · B类'),('jiangsu_c','江苏 · C类')]
NOTICE='软件自定训练量表：维度、权重及分档由软件设计，并非官方阅卷细则或官方采分点。题干和给定材料优先。'
SOURCES={
    'national':{'year':'2026','title':'中央机关及其直属机构2026年度考试录用公务员公共科目笔试考试大纲',
                'url':'https://www.forestry.gov.cn/c/www/gsgg/645383.jhtml',
                'access':'国家林草局官方转载的能力要求索引已核对；直连全文超时。'},
    'jiangsu':{'year':'2026','title':'江苏省2026年度考试录用公务员公共科目笔试考试大纲',
               'url':'https://www.jsxf.gov.cn/tzgg/art/2025/art_4b4f6c1ef283b21741ec26bc3c0ecc30.html',
               'access':'省公务员局发布，江苏先锋正文已核对。'}}
FOCUS={
    'generic':'依据本题任务、材料证据和文体评价；未指定地区能力侧重。',
    'national_provincial':'关注材料事实背后的关系、推理及问题解决；观点须有论据和分析。',
    'national_local':'关注工作目标、执行步骤与具体问题的解决，说明措施怎样落实。',
    'national_law':'涉及执法时核对职责权限、程序与服务对象；无依据不编造法条、罚款或执法权限。',
    'jiangsu_a':'关注综合分析、工作目标与解决问题的关系，用材料支撑判断和措施。',
    'jiangsu_b':'关注依法办事和公共服务；涉及执法时核对权限、程序和群众实际需求。',
    'jiangsu_c':'关注基层落实、群众诉求与沟通效果；应用写作应使对象理解并能采取行动。'}
# Order matches the existing per-question-type dimension IDs. Every row sums to100.
WEIGHTS={
    'national_provincial':[[15,35,30,15,5],[20,25,35,15,5],[25,20,35,15,5],[25,25,30,15,5],[25,20,30,10,10,5]],
    'national_local':[[20,40,25,10,5],[25,30,25,15,5],[25,20,35,15,5],[30,30,25,10,5],[25,25,25,10,10,5]],
    'national_law':[[20,40,25,10,5],[25,30,25,15,5],[30,20,30,15,5],[30,30,25,10,5],[25,25,25,10,10,5]],
    'jiangsu_a':[[15,35,30,15,5],[20,25,35,15,5],[25,20,35,15,5],[25,25,30,15,5],[25,20,30,10,10,5]],
    'jiangsu_b':[[20,40,25,10,5],[25,30,25,15,5],[30,20,30,15,5],[30,30,25,10,5],[25,25,25,10,10,5]],
    'jiangsu_c':[[20,40,25,10,5],[25,30,25,15,5],[25,20,35,15,5],[30,25,25,15,5],[25,25,20,15,10,5]]}
TYPE_CHECKS={
    '归纳概括':{
        'task':'识别题目问的是问题、原因、做法、成效、特点还是变化；只评价指定对象和材料范围。',
        'coverage':'按指定材料核对实质要点，合并重复事实；不能把做法替代成效，也不以关键词命中替代含义覆盖。',
        'accuracy':'概括后保留主体、对象、动作、结果及限定；不把部分情况扩成全部，不生造高阶术语。',
        'expression':'按材料和任务归类压缩，去除重复、背景枝节；题干要求一段话时不强制分条。'},
    '综合分析':{
        'task':'辨别词句理解、观点评析、比较、启示或关系任务；解释含义的题不机械补写对策。',
        'evidence':'每项解释、评价或比较须回到相关事实，区分材料明示与有限推断。',
        'reasoning':'说明关键词、因果或条件怎样关联；比较需有共同维度，关系题须讲清双方联系，不机械两面论。',
        'expression':'先形成回答任务的判断，再按实际关系展开；避免堆概念和套用固定三段。'},
    '提出对策':{
        'alignment':'逐项对应材料问题或成因；检查措施是否真正消除障碍，避免用宣传重视替代具体行动。',
        'evidence':'优先使用材料有效做法、失败原因的反向修正及可迁移经验；补充常识须说明适用条件。',
        'action':'说明实施主体、对象、动作与必要条件；按任务需要补步骤、资源或反馈，不能越过主体权限。',
        'expression':'程序任务按流程，协作任务按职责，紧急任务先处置再完善；措施短而可落实。'},
    '应用写作':{
        'task':'锁定作答身份、受众、文种和沟通目的；检查收件者能否据此理解事项或采取行动。',
        'evidence':'保留本任务所需的事实、对象、条件和安排；未知时间、地址、电话及政策依据不得补造。',
        'action':'通知突出事项与办理方式，提纲突出层次，讲话符合场合，短评保留分析；不一律写成部署清单。',
        'expression':'用语适应受众；标题、称谓、落款按题干和场景确定，只要求正文时不强加完整格式。'},
    '文章写作':{
        'task':'主张回应题干核心词及其关系，立意可由材料支撑；不因宏大表述或社论腔获得额外分数。',
        'evidence':'论据与分论点相关，事实、数据和政策可核验；不把材料案例机械堆砌，不虚构外部事例。',
        'reasoning':'检查主张、证据、机制分析和回扣；讲明为何支持观点及适用边界，措施罗列不能代替论证。',
        'structure':'分论点有区分且共同服务主张，段落关系清楚；不预设固定五段或政府社会个人分类。',
        'expression':'语言准确、自然、连贯，段落围绕明确观点展开；修辞和术语只在表达有用时使用。'}}
ANCHORS='分档参考（训练自定）：90—100实质完成；75—89主体完成且有局部缺项；60—74有明显遗漏或关系不清；40—59关键任务部分未完成；0—39严重偏离或证据不足。须据本维度原文说明理由，避免同一缺陷机械重复扣分。'


def _key(value):
    return re.sub(r'[\s（）()·\-_/]','',str(value or '')).casefold()


def infer_profile(exam,category):
    exam,category=_key(exam),_key(category)
    if exam in ('江苏省考','江苏','江苏公务员考试','江苏省公务员考试'):
        return {'a':'jiangsu_a','a类':'jiangsu_a','b':'jiangsu_b','b类':'jiangsu_b','c':'jiangsu_c','c类':'jiangsu_c'}.get(category,'generic')
    if exam in ('国考','国家公务员考试','国家公务员','中央机关公务员考试'):
        aliases={
            'national_provincial':('省部级','省级','中央省级综合管理','中央机关及其省级直属机构综合管理类','中央机关及其省级直属机构综合管理类职位'),
            'national_local':('市地级','地市级','市地级综合管理','市地级及以下直属机构综合管理类','市地级及以下直属机构综合管理类职位'),
            'national_law':('行政执法','行政执法类','行政执法类职位')}
        return next((profile for profile,words in aliases.items() if category in words),'generic')
    return 'generic'


def validate_selection(selection):
    if not isinstance(selection,str) or selection not in dict(PROFILE_CHOICES):raise ValueError('量表模板选项不受支持，请重新选择。')
    return selection


def resolve_template(question_type,exam='',category='',year='',selection='auto'):
    selection=validate_selection(selection)
    rows=rubric_for(question_type)
    profile=infer_profile(exam,category) if selection=='auto' else selection
    inferred=infer_profile(exam,category);warnings=[]
    if selection=='auto' and profile=='generic' and _key(exam) not in ('','通用训练'):
        warnings.append('考试或类别未匹配已支持的能力框架，本次使用通用题型训练量表；可补充类别或手动选择模板。')
    if selection not in ('auto','generic') and profile!=inferred:
        warnings.append('手动模板与填写的考试或类别不一致；题目信息保留，请核对模板适用性。')
    source=SOURCES.get('jiangsu' if profile.startswith('jiangsu_') else 'national') if profile!='generic' else None
    if source and str(year).strip()!=source['year']:
        warnings.append(('未填写年份' if not str(year).strip() else '尚未核验'+str(year)+'年度专属要求')+
                        '；本模板参考2026年公开能力要求，仅作训练参考，不能视作本题官方评分规则。')
    index=list(TYPE_CHECKS).index(question_type)
    for n,row in enumerate(rows):
        if profile!='generic':row['weight']=WEIGHTS[profile][index][n]
        row['description']=TYPE_CHECKS[question_type].get(row['id'],row['description'])
        if row['id'] in ('task','reasoning','action','expression') and profile!='generic':
            row['description']+=' 类别侧重：'+FOCUS[profile]+' 仅在本题相关时检查，不强加无关内容。'
        if row['id']=='constraints':row['description']='按本题字数、范围和文体检查；未知官方扣分标准时只说明影响，不虚构超字固定扣分。'
        row['description']+=' '+ANCHORS
    labels=dict(PROFILE_CHOICES)
    metadata={'id':TEMPLATE_VERSION+':'+profile+':'+question_type,'name':labels[profile]+' · '+question_type,
              'version':TEMPLATE_VERSION,'kind':'builtin','profile':profile,'selection':selection,
              'source_year':source['year'] if source else '', 'source_title':source['title'] if source else '',
              'source_url':source['url'] if source else '', 'source_access':source['access'] if source else '',
              'focus':FOCUS[profile],'notice':NOTICE,'warnings':warnings,
              'annual_verified':bool(source and str(year).strip()==source['year'])}
    return {'rubric':validate_rubric(rows),'metadata':metadata}


def submission_rubric(raw):
    selection=validate_selection(raw.get('rubric_selection','auto'))
    resolved=resolve_template(raw['question_type'],raw.get('exam',''),raw.get('category',''),raw.get('year',''),selection)
    if 'rubric' not in raw:return resolved
    rows=validate_rubric(raw['rubric'])
    if raw.get('rubric_template')==resolved['metadata'] and rubric_fingerprint(rows)==rubric_fingerprint(resolved['rubric']):
        return resolved
    metadata={'id':'custom:'+rubric_fingerprint(rows),'name':'用户自定义 · '+raw['question_type'],
              'version':'custom-1','kind':'custom','profile':'custom','selection':'custom',
              'source_year':'','source_title':'','source_url':'','source_access':'',
              'focus':'按用户提供的实际维度、权重和说明评价，题干与材料优先。',
              'notice':'用户自定义训练量表：实际维度和权重由用户提供或从模板复制。软件未核验其官方来源，不能视作已核验的官方阅卷细则，也未经真实阅卷校准。',
              'warnings':[],'annual_verified':False}
    return {'rubric':copy.deepcopy(rows),'metadata':metadata}
