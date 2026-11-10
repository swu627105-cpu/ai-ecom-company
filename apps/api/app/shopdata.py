"""模拟店铺数据。

外部平台先不接：这里的数字是稳定可复现的样例，用来驱动「分析 → 建议 → 审批 → 执行」的完整链路。
接真实店铺时，把 summary() 换成连接器的查询结果即可，上层不感知。
"""
from __future__ import annotations

from datetime import date, timedelta


def summary() -> dict:
    today = date.today()
    return {
        "date": today.isoformat(),
        "window": f"{(today - timedelta(days=6)).isoformat()} ~ {today.isoformat()}",
        "kpi": {
            "gmv": 12480,
            "orders": 186,
            "aov": 67.1,
            "margin": 27.3,
            "refund_rate": 2.4,
            "refund_rate_prev": 2.1,
            "ad_spend": 5240,
            "ad_roi": 3.2,
            "roi_breakeven": 3.5,
        },
        "skus": [
            {"id": "SKU-101", "name": "日式收纳柜 三层", "price": 429, "sold_7d": 322, "margin": 22.6, "stock": 610, "stock_days": 13, "refund_rate": 3.6},
            {"id": "SKU-102", "name": "折叠收纳箱 大号", "price": 89, "sold_7d": 364, "margin": 34.1, "stock": 1240, "stock_days": 24, "refund_rate": 1.2},
            {"id": "SKU-103", "name": "免安装置物架", "price": 159, "sold_7d": 266, "margin": 31.5, "stock": 320, "stock_days": 8, "refund_rate": 2.0},
            {"id": "SKU-104", "name": "桌面抽屉盒 双层", "price": 69, "sold_7d": 203, "margin": 41.2, "stock": 860, "stock_days": 30, "refund_rate": 0.9},
            {"id": "SKU-105", "name": "阳台晾衣架 落地", "price": 199, "sold_7d": 147, "margin": 18.4, "stock": 90, "stock_days": 4, "refund_rate": 5.1},
        ],
        "refund_sample_note": "退款原因来自历史售后样本 186 单，与近 7 天订单不是同一统计口径。",
        "refunds": [
            {"reason": "物流破损", "count": 76, "share": 41, "note": "集中在 10/12 之后那批，与新换快递、外箱由五层改三层同时发生"},
            {"reason": "安装不会用", "count": 50, "share": 27, "note": "65% 发生在收货 48 小时内，说明书字号偏小"},
            {"reason": "色差", "count": 35, "share": 19, "note": "集中在暖白色系，主图偏冷"},
            {"reason": "其他", "count": 25, "share": 13, "note": ""},
        ],
        "reviews": [
            {"sku": "SKU-101", "stars": 2, "text": "打开就裂了一角，包装太薄了，客服倒是很快给了补发"},
            {"sku": "SKU-105", "stars": 1, "text": "研究了两个小时才装上，孔位对不上，说明书看不懂"},
            {"sku": "SKU-103", "stars": 3, "text": "颜色和图片差挺多，实物偏黄，其他还行"},
        ],
        "ads": [
            {"plan": "收纳-拉新", "spend": 3200, "roi": 3.2, "status": "在投"},
            {"plan": "收纳-复购", "spend": 2040, "roi": 4.1, "status": "在投"},
        ],
    }


def digest(s: dict | None = None) -> str:
    """把数据压成给模型看的一小段文本，控制 token。"""
    s = s or summary()
    k = s["kpi"]
    skus = "\n".join(
        f"- {x['id']} {x['name']}：售价 {x['price']}，7 日销 {x['sold_7d']}，毛利率 {x['margin']}%，"
        f"库存 {x['stock']}（可售 {x['stock_days']} 天），退款率 {x['refund_rate']}%"
        for x in s["skus"]
    )
    refunds = "\n".join(f"- {r['reason']}：{r['count']} 单，占 {r['share']}%。{r['note']}" for r in s["refunds"])
    reviews = "\n".join(f"- {r['sku']} {r['stars']} 星：{r['text']}" for r in s["reviews"])
    ads = "\n".join(f"- {p['plan']}：花费 {p['spend']}，ROI {p['roi']}，{p['status']}" for p in s["ads"])
    return (
        f"统计区间：{s['window']}\n"
        f"整体：GMV {k['gmv']}，订单 {k['orders']}，客单价 {k['aov']}，毛利率 {k['margin']}%，"
        f"退款率 {k['refund_rate']}%（上期 {k['refund_rate_prev']}%），"
        f"广告花费 {k['ad_spend']}，ROI {k['ad_roi']}（保本 {k['roi_breakeven']}）\n\n"
        f"商品：\n{skus}\n\n退款原因（{s['refund_sample_note']}）：\n{refunds}\n\n近期差评：\n{reviews}\n\n投放计划：\n{ads}"
    )


def rule_based(s: dict | None = None) -> dict:
    """模型不可用时的兜底分析：直接按数据算，不编。"""
    s = s or summary()
    k = s["kpi"]
    top_refund = max(s["refunds"], key=lambda r: r["share"])
    best_margin = max(s["skus"], key=lambda x: x["margin"])
    worst_stock = min(s["skus"], key=lambda x: x["stock_days"])
    worst_refund = max(s["skus"], key=lambda x: x["refund_rate"])

    findings = [
        f"退款率 {k['refund_rate']}%，比上期高 {round(k['refund_rate'] - k['refund_rate_prev'], 1)} 个百分点；"
        f"最大来源是{top_refund['reason']}，占 {top_refund['share']}%。",
        f"{worst_stock['name']} 可售只有 {worst_stock['stock_days']} 天，是当前最紧的库存；"
        f"{worst_refund['name']} 退款率 {worst_refund['refund_rate']}%，高于整体。",
        f"投放 ROI {k['ad_roi']} 低于保本线 {k['roi_breakeven']}，"
        f"两个计划里「收纳-复购」{s['ads'][1]['roi']} 明显更好。",
        f"{best_margin['name']} 毛利率 {best_margin['margin']}%，是全店最能扛价格战的品。",
    ]
    actions = [
        {"text": "外箱改回五层、缓冲换珍珠棉，先止损破损", "owner": "sc", "why": f"{top_refund['reason']}占退款 {top_refund['share']}%"},
        {"text": "补货 1,200 件并锁价，避免断货打断投放", "owner": "sc", "why": f"{worst_stock['name']}只剩 {worst_stock['stock_days']} 天"},
        {"text": "把「收纳-拉新」预算挪 30% 到「收纳-复购」", "owner": "ad", "why": "拉新 ROI 3.2 低于保本线，复购 4.1"},
        {"text": "出一条 90 秒安装视频并挂详情页", "owner": "ct", "why": "安装类退款占 27%，65% 发生在收货 48 小时内"},
    ]
    return {
        "source": "rules",
        "model": "",
        "headline": f"退款率升到 {k['refund_rate']}%，主因是{top_refund['reason']}；同时{worst_stock['name']}面临断货。",
        "findings": findings,
        "actions": actions,
        "data": s,
    }
