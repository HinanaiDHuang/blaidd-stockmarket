# -*- coding: utf-8 -*-
"""新闻素材库（v4.1）。

单独成文件是因为模板量已经很大（120 条普通快讯 + 44 条爆炸性新闻），
放在 engine.py 里会把引擎逻辑埋掉。

普通快讯 5 元组：(标题模板, 最小冲击, 最大冲击, 作用范围, 题材)
爆炸性新闻 dict：kind/scope/lo/hi/tpl/mood/hours/label/cat/circuit
  - scope: market / industry / company
  - mood: 情绪周期强度（-1 ~ 1），黑天鹅之后会持续数小时
  - hours: 情绪周期持续小时数
  - circuit: 触发后临时休市分钟数（0 = 不休市）
"""
import random

NEWS_TEMPLATES = [
    ('{name} 发布季报，营收同比增长 {num}%，超机构预期', 0.02, 0.08, 'company', '财报'),
    ('{name} 季报利润腰斩，{ind}板块跟着一起绿', -0.04, -0.12, 'company', '财报'),
    ('{name} 现金流告急，账上现金撑不过两个月', -0.04, -0.11, 'company', '财报'),
    ('{name} 审计师突然换所，财务总监连夜辞职', -0.03, -0.1, 'company', '财报'),
    ('{name} 毛利率创新高，老板在内部会上只说了两个字：辛苦', 0.02, 0.07, 'company', '财报'),
    ('{name} 季度报表难看，市场开始重新给它估值', -0.03, -0.09, 'company', '财报'),
    ('{name} 被监管约谈，{ind}行业风声鹤唳', -0.03, -0.09, 'company', '监管'),
    ('{name} 收到监管工作函，要求说明关联交易', -0.02, -0.08, 'company', '监管'),
    ('{name} 通过了突击检查，悬着的心终于放下', 0.01, 0.05, 'company', '监管'),
    ('{name} 环保处罚落地，罚款金额创行业纪录', -0.03, -0.09, 'company', '监管'),
    ('{name} 被列入重点关注名单，机构调研门槛提高', -0.02, -0.07, 'company', '监管'),
    ('{name} 产品抽检不合格，召回公告已挂上官网', -0.03, -0.1, 'company', '监管'),
    ('{name} 宣布收购 {peer}，后者股价应声大涨', 0.02, 0.08, 'company', '并购'),
    ('{name} 拟分拆子公司独立上市，估值先翻倍', 0.03, 0.1, 'company', '并购'),
    ('{name} 重组方案被否，公告里全是叹号', -0.04, -0.1, 'company', '并购'),
    ('{name} 与 {peer} 达成换股合并，市场解读成抱团取暖', 0.01, 0.06, 'company', '并购'),
    ('{name} 悄悄卖掉了海外子公司，没告诉任何人', -0.02, -0.07, 'company', '并购'),
    ('{name} 拿下年度最大订单，产能直接拉满', 0.03, 0.1, 'company', '订单'),
    ('{name} 中标大型基建项目，金额创历史新高', 0.03, 0.09, 'company', '订单'),
    ('{name} 大客户突然跑路，{ind}同行连夜下调预期', -0.03, -0.09, 'company', '订单'),
    ('{name} 拿到海外超级大单，股价直接封涨停', 0.03, 0.1, 'company', '订单'),
    ('{name} 仓库被抢空，客服电话被打爆', 0.01, 0.05, 'company', '订单'),
    ('{name} 老客户回购三千万，评论区一片尖叫', 0.01, 0.05, 'company', '订单'),
    ('{name} 客户集体退货，仓库堆不下', -0.02, -0.07, 'company', '订单'),
    ('{name} 直播带货一夜卖出十个亿', 0.02, 0.08, 'company', '订单'),
    ('{name} 发布新品，{ind}板块集体走强', 0.02, 0.09, 'company', '产品'),
    ('{name} 关键专利获批，研发费用省下一大笔', 0.02, 0.07, 'company', '技术'),
    ('{name} 产线升级完成良品率提升，成本降下来了', 0.01, 0.05, 'company', '技术'),
    ('{name} 新品发布会翻车，全网连夜做表情包', -0.02, -0.07, 'company', '产品'),
    ('{name} 宣布开源核心框架，开发者连夜提交 issue', 0.02, 0.08, 'company', '技术'),
    ('{name} 被曝仍在用老旧设备，产能瓶颈谁来背锅', -0.02, -0.07, 'company', '技术'),
    ('{name} 拿到顶级人才团队，研发投入翻倍', 0.01, 0.06, 'company', '技术'),
    ('{name} 发布一份没人看懂的品说明书，全网集体懵圈', 0.0, 0.05, 'company', '产品'),
    ('{name} 突发停电，产线停摆 6 小时', -0.02, -0.08, 'company', '产品'),
    ('有传言称 {name} 老板在澳门输掉一个厂，公司紧急辟谣', -0.03, -0.1, 'company', '舆情'),
    ('{name} 员工发长文控诉加班文化，老板亲自下场删帖', -0.02, -0.07, 'company', '舆情'),
    ('{name} 官宣代言人，{ind}板块销量预期上调', 0.02, 0.07, 'company', '舆情'),
    ('{name} 售后维权视频上了热搜，客服电话被打爆', -0.02, -0.08, 'company', '舆情'),
    ('{name} 老板说自己不上班，评论区一片问号', -0.01, 0.03, 'company', '舆情'),
    ('{name} 官网挂错价格，第二天悄悄改回来', -0.01, 0.03, 'company', '舆情'),
    ('{name} 入选年度最佳雇主名单，股价纹丝不动', 0.0, 0.03, 'company', '舆情'),
    ('{name} 在 {ind}行业大会上 PPT 翻车，被做成表情包', -0.02, -0.06, 'company', '舆情'),
    ('网友拍到 {name} 员工在办公室养猫，公司称这是企业文化', 0.01, 0.05, 'company', '舆情'),
    ('{name} 官网服务器被挤爆，页面一度打不开', 0.01, 0.04, 'company', '产品'),
    ('{name} 换了 CEO，据说新来的喜欢开会到凌晨', -0.01, 0.04, 'company', '人事'),
    ('{name} 核心高管连夜跑路，董事会紧急开会', -0.03, -0.08, 'company', '人事'),
    ('{name} 挖来行业最年轻的首席科学家，团队连夜发红包', 0.02, 0.06, 'company', '人事'),
    ('{name} 承诺给员工加薪，HR 连夜加班改公告', 0.01, 0.05, 'company', '人事'),
    ('{name} 那个写了 800 行注释的程序员提交了离职信', -0.01, -0.05, 'company', '人事'),
    ('{name} 控股股东质押爆仓风险上升', -0.03, -0.1, 'company', '股东'),
    ('{name} 大股东悄悄减持，公告里只有一行小字', -0.03, -0.09, 'company', '股东'),
    ('{name} 获产业资本举牌，短线资金蠢蠢欲动', 0.03, 0.09, 'company', '股东'),
    ('{name} 定增方案获批，弹药充足准备继续扩张', 0.01, 0.06, 'company', '增发'),
    ('{name} 定增被否，市场开始怀疑故事讲不下去', -0.03, -0.08, 'company', '增发'),
    ('{name} 限售股解禁在即，抛压提前被讨论', -0.02, -0.07, 'company', '解禁'),
    ('{name} 解禁当天高开低走，散户含泪离场', -0.02, -0.06, 'company', '解禁'),
    ('{name} 宣布中期分红，每 10 股派 {num} 元', 0.01, 0.06, 'company', '分红'),
    ('{name} 分红方案低于预期，持股股东骂了一晚上', -0.02, -0.06, 'company', '分红'),
    ('{name} 主力资金连续三日净流出', -0.02, -0.06, 'company', '资金'),
    ('{name} 拿到政府补贴，账上现金多到花不完', 0.02, 0.08, 'company', '资金'),
    ('{name} 股价创新高，机构调研排起长龙', 0.02, 0.06, 'company', '资金'),
    ('{name} 创始人登顶福布斯排行榜，股价应声上涨', 0.02, 0.07, 'company', '舆情'),
    ('{name} 工厂被曝产能过剩，{ind}价格战开打', -0.02, -0.07, 'company', '竞争'),
    ('{name} 竞品发布全新方案，{ind}客户开始动摇', -0.02, -0.06, 'company', '竞争'),
    ('{name} 抢先推出同类产品，{peer} 的客户全被截胡', -0.03, -0.08, 'company', '竞争'),
    ('{ind} 行业迎来政策利好，板块集体飘红', 0.02, 0.06, 'industry', '政策'),
    ('监管收紧 {ind} 新规，板块估值重估', -0.03, -0.07, 'industry', '政策'),
    ('{ind} 板块传出要合并重组的传闻', 0.01, 0.05, 'industry', '并购'),
    ('{ind} 行业被列入实体清单，相关公司连夜改航线', -0.03, -0.07, 'industry', '国际'),
    ('{ind} 获准入华放宽，外资排队提交材料', 0.02, 0.05, 'industry', '国际'),
    ('{ind} 上游突然断供，有人在群里发了张合同截图', -0.02, -0.06, 'industry', '产业链'),
    ('{ind} 龙头带动产业链集体涨价，中小厂喜迎春天', 0.01, 0.045, 'industry', '产业链'),
    ('{ind} 迎来涨价周期，供需紧张', 0.01, 0.05, 'industry', '商品'),
    ('大宗商品价格异动，{ind}成本端承压', -0.01, -0.05, 'industry', '商品'),
    ('{ind} 行业传出降价潮，板块承压', -0.02, -0.06, 'industry', '竞争'),
    ('{ind} 行业传出降价潮，厂商利润被挤成纸', -0.02, -0.06, 'industry', '竞争'),
    ('{ind} 行业核心原材料价格暴涨，利润被上游拿走', -0.015, -0.05, 'industry', '商品'),
    ('{ind} 行业景气度回升，订单开始爆了', 0.01, 0.04, 'industry', '需求'),
    ('{ind} 招聘突然停了，行业寒冬提前到来', -0.02, -0.055, 'industry', '需求'),
    ('{ind} 龙头公司带头扩产，全行业开始抢人抢地', 0.015, 0.045, 'industry', '需求'),
    ('{ind} 出口订单爆单，港口堆箱量创纪录', 0.015, 0.05, 'industry', '出口'),
    ('{ind} 出口订单被砍，海外买家集体换供应商', -0.015, -0.05, 'industry', '出口'),
    ('外资大举流入 {ind} 板块', 0.01, 0.05, 'industry', '资金'),
    ('{ind} 板块获公募基金集体加仓', 0.02, 0.05, 'industry', '资金'),
    ('{ind} 板块被机构集中减持，筹码松动', -0.02, -0.05, 'industry', '资金'),
    ('资金加速流入低估板块，{ind} 成为新宠', 0.005, 0.03, 'industry', '资金'),
    ('{ind} 板块传出龙头要上市的消息，影子股先涨了', 0.01, 0.04, 'industry', '资金'),
    ('{ind} 新技术路线曝光，旧产线被抛在身后', -0.02, -0.06, 'industry', '技术'),
    ('{ind} 技术攻关成功，国产替代进入倒计时', 0.02, 0.05, 'industry', '技术'),
    ('大盘情绪回暖，全市场普涨', 0.01, 0.03, 'market', '情绪'),
    ('大盘放量上涨，三千只个股飘红', 0.005, 0.02, 'market', '情绪'),
    ('成交量创阶段新高，资金入场意愿强', 0.01, 0.02, 'market', '资金'),
    ('监管表态支持资本市场，场外资金蠢蠢欲动', 0.002, 0.015, 'market', '政策'),
    ('两市成交额创阶段新高，赚钱效应回来了', 0.002, 0.012, 'market', '资金'),
    ('北向资金创近月新高扫货，龙头被买爆', 0.005, 0.02, 'market', '资金'),
    ('北向资金连续净流出，聪明钱在跑路', -0.005, -0.02, 'market', '资金'),
    ('两融余额创新高，杠杆资金情绪高涨', 0.005, 0.018, 'market', '资金'),
    ('两融余额快速回落，杠杆资金开始撤退', -0.008, -0.022, 'market', '资金'),
    ('传说中的国家队又出手了，蓝筹先动', 0.008, 0.022, 'market', '资金'),
    ('传说中的国家队悄悄撤离，蓝筹先崩', -0.01, -0.025, 'market', '资金'),
    ('市场传闻监管要收紧杠杆交易', -0.005, -0.02, 'market', '政策'),
    ('外围市场暴跌，A股情绪受挫', -0.01, -0.03, 'market', '国际'),
    ('外围市场小幅回调，A股情绪受牵连', -0.005, -0.015, 'market', '国际'),
    ('全球贸易谈判陷入僵局，市场担心需求萎缩', -0.008, -0.02, 'market', '国际'),
    ('全球贸易达成阶段性协议，风险偏好回升', 0.008, 0.02, 'market', '国际'),
    ('十年期国债收益率上行，估值端压力变大', -0.008, -0.02, 'market', '宏观'),
    ('CPI 数据低于预期，降准预期重回讨论桌', 0.005, 0.02, 'market', '宏观'),
    ('GDP 增速不及预期，市场开始下调全年预期', -0.008, -0.02, 'market', '宏观'),
    ('PMI 重回扩张区间，制造业朋友先笑出来', 0.005, 0.018, 'market', '宏观'),
    ('美元指数站上关键位，人民币汇率承压', -0.005, -0.018, 'market', '汇率'),
    ('大宗商品普涨，通胀预期重新抬头', 0.005, 0.018, 'market', '商品'),
    ('技术面走坏，指数跌破关键支撑位', -0.01, -0.025, 'market', '技术面'),
    ('技术面走强，量价配合良好', 0.008, 0.022, 'market', '技术面'),
    ('量化策略集体回撤，基民开始怀疑人生', -0.008, -0.022, 'market', '技术面'),
    ('成交额萎缩，场子里的人开始睡觉', -0.015, -0.03, 'market', '情绪'),
    ('赚钱效应开始退潮，老法师建议降低仓位', -0.01, -0.025, 'market', '情绪'),
    ('避险情绪升温，黄金概念集体走强', 0.005, 0.015, 'market', '情绪'),
    ('新股首日暴涨，泡沫论又回来了', -0.003, -0.012, 'market', '情绪'),
    ('卖方研报集体上调评级，目标是再涨两成', 0.005, 0.018, 'market', '情绪'),
    ('卖方研报集体下调评级，说的是估值太贵', -0.005, -0.018, 'market', '情绪'),
]

BOMB_TEMPLATES = [
    {
        "kind": 'crash', "scope": 'market', "lo": -0.22, "hi": -0.11,
        "tpl": '中东战事突然升级，油价暴涨，全球资金疯狂涌向避险资产',
        "mood": -0.8, "hours": 6, "label": '地缘冲突', "cat": '国际', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.22, "hi": -0.12,
        "tpl": '某主权国家突然违约，信用市场出现连锁反应',
        "mood": -0.8, "hours": 6, "label": '主权信用危机', "cat": '国际', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.25, "hi": -0.15,
        "tpl": '大型银行挤兑，全球金融市场流动性瞬间冻结',
        "mood": -0.9, "hours": 8, "label": '系统性金融危机', "cat": '宏观', "circuit": 20,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.2, "hi": -0.11,
        "tpl": '头部量化策略集体爆仓，程序化止损引发连锁踩踏',
        "mood": -0.7, "hours": 5, "label": '量化踩踏', "cat": '技术面', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.18, "hi": -0.1,
        "tpl": '多国股市同日触发熔断，全球资产集体跳水',
        "mood": -0.75, "hours": 5, "label": '全球熔断', "cat": '国际', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.18, "hi": -0.09,
        "tpl": '全球金融系统遭大规模网络攻击，多家交易所中断交易',
        "mood": -0.7, "hours": 4, "label": '金融系统故障', "cat": '技术面', "circuit": 20,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.16, "hi": -0.08,
        "tpl": '主要货币汇率崩盘，跨境资金疯狂外逃',
        "mood": -0.7, "hours": 5, "label": '汇率崩盘', "cat": '汇率', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.2, "hi": -0.1,
        "tpl": '未知传染病爆发，多国封锁边境，供应链全面停摆',
        "mood": -0.8, "hours": 6, "label": '公共卫生事件', "cat": '国际', "circuit": 15,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.15, "hi": -0.07,
        "tpl": '贸易战全面升级，关键行业被列入限制清单',
        "mood": -0.6, "hours": 5, "label": '贸易战升级', "cat": '国际', "circuit": 10,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.14, "hi": -0.06,
        "tpl": '主要产油国宣布石油禁运，能源价格彻底失控',
        "mood": -0.55, "hours": 4, "label": '能源断供', "cat": '商品', "circuit": 10,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.14, "hi": -0.07,
        "tpl": '央行意外大幅加息，全市场估值被重新定价',
        "mood": -0.6, "hours": 4, "label": '意外加息', "cat": '政策', "circuit": 10,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.13, "hi": -0.06,
        "tpl": '监管突然出手打击场外配资，杠杆资金被强制平仓',
        "mood": -0.55, "hours": 4, "label": '监管降温', "cat": '政策', "circuit": 10,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.16, "hi": -0.08,
        "tpl": '杠杆泡沫破裂，热门赛道连续跌停引发踩踏',
        "mood": -0.65, "hours": 4, "label": '杠杆泡沫破裂', "cat": '技术面', "circuit": 12,
    },
    {
        "kind": 'crash', "scope": 'market', "lo": -0.12, "hi": -0.05,
        "tpl": '特大台风横扫整个产业带，产能损失惨重',
        "mood": -0.5, "hours": 3, "label": '极端天气', "cat": '国际', "circuit": 8,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.08, "hi": 0.16,
        "tpl": '央行紧急降准降息，流动性洪水涌向股市',
        "mood": 0.7, "hours": 5, "label": '紧急降准降息', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.07, "hi": 0.14,
        "tpl": '国家队再次入场护盘，千亿资金托住指数',
        "mood": 0.65, "hours": 5, "label": '国家队救市', "cat": '资金', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.06, "hi": 0.12,
        "tpl": '巨额养老金宣布入市，长期资金开始建仓',
        "mood": 0.6, "hours": 4, "label": '长期资金入市', "cat": '资金', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.08, "hi": 0.16,
        "tpl": '万亿级刺激措施重磅落地，各路资金疯狂抢筹',
        "mood": 0.75, "hours": 5, "label": '万亿级刺激', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.1, "hi": 0.2,
        "tpl": '重大技术突破震撼全行业，资本市场重新定价',
        "mood": 0.7, "hours": 4, "label": '科技革命', "cat": '技术', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.08, "hi": 0.16,
        "tpl": '重大战略资源被发现，市场疯狂追逐资源概念',
        "mood": 0.65, "hours": 4, "label": '资源大发现', "cat": '商品', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.06, "hi": 0.13,
        "tpl": '全球资金大举涌入中国资产，汇率与股市齐飞',
        "mood": 0.65, "hours": 4, "label": '全球抢筹中国资产', "cat": '国际', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.06, "hi": 0.12,
        "tpl": '资本市场重大制度改革落地，市场长期估值有望抬升',
        "mood": 0.55, "hours": 3, "label": '制度性利好', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.09, "hi": 0.16,
        "tpl": '各大券商连夜上调目标价，研报标题全是最强',
        "mood": 0.65, "hours": 4, "label": '机构上调目标价', "cat": '资金', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.10, "hi": 0.18,
        "tpl": '全球订单集体爆发，产业链公司全线涨停',
        "mood": 0.7, "hours": 4, "label": '全球订单爆发', "cat": '需求', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.07, "hi": 0.14,
        "tpl": '重大民生政策落地，消费板块全线爆发',
        "mood": 0.6, "hours": 4, "label": '民生政策', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.08, "hi": 0.15,
        "tpl": '国际巨头主动上门求合作，谈判现场直接签约',
        "mood": 0.65, "hours": 4, "label": '国际合作', "cat": '国际', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.07, "hi": 0.13,
        "tpl": '市场传闻即将出台重磅利好，传闻先涨为敬',
        "mood": 0.6, "hours": 3, "label": '传闻利好', "cat": '情绪', "circuit": 0,
    },
    {
        "kind": 'boom', "scope": 'market', "lo": 0.06, "hi": 0.12,
        "tpl": '资金面突然转松，杠杆资金终于敢进场了',
        "mood": 0.55, "hours": 3, "label": '资金面转松', "cat": '资金', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.26, "hi": -0.15,
        "tpl": '核心技术被海外同行率先突破，{ind}行业遭到颠覆式冲击',
        "mood": -0.3, "hours": 3, "label": '行业被颠覆', "cat": '技术', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.22, "hi": -0.12,
        "tpl": '{ind}行业龙头集体爆雷，产业链恐慌性抛售',
        "mood": -0.3, "hours": 3, "label": '行业爆雷', "cat": '财报', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.18, "hi": -0.1,
        "tpl": '{ind}行业爆发大规模召回事件，监管连夜发文',
        "mood": -0.25, "hours": 2, "label": '行业召回', "cat": '监管', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.24, "hi": -0.14,
        "tpl": '{ind}行业被列入实体清单，全行业出口瞬间冻结',
        "mood": -0.3, "hours": 3, "label": '行业断供', "cat": '国际', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.16, "hi": -0.08,
        "tpl": '{ind}行业核心原材料价格暴涨，利润全被上游拿走',
        "mood": -0.2, "hours": 3, "label": '成本暴涨', "cat": '商品', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.14, "hi": -0.07,
        "tpl": '{ind}行业被要求限产，中小厂商先扛不住了',
        "mood": -0.2, "hours": 2, "label": '行业限产', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'crash_ind', "scope": 'industry', "lo": -0.15, "hi": -0.08,
        "tpl": '{ind}行业概念退潮，热钱一哄而散',
        "mood": -0.2, "hours": 2, "label": '概念退潮', "cat": '资金', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.12, "hi": 0.26,
        "tpl": '{ind}行业获得国家大订单，产业链订单井喷',
        "mood": 0.3, "hours": 3, "label": '行业订单爆发', "cat": '订单', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.1, "hi": 0.22,
        "tpl": '{ind}行业突然被纳入国家级扶持目录，板块应声暴涨',
        "mood": 0.28, "hours": 3, "label": '行业政策风口', "cat": '政策', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.1, "hi": 0.2,
        "tpl": '{ind}行业关键技术国产化突破，海外厂商份额崩了',
        "mood": 0.25, "hours": 3, "label": '行业技术突破', "cat": '技术', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.09, "hi": 0.18,
        "tpl": '{ind}行业出口订单排到明年，产能直接拉满',
        "mood": 0.25, "hours": 2, "label": '出口爆单', "cat": '出口', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.09, "hi": 0.18,
        "tpl": '{ind}行业需求突然爆发，到处都在抢货',
        "mood": 0.25, "hours": 2, "label": '需求爆发', "cat": '需求', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.10, "hi": 0.20,
        "tpl": '{ind}行业突然被写进教科书，行业地位彻底翻转',
        "mood": 0.25, "hours": 3, "label": '行业地位翻转', "cat": '竞争', "circuit": 0,
    },
    {
        "kind": 'boom_ind', "scope": 'industry', "lo": 0.09, "hi": 0.18,
        "tpl": '{ind}行业龙头集体扩产，整个板块订单排到明年',
        "mood": 0.25, "hours": 2, "label": '龙头扩产', "cat": '需求', "circuit": 0,
    },
    {
        "kind": 'delist', "scope": 'company', "lo": -0.55, "hi": -0.3,
        "tpl": '{name} 财务造假实锤，证监会连夜立案调查',
        "mood": -0.2, "hours": 2, "label": '个股暴雷', "cat": '监管', "circuit": 0,
    },
    {
        "kind": 'delist', "scope": 'company', "lo": -0.45, "hi": -0.25,
        "tpl": '{name} 生产基地突发事故全线停产，订单全部违约',
        "mood": -0.2, "hours": 2, "label": '个股事故', "cat": '产品', "circuit": 0,
    },
    {
        "kind": 'delist', "scope": 'company', "lo": -0.5, "hi": -0.28,
        "tpl": '{name} 实际控制人被带走，公司业务全面停摆',
        "mood": -0.2, "hours": 2, "label": '实控人失联', "cat": '人事', "circuit": 0,
    },
    {
        "kind": 'delist', "scope": 'company', "lo": -0.45, "hi": -0.26,
        "tpl": '{name} 债券到期无法兑付，债权人上门拉横幅',
        "mood": -0.2, "hours": 2, "label": '债券违约', "cat": '财报', "circuit": 0,
    },
    {
        "kind": 'delist', "scope": 'company', "lo": -0.3, "hi": -0.18,
        "tpl": '{name} 用户数据大规模泄露，监管要求限期整改',
        "mood": -0.15, "hours": 2, "label": '数据泄露', "cat": '监管', "circuit": 0,
    },
    {
        "kind": 'buyout', "scope": 'company', "lo": 0.4, "hi": 0.9,
        "tpl": '{name} 被行业巨头全资收购，停牌前股价狂拉',
        "mood": 0.2, "hours": 2, "label": '被收购', "cat": '并购', "circuit": 0,
    },
    {
        "kind": 'buyout', "scope": 'company', "lo": 0.3, "hi": 0.6,
        "tpl": '{name} 拿到海外超级大单，股价直接封涨停',
        "mood": 0.2, "hours": 2, "label": '超级订单', "cat": '订单', "circuit": 0,
    },
    {
        "kind": 'buyout', "scope": 'company', "lo": 0.25, "hi": 0.45,
        "tpl": '{name} 被产业资本举牌，二级市场开始重新定价',
        "mood": 0.2, "hours": 2, "label": '被举牌', "cat": '股东', "circuit": 0,
    },
    {
        "kind": 'buyout', "scope": 'company', "lo": 0.25, "hi": 0.4,
        "tpl": '{name} 拿下行业第一的称号，估值直接翻倍',
        "mood": 0.2, "hours": 2, "label": '登顶行业', "cat": '竞争', "circuit": 0,
    },
    {
        "kind": 'buyout', "scope": 'company', "lo": 0.2, "hi": 0.35,
        "tpl": '{name} 主力资金疯狂抢筹，股价被直接推上天',
        "mood": 0.18, "hours": 2, "label": '资金抢筹', "cat": '资金', "circuit": 0,
    },
]

CAT_MARKS = {'财报': '📊', '监管': '⚖️', '并购': '🤝', '订单': '📦', '产品': '💡', '舆情': '💬', '人事': '👤', '资金': '💰', '技术': '⚙️', '分红': '🎁', '解禁': '🔓', '股东': '🧾', '增发': '🏦', '政策': '🏛️', '商品': '🛢️', '产业链': '🔗', '需求': '📈', '出口': '🚢', '竞争': '⚔️', '宏观': '🌍', '国际': '🌐', '技术面': '📉', '情绪': '🎭', '汇率': '💱'}
CAT_ORDER = ['财报', '监管', '政策', '宏观', '国际', '资金', '技术', '技术面', '商品', '产业链', '需求', '出口', '并购', '订单', '产品', '分红', '解禁', '增发', '股东', '人事', '舆情', '竞争', '情绪', '汇率']
SCOPE_LABELS = {'company': '个股', 'industry': '行业', 'market': '大盘'}
SCOPE_WEIGHTS = {'market': 0.45, 'industry': 0.33, 'company': 0.22}
BOMB_TAG = "\U0001F525"


def bombs_of(scope):
    return [b for b in BOMB_TEMPLATES if b["scope"] == scope]


def pick_bomb(rows, scope=None):
    """挑一条爆炸性新闻模板，并绑定目标公司 / 行业。

    scope=None 时按权重抽：大盘级 45% / 行业级 33% / 个股级 22%。
    用户特别强调「要能动荡整个市场」，所以大盘级权重最高。
    """
    if not rows:
        return None
    if scope is None:
        scope = random.choices(list(SCOPE_WEIGHTS.keys()),
                               weights=list(SCOPE_WEIGHTS.values()))[0]
    pool = bombs_of(scope)
    if not pool:
        return None
    b = dict(random.choice(pool))
    if b["scope"] == "company":
        b["cid"] = random.choice(rows)["id"]
    elif b["scope"] == "industry":
        inds = [r["industry"] for r in rows if r["industry"]]
        if not inds:
            return None
        b["ind"] = random.choice(inds)
    return b


def cat_mark(cat):
    return CAT_MARKS.get(cat or "", "")


def fmt_title(scope, cat, title, bomb=False):
    """快讯标题：【题材】图标 + 原文（爆炸性新闻带 \U0001F525 前缀）。

    题材前缀让快讯一眼能分出「财报 / 监管 / 资金 / 国际」，
    群友翻消息面时不再是一锅粥。
    """
    mark = cat_mark(cat)
    tag = ("【%s】%s " % (cat, mark)) if (cat and mark) else ""
    head = (BOMB_TAG + " ") if bomb else ""
    return head + (tag + title if tag else title)


# ---------------------------------------------------------------- 多空平衡
def _rebalance_normal():
    """普通快讯按 scope 做多空中性。

    手写素材天然偏空：坏消息又多又重，120 条的平均冲击是 -0.39%。
    一天 330 条快讯叠起来就是稳定失血——实测行情几何漂移 -1.1%/天，
    玩家越玩越亏，炒股直接变成骗局。
    这里给每个 scope 统一平移一个常数，把期望冲击拉到 0：
    新闻只负责制造波动和剧情，长期涨跌交给 market_premium 决定。
    """
    for scope in ("company", "industry", "market"):
        rows = [t for t in NEWS_TEMPLATES if t[3] == scope]
        if not rows:
            continue
        mean = sum((t[1] + t[2]) / 2.0 for t in rows) / len(rows)
        if abs(mean) < 1e-9:
            continue
        for i, t in enumerate(NEWS_TEMPLATES):
            if t[3] != scope:
                continue
            NEWS_TEMPLATES[i] = (t[0], round(t[1] - mean, 6), round(t[2] - mean, 6), t[3], t[4])


def _rebalance_bombs():
    """黑天鹅多空平衡（按 scope 分别对齐）。

    手写素材天然偏空，而且偏空的方式不止一处：
      - 冲击区间没对齐：大盘级下跌侧 E[impact]=-0.132，上涨侧只有 +0.112；
      - 情绪周期没对齐：大盘级 Σ(mood×hours) 下跌侧 -49.05、上涨侧 +46.92；
      - 下跌侧还带熔断（休市），上涨侧没有。
    大盘级黑天鹅是全市场冲击，上面任何一条不对称都会变成系统性的空头 drift，
    玩家玩几天就发现「炒股必亏」，打工反而更赚。

    这里按 market / industry / company 三个 scope 分别把上涨侧对齐到下跌侧：
      1) 冲击幅度：上涨侧整体缩放到与下跌侧同强度（两边都会被 ±10% 涨跌停夹住，
         所以真正生效的是「谁被夹住」，对齐后夹住的机会也对称）；
      2) 情绪周期：Σ(mood×hours) 对齐（mood 上限 1.0，用迭代逼近，残差 <1e-6）。
    单条黑天鹅照样惊心动魄（熔断、恐慌指数照旧），但长期多空中性。
    """
    def mean_impact(items):
        return sum((b["lo"] + b["hi"]) / 2.0 for b in items) / len(items)

    for scope in ("market", "industry", "company"):
        rows = [b for b in BOMB_TEMPLATES if b.get("scope") == scope]
        up = [b for b in rows if b.get("mood", 0) > 0]
        dn = [b for b in rows if b.get("mood", 0) < 0]
        if not up or not dn:
            continue
        # ---- 1) 冲击幅度对齐 ----
        up_e, dn_e = mean_impact(up), mean_impact(dn)
        if abs(up_e) > 1e-9 and abs(dn_e) > 1e-9:
            k = abs(dn_e) / abs(up_e)
            if abs(k - 1.0) > 1e-6:
                for b in up:
                    b["lo"] = round(b["lo"] * k, 4)
                    b["hi"] = round(b["hi"] * k, 4)
        # ---- 2) 情绪周期对齐（mood 有 1.0 上限，迭代逼近）----
        for _ in range(60):
            up_mh = sum(b["mood"] * b["hours"] for b in up)
            dn_mh = sum(b["mood"] * b["hours"] for b in dn)
            if up_mh <= 0 or dn_mh >= 0:
                break
            k = (-dn_mh) / up_mh
            if abs(k - 1.0) < 1e-9:
                break
            for b in up:
                b["mood"] = round(min(b["mood"] * k, 1.0), 4)


def _normalize_ranges():
    """把素材里写反的区间摆正（保证 lo <= hi）。

    手写模板时习惯写「跌 -0.114 ~ -0.034」，结果 lo=-0.034、hi=-0.114，
    顺序是反的。random.uniform(a, b) 本身能吃反序（返回 [b, a] 上的均匀分布，
    均值仍是中点），所以线上表现没歪；但任何按 (lo+hi)/2 算中点的工具
    （漂移标定、平衡校验、离线沙盘）都会算错 —— 之前离线沙盘就因此把
    市场漂移误测成 +10%/天，结论全废。这里统一摆正，语义不变。
    """
    for i, t in enumerate(NEWS_TEMPLATES):
        if t[1] > t[2]:
            NEWS_TEMPLATES[i] = (t[0], t[2], t[1], t[3], t[4])
    for b in BOMB_TEMPLATES:
        if b["lo"] > b["hi"]:
            b["lo"], b["hi"] = b["hi"], b["lo"]


_normalize_ranges()
_rebalance_normal()
_rebalance_bombs()
