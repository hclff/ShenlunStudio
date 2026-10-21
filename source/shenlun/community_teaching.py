"""Short, independently rewritten community methods; no remote skill execution.

Teacher IDs only group the repository's claimed perspective. They never certify
authorship. Provenance and this distinction travel with every frozen method.
"""
from urllib.parse import quote

from .teaching import teaching_content_hash


REPOSITORIES = {
    'coffe': ('coffe-d/Shenlun.skill', '354341cdce1f357bb78c24f05a929a339cd7f4cf',
              'MIT'),
    'hill': ('hilltahuang/gongkao-distill', '36d2d10d2ac3299974e762914db7875484605ed2',
             '未注明'),
    'wang': ('WangJunqing-coder/shenlun-skill', 'ba3760375c270735ff49d2dfa58ab53205553a9f',
             '未注明'),
}
COMMUNITY_TEACHER_IDS = frozenset(('bailu','yuandong','limengyuan','xiaomage',
    'zhanzhang','sihai_feiyang','zhangxiaolong','zhongjun','shanshuling'))
_BL = 'skills/shenlun-xiaoti/references/bai-lu-methodology.md'
_YD = 'skills/shenlun-dawenti/references/yuan-dong-methodology.md'
_XM = 'skills/shenlun-xiaoti/references/xiaoma-methodology.md'
_WANG = 'references/mingshi-xiangjie.md'
_HILL = 'source/公务员/申论/{}/SKILL.md'
_ANCHOR_LINES = {'GH_BL01':46,'GH_BL02':13,'GH_YD01':77,'GH_YD02':9,
                 'GH_LM01':6,'GH_LM02':61,'GH_XM01':44,'GH_ZZ01':211,
                 'GH_FY01':533,'GH_ZX01':72,'GH_ZJ01':15,'GH_SS01':71}
def community_teaching_methods():
    """Return detached version-zero drafts, visible after a normal app upgrade."""
    rows = [
        ('GH_BL01','bailu','白鹭·按任务约束审题','coffe',_BL,'一、题干四要素审题法','名词定对象',
         ['归纳概括','综合分析','提出对策','应用写作'], '先把题目要求转为可逐项检查的任务',
         ['写明作答动作、所问对象、材料范围与身份。',
          '列出题干明确要求和字数限制，区分必须回答与可省略内容。',
          '逐条核对原答是否完成任务，并用本题材料说明缺失。'],
         '不按仓库预判句数定答案条数；不因动词相同就认定题型完全相同。'),
        ('GH_BL02','bailu','白鹭·多要素对应整理','coffe',_BL,'本库验证状态；八、白鹭 vs 小马哥：差异对照','双要素配对',
         ['归纳概括','综合分析','提出对策'], '检查多对象、多要素问题的对应关系',
         ['按题干所问对象或要素建立对应项，而非先固定分段数量。',
          '把同一对象的举措、成效或问题、建议逐项关联并标出证据。',
          '核对缺项、错配与重复，按本题需要选配对呈现或分栏表达。'],
         '不是每道多要素题都必须分栏；成品答案样本不能证明思考过程。'),
        ('GH_YD01','yuandong','袁东·主题关系与论证','coffe',_YD,'三、考查形式及双主题讲解','双主题',
         ['文章写作'], '先明确主题之间的关系，再检查文章是否真正解释关系',
         ['从题干和材料提出关于主题关系的完整主张，区分并列、相互作用和共同指向。',
          '逐段检查论据能否支撑主张，指出只有两个主题各说一遍而缺少联系的位置。',
          '材料支持互动时说明作用环节与成立条件，不为凑关系虚构因果。'],
         '不强制互相促进、固定三段或特定标题公式；仓库分档分数未作阅卷校准。'),
        ('GH_YD02','yuandong','袁东·论据到结论的连接','coffe',_YD,'零、第一原则及分论点讲解','公式是备用工具',
         ['文章写作'], '把模板当作检查思路的可选工具，补足论证连接',
         ['标出段落主张、使用的事实和已写出的解释。',
          '检查事实为何能支持该主张；缺连接时补充基于材料的分析。',
          '允许举例、比较、机制分析等表达，最后核对与全文中心是否一致。'],
         '不要求名言、固定开头结尾或五步句式；不按是否套模板决定分档。'),
        ('GH_LM01','limengyuan','李梦圆·区分摘录概括与推断','hill',_HILL.format('李梦圆'),'3.1 单一题','找点三板斧',
         ['归纳概括','综合分析','提出对策'], '区分材料事实、归纳结论和有限推断',
         ['围绕题目建立事实清单，保留行为、对象和必要限定。',
          '对同义信息归并，说明从具体事例到概括表达的依据。',
          '确需推断时单独写明依据和不确定性；没有支撑的内容不补造。'],
         '仓库大量细步自标粉笔通用或学员笔记；不能据此认定为李梦圆独创，不按原词命中自动扣分。'),
        ('GH_LM02','limengyuan','李梦圆·按沟通目的组织应用文','hill',_HILL.format('李梦圆'),'2 公文目的与3.4 公文','内容由目的决定',
         ['应用写作'], '让正文内容和语言服务题设沟通任务',
         ['明确谁面向谁、要告知什么或推动什么行动。',
          '据此选用事实、理由、措施或号召；每项内容回指给定材料。',
          '检查文种、身份、语气和题干要求的形式，删去无关模块。'],
         '归属为仓库整理的共享体系；不把四类目的当全部文种或统一格式评分表。'),
        ('GH_XM01','xiaomage','小马哥·主旨与支撑细节分层','coffe',_XM,'解题总框架及答案结构','读出层次，找对大哥',
         ['归纳概括','综合分析','应用写作'], '保留核心意思，同时检查具体事实是否支撑概括',
         ['在相关材料中区分主旨、做法和支撑细节，按实际话题分层。',
          '逐项检查概括与细节的对应，保留题目需要的具体信息。',
          '根据任务决定分条或成文，核对含义完整、没有同义反复。'],
         '仓库对总括句及固定框架的结论存在冲突；不强制总括句、固定层次或按样本分值评分。'),
        ('GH_ZZ01','zhanzhang','站长申论·从任务反查答案','wang',_WANG,'3.3 出题人视角','看题干关键词',
         ['归纳概括','综合分析','文章写作'], '用题干要求反查回答的相关性和证据',
         ['把题干所问改写为可检查的问题，明确对象、角度和作答范围。',
          '逐条核对原答在回答哪个问题，材料是否确实支持该回答。',
          '指出遗漏与答非所问之处，仅对材料支持的部分提出修改。'],
         '站长归类对应站长爱申论；仓库四步释义未得到教师原图核实，本卡不把四步当其确定讲法，也不揣测命题者隐秘意图。'),
        ('GH_FY01','sihai_feiyang','四海／飞扬·按问题做针对复盘','wang',_WANG,'7.4 套题实战法','针对性改进',
         ['归纳概括','综合分析','提出对策','应用写作','文章写作'], '把一次作答的问题转为下一次可检验的训练动作',
         ['依据本题量表和材料列出遗漏、误读与论证不足，不把机构参考答案视作唯一标准。',
          '选择一个主要问题，写清下次练习的具体动作与检查办法。',
          '同题改写后核对该问题是否改善；保留评分条件变化说明。'],
         '限时按实际考试设置，不照搬180分钟，不采用关键词给分承诺。'),
        ('GH_ZX01','zhangxiaolong','张小龙·多要素分块与连贯','hill',_HILL.format('张小龙'),'2 题型识别与机械／有机综合','要素分块相加',
         ['归纳概括','综合分析','应用写作'], '区分需要并列列项与需要组织为连贯表达的任务',
         ['数清本题要求回答的要素，分别寻找对应材料。',
          '并列问法可分块；面向受众的成文任务检查要素之间的解释、劝说和衔接。',
          '检查各要素完整且没有越界添加，将材料外的补充明确标成推断。'],
         '仅指公考申论教师张小龙；书目目录和仓库自评级不能证明每一操作均为教师原话。'),
        ('GH_ZJ01','zhongjun','钟君·用框架检索后回到材料','hill',_HILL.format('钟君'),'核心透镜与3.3 对策','框架是检索器不是填空卷',
         ['提出对策','综合分析'], '用可选角度检查遗漏，再删除没有材料依据的建议',
         ['根据问题及成因提出候选行动，说明与问题的对应关系。',
          '检查实施主体、行动对象、实际条件与预期作用。',
          '删除只有口号、与题目无关或超出材料依据的框架项。'],
         '不照搬万能条数、主体或固定对策；原书方法名称不等于本轮已核完整课堂细步。'),
        ('GH_SS01','shanshuling','单淑玲·区分做法经验与建议','hill',_HILL.format('单淑玲'),'2 单一题问法子路由','问法子路由',
         ['归纳概括','提出对策'], '让信息加工的程度与题目所问匹配',
         ['先判断题目问具体做法、可借鉴经验，还是要求提出建议。',
          '做法保留动作和对象；经验说明可迁移原则及条件；建议与问题逐项对应。',
          '按实际话题组织，核对信息完整、限定准确和语义连贯。'],
         '细步主要来自仓库自述的学员笔记与共享体系；教师公开理念不能为全部细步背书。经验概括也不能删掉决定适用性的条件。'),
    ]
    result = []
    for identity,owner,name,repo_key,path,section,anchor,types,purpose,steps,boundary in rows:
        repo,commit,license_note = REPOSITORIES[repo_key]
        source = {'title':f'{repo} / {path}',
                  'url':f'https://github.com/{repo}/blob/{commit}/'+quote(path,safe='/')+f'#L{_ANCHOR_LINES[identity]}',
                  'author':repo.split('/')[0],
                  'published_at':'','accessed_at':'2026-10-17','access_scope':'full_text',
                  'locator':f'第{_ANCHOR_LINES[identity]}行；{section}；commit {commit}；许可：{license_note}',
                  'excerpt':anchor,'quote':anchor}
        record = {'id':identity,'owner_id':owner,'name':name,
                  'purpose':purpose,'steps':steps,
                  'checks':['每项观察须有本题材料依据，推断另行标明。',
                            '只做方法辅助；不按固定模板、关键词命中或仓库分值直接评分。'],
                  'applicable_types':types,'not_applicable':boundary,'sources':[source],
                  'version':0,'enabled':False,'status':'draft','reviewer':'',
                  'note':'',
                  'created_at':'2026-10-17T00:00:00+08:00'}
        record['content_hash'] = teaching_content_hash(record)
        result.append(record)
    return result
