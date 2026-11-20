# -*- coding: utf-8 -*-
"""净值分层（v4.7）—— 给「有钱」这件事装一个资金出口。

【为什么要做】
复利在游戏里也是成立的：收益率是百分比，而「能撬动的仓位」=
净值 × 杠杆上限。现网主群 216 人：Kaito 净值 334 万，
能压 1000 万保证金；中位数玩家 9.9 万，只能压 30 万。
百分比收益乘上三倍多的基数，差距是**指数**拉开的，不是线性拉开的。
只靠打工 / 签到这种线性收入，永远追不上复利，越玩越绝望。

所以 v4.7 给净值分层：
  * 高净值玩家每天自动缴一笔「会员费」（进全民分红池）——
    这是用户要的「让富人去消费」，而且是系统自动收，不靠自觉；
  * 换来的是特权：杠杆上限更高、手续费打折、打工工资更高、
    被打土豪的成功率更低（声望保护）。
「有钱」从一个纯数字变成一种**要持续付费维持的身份**。

【分层线为什么定在 20 万】
现网：中位数 9.9 万，139/216（64.4%）低于 10 万，最高 334 万。
20 万卡在头部 10~15%：绝大多数人留在「不用交钱」的舒适区，
真正跑出来的赢家开始出血 —— 出血量还不至于把人劝退。

【守恒】
会员费是**转移**不是增发：富人的现金 → 全民分红池 → 群友分红。
全群总现金守恒。唯一会被销毁的是「挥霍」消费里明确标了销毁的那部分。
"""

# 顺序即档位顺序（min 必须递增）
TIERS = [
    {"key": "retail", "name": "普通股民", "min": 0,
     "fee": 0.0000, "rob": 0.22,
     "lev": 0, "fdisc": 0.00, "wage": 0.00, "take": 0.00},
    {"key": "small", "name": "小富之家", "min": 200000,
     "fee": 0.0004, "rob": 0.32,
     "lev": 2, "fdisc": 0.05, "wage": 0.10, "take": 0.10},
    {"key": "big", "name": "大富之家", "min": 500000,
     "fee": 0.0007, "rob": 0.42,
     "lev": 4, "fdisc": 0.10, "wage": 0.20, "take": 0.20},
    {"key": "lux", "name": "豪门", "min": 1500000,
     "fee": 0.0010, "rob": 0.52,
     "lev": 6, "fdisc": 0.15, "wage": 0.30, "take": 0.30},
    {"key": "mega", "name": "巨富", "min": 5000000,
     "fee": 0.0020, "rob": 0.62,
     "lev": 8, "fdisc": 0.20, "wage": 0.40, "take": 0.40},
    {"key": "tycoon", "name": "财阀", "min": 15000000,
     "fee": 0.0026, "rob": 0.72,
     "lev": 10, "fdisc": 0.25, "wage": 0.50, "take": 0.50},
]

def tier_index(equity):
    """按净值落在哪一档（返回下标）。"""
    eq = float(equity or 0)
    idx = 0
    for i, t in enumerate(TIERS):
        if eq >= t["min"]:
            idx = i
        else:
            break
    return idx


def tier_of(equity):
    """按净值取档位字典。"""
    return TIERS[tier_index(equity)]


def tier_name(equity):
    return TIERS[tier_index(equity)]["name"]


def fee_ratio(equity):
    """每日会员费费率（净值比例）。"""
    return float(tier_of(equity)["fee"])


def fee_cap(cfg, equity):
    """会员费封顶：绝对底值 + 净值比例。

    【为什么要改（v4.13）】原来 tycoon_fee_cap 是**绝对值** 30000：
    净值 534 亿的巨富按 0.26% 该缴 1390 万，封顶 3 万 —— 等于免税。
    这是现有机制最大的漏洞：越有钱，交的税越接近于零。
    现在封顶跟着净值走（tycoon_fee_cap_ratio），巨富该交多少交多少。

    中小玩家完全不受影响：净值低于 1150 万时
    max(30000, 净值×0.26%) 还是 30000，而他们按费率交的钱本来就远低于 30000
    （1500 万财阀档 = 3.9 万，才刚过 3 万这条线）。
    """
    cfg = cfg or {}
    base = float(cfg.get("tycoon_fee_cap", 30000))
    ratio = float(cfg.get("tycoon_fee_cap_ratio", 0) or 0)
    if ratio <= 0:
        return base
    return max(base, max(0.0, float(equity or 0)) * ratio)


def lev_bonus(equity):
    """特权：杠杆上限加成。"""
    return int(tier_of(equity)["lev"])


def fee_discount(equity):
    """特权：交易手续费折扣（0.05 = 打九五折）。"""
    return float(tier_of(equity)["fdisc"])


def wage_multi(equity):
    """特权：打工工资加成（0.10 = 多拿 10%）。"""
    return float(tier_of(equity)["wage"])


def rob_rate(equity):
    """档位决定「被打」的基准成功率：越富越招人惦记。"""
    return float(tier_of(equity)["rob"])


def next_tier(equity):
    """下一档（已经是最顶就返回 None）。"""
    i = tier_index(equity)
    if i >= len(TIERS) - 1:
        return None
    return TIERS[i + 1]


def tier_lines(cfg=None):
    """档位说明文本（给群友看的规则表）。"""
    cfg = cfg or {}
    cap = float(cfg.get("tycoon_fee_cap", 30000))
    cap_ratio = float(cfg.get("tycoon_fee_cap_ratio", 0) or 0)
    if cap_ratio > 0:
        cap_txt = "max(%.1f 万, 净值×%.2f%%)" % (cap / 10000, cap_ratio * 100)
    else:
        cap_txt = "%.1f 万" % (cap / 10000)
    lines = ["👑 净值分层（会员费每天自动收，进全民分红池）"]
    for t in TIERS:
        if t["fee"] <= 0:
            lines.append("· %s：不用交钱" % t["name"])
            continue
        lines.append("· %s：净值≥%s 万，每日会员费 %.2f%%（封顶 %s）"
                     "｜杠杆上限 +%d、手续费 -%.0f%%、打工 +%.0f%%" % (
                         t["name"], int(t["min"] / 10000), t["fee"] * 100,
                         cap_txt, t["lev"], t["fdisc"] * 100, t["wage"] * 100))
    lines.append("会员费不是罚款，是你留在牌桌上的门票费 —— 不交钱就没有特权")
    return "\n".join(lines)
