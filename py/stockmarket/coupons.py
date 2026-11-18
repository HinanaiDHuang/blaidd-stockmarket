"""道具券：每日抽奖发的 buff。

一种券一个用途，存在 coupons 表里（qq + kind 唯一）：
  fee_free   免手续费：value = 还能免几笔（开仓/平仓各算一笔）
  loan_free  免息：expire_at 之前贷款不计息
  job_double 打工双倍：value = 还能双倍几次
"""
import time

FREE_TRADE_LIMIT = 10          # 一次抽奖最多免 10 笔手续费
JOB_DOUBLE_LIMIT = 3           # 一次抽奖最多双倍 3 次打工
LOAN_FREE_DAYS = 1             # 免息券有效天数

KIND_NAMES = {
    "fee_free": "免手续费券",
    "loan_free": "免息券",
    "job_double": "打工双倍券",
}


def _now():
    return int(time.time())


def grant(db, qq, kind, value=1, days=0):
    """发一张券（同种券覆盖旧的，value 取较大值）。"""
    qq = str(qq)
    expire = _now() + int(days * 86400) if days else 0
    row = db.q1("SELECT value, expire_at FROM coupons WHERE qq=? AND kind=?", (qq, kind))
    if row and row["value"] >= value and (not row["expire_at"] or row["expire_at"] >= expire):
        return False
    db.ex("INSERT INTO coupons(qq,kind,value,expire_at,created_at) VALUES(?,?,?,?,?) "
          "ON CONFLICT(qq,kind) DO UPDATE SET value=excluded.value, "
          "expire_at=excluded.expire_at, created_at=excluded.created_at",
          (qq, kind, float(value), expire, _now()))
    return True


def get(db, qq, kind):
    """取一张有效的券，没有返回 None。"""
    row = db.q1("SELECT * FROM coupons WHERE qq=? AND kind=?", (str(qq), kind))
    if not row:
        return None
    if row["expire_at"] and row["expire_at"] < _now():
        db.ex("DELETE FROM coupons WHERE qq=? AND kind=?", (str(qq), kind))
        return None
    if row["value"] <= 0:
        return None
    return row


def use(db, qq, kind, times=1):
    """消耗券。返回 True 表示这次确实用掉了。"""
    row = get(db, qq, kind)
    if not row:
        return False
    left = float(row["value"]) - times
    if left <= 0:
        db.ex("DELETE FROM coupons WHERE qq=? AND kind=?", (str(qq), kind))
    else:
        db.ex("UPDATE coupons SET value=? WHERE qq=? AND kind=?", (left, str(qq), kind))
    return True


def active(db, qq):
    """列出我身上所有有效的券（给页面显示）。"""
    rows = db.q("SELECT * FROM coupons WHERE qq=? AND value>0", (str(qq),))
    out = []
    for r in rows:
        if r["expire_at"] and r["expire_at"] < _now():
            continue
        out.append({
            "kind": r["kind"],
            "name": KIND_NAMES.get(r["kind"], r["kind"]),
            "value": int(r["value"]),
            "expire_at": r["expire_at"],
        })
    return out


def loan_waived(db, qq):
    """贷款是否免息。"""
    return bool(get(db, qq, "loan_free"))


def job_doubled(db, qq):
    """这次打工是否双倍工资。"""
    return use(db, qq, "job_double", 1)
