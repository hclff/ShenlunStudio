"""Pure source catalog: partial public-method drafts, never active database seeds."""
from .teaching import teaching_content_hash
from .community_teaching import community_teaching_methods


def teaching_owners():
    rows = [
        ('bailu','白鹭','pending','',['https://www.bilibili.com/video/BV1rg4y1R7Js']),
        ('yuandong','袁东','pending','',['https://www.bilibili.com/video/BV167Voz5Ecm/']),
        ('limengyuan','李梦圆','partial','',['https://www.fenbi.com/page/teacher/287']),
        ('fenbi','粉笔（机构）','partial','',['https://www.fenbi.com/page/information-hub-detail/9585']),
        ('zhanzhang','站长申论（站长爱申论）','partial','',['https://www.sina.cn/news/detail/5183528888175481.html','https://www.sina.cn/news/detail/5306381326615897.html']),
        ('sihai_feiyang','四海公考／飞扬','pending','',['https://sihaigongkao.mddclass.com/?tab=allcourse','https://www.bilibili.com/video/BV1Fh411W7H6/']),
        ('xiaomage','小马哥','pending','',['https://www.bilibili.com/video/BV1YM4y137h3','https://www.bilibili.com/video/BV1P34y167K4/']),
        ('zhangxiaolong','张小龙','partial','',['https://hb.huatu.com/xyzl/2012/0405/234277.html']),
        ('zhongjun','钟君','partial','',['https://www.huatu.com/a/ziliao/gwy/shenlun/2012/0620/295683.html']),
        ('tangdi','唐棣','partial','',['https://k.sina.cn/article_6015321269_1668a84b5001002g7z.html']),
        ('shanshuling','单淑玲','pending','',['https://www.fenbi.com/page/teacher/675']),
    ]
    return [{'id':i,'name':n,'status':s,'description':d,'urls':u} for i,n,s,d,u in rows]


_SOURCES = {
    'S01': ('粉笔李梦圆：2014国考省部级两题解析','https://blog.sina.com.cn/s/blog_bf0500d90101rh2x.html','李梦圆','2013-11-25','full_text','两题要求及括号方法提示'),
    'S02': ('粉笔：题型策略版','https://www.fenbi.com/page/information-hub-detail/9585','粉笔','2026-08-14','full_text','归纳、分析、公文、作文对应小节'),
    'S03': ('粉笔：作答策略与失分避坑指南','https://www.fenbi.com/page/information-hub-detail/9897','粉笔','','index_text','搜索索引正文：对策小节；直接页面仅加载壳'),
    'S04': ('站长爱申论：分析题材料阅读方法','https://www.sina.cn/news/detail/5183528888175481.html','站长爱申论','2025-07-01','index_text','署名搜索索引正文：材料加工与推断段；直接打开失败'),
    'S05': ('张小龙：从命题技术看421联考申论答题','https://hb.huatu.com/xyzl/2012/0405/234277.html','张小龙','2012-04-05','full_text','材料取舍与布局段'),
    'S06': ('张小龙：文章写作与独立思考','https://wx.huatu.com/htnews/gongwuyuanty/shenlun/2012/0423/48517.html','张小龙','2012-04-23','full_text','特定关系作文署名讲解：独立思考段'),
    'S07': ('钟君：申论备考策略','https://www.huatu.com/a/ziliao/gwy/shenlun/2012/0620/295683.html','钟君','2012-06-20','full_text','仅第一页：提出与解决问题段'),
    'S08': ('唐棣：你所理解的策论文','https://k.sina.cn/article_6015321269_1668a84b5001002g7z.html','唐棣','2018-01-09','index_text','署名搜索索引正文：论证与列措施辨别段；直接打开失败'),
}


def builtin_teaching_methods():
    """Return fresh detached version-zero drafts with short exact anchors only."""
    rows = [
        ('LM01','limengyuan','按所问对象分类组织','S01','分类分条',['归纳概括'],'根据所问对象分类组织信息',['逐项列出材料事实与编号','根据对象分类，说明重复或交叉信息'],'材料决定类别，不照搬示例点数或分类名称。'),
        ('LM02','limengyuan','联系语境解释词句','S01','字面+引申+评价',['综合分析'],'联系上下文解释概念及相关内涵',['先写语境解释','列出支持解释的材料事实'],'示例层次不要求每题照搬。'),
        ('FB01','fenbi','找信息后合并整理','S02','合并',['归纳概括'],'围绕任务找信息，再合并整理',['保留相关事实，区分枝节','核对合并是否遗漏对象和条件'],'同义准确表达不因缺某词直接扣分。'),
        ('FB02','fenbi','按任务组织分析槽位','S02','依托材料',['综合分析'],'用可选择的结构组织材料分析',['按任务选择解释、原因、影响或建议槽位','为实际使用的槽位标注材料证据'],'不强制每题写全是什么、为什么、怎么办。'),
        ('FB03','fenbi','应用文题意与形式核对','S02','题干要求为准',['应用写作'],'先看题目对正文和形式的限制',['列出身份、受众和沟通目的','核对本题文种及所需形式要素'],'形式随文种和题意核对，不套统一格式扣分。'),
        ('FB04','fenbi','按关键词关系核对立意','S02','立意准确',['文章写作'],'从题干关键词关系核对作文主张',['比较题干关系、拟定主张及材料支撑','指出遗漏的关键词及关系'],'材料未用于小题，不等于必然用于作文。'),
        ('FB05','fenbi','对策与主体权限对应','S03','超出自身权限',['提出对策'],'结合问题与实施身份设计行动',['列明问题、行动、负责主体和权限依据','常识补充另行标注'],'未给预算、部门或条文，不补造具体规定。'),
        ('ZZ01','zhanzhang','材料转化与必要推断','S04','必要的推理判断',['综合分析'],'在材料基础上作必要转化和判断',['将材料做法改写为能解释结果的原因','注明证据与推断跨度'],'最终只答所问，不写无关完整推理流水账。'),
        ('ZX01','zhangxiaolong','区分直接概括与加工事实','S05','分类整理',['归纳概括','综合分析'],'区分现成概括和需要加工的事实',['标记可直接使用、需抽象或需有限推断的信息','对分散证据按所问对象分类'],'不照搬历史抄写比例和当年难度判断。'),
        ('ZX02','zhangxiaolong','作文认识与论证联系','S06','独立的见解',['文章写作'],'依据题意形成有材料支撑的认识',['检查分论点是否回应主张','检查论据之后是否说明与主张的联系'],'题干未允许的经历、感受或外部案例不强加。'),
        ('ZJ01','zhongjun','措施实施条件与实际作用','S07','可行性与可操作性',['提出对策','应用写作'],'检查措施的实施条件与实际作用',['列出问题、实施者、对象和行动','复查可行条件与建议的具体作用'],'不引入历史卷种预测，不按万能条数凑对策。'),
        ('TD01','tangdi','区分列措施与论证措施','S08','论证',['文章写作'],'区分列出措施和论证措施',['标出主张、证据和分析','只有口号或措施时提示补充论证联系'],'不把应用文一律判为无需论证，不强制五段三分。'),
    ]
    result = []
    for identity,owner,name,source_id,anchor,types,purpose,steps,boundary in rows:
        title,url,author,date,scope,locator = _SOURCES[source_id]
        source = {'title':title,'url':url,'author':author,'published_at':date,'accessed_at':'2026-10-16',
                  'access_scope':scope,'locator':source_id+'；'+locator,'excerpt':anchor,'quote':anchor}
        record = {'id':identity,'owner_id':owner,'name':name,'purpose':purpose,'steps':steps,
                  'checks':['核对每项结论的材料依据；尊重题干、量表和已确认参考要点。'],
                  'applicable_types':types,'not_applicable':boundary,'sources':[source],
                  'version':0,'enabled':False,'status':'draft','reviewer':'','note':'',
                  'created_at':'2026-10-16T00:00:00+08:00'}
        record['content_hash'] = teaching_content_hash(record)
        result.append(record)
    return result + community_teaching_methods()
