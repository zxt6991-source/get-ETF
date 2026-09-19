#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""etf_screen.py —— 行业/主题 ETF 五维打分卡（纯标准库，离线可用）

字段口径见 references/scoring-card.md，方法论见 references/framework.md。

用法:
  python3 etf_screen.py --template > candidates.csv   # 输出输入模板
  python3 etf_screen.py candidates.csv                # 默认 markdown 报告
  python3 etf_screen.py candidates.csv --format json --out result.json
  python3 etf_screen.py candidates.csv --format csv

设计原则:
  1. 缺失字段一律视为"未知"，不会当成 0；
  2. 缺失维度按剩余维度权重重归一化，并输出"数据覆盖率"；
  3. 规模 < 1 亿元的候选按规则淘汰，单独列出而非混在排名里；
  4. 分数只用于同赛道横向排序，脚本不产出唯一答案，只产出排序 + 标签 + 预警。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys

UNKNOWN = {"", "-", "--", "na", "n/a", "null", "none", "nan", "待补", "未知", "?", "/", "无"}

WEIGHTS = {
    "liquidity": 0.10,      # 可交易性（第1步）
    "design": 0.15,         # 编制质量（第2步）
    "concentration": 0.20,  # 集中度（第3步）
    "performance": 0.30,    # 历史表现（第4步）
    "valuation": 0.25,      # 价值（第5步）
}
DIM_LABEL = {
    "liquidity": "可交易性",
    "design": "编制质量",
    "concentration": "集中度",
    "performance": "历史表现",
    "valuation": "价值",
}

FIELDS = [
    "code", "name", "track_index", "selection_rule", "scale_yi", "avg_amount_yi", "constituents",
    "weight_cap_pct", "rebalance", "top10_pct", "top1_pct", "sector_top1_name",
    "sector_top1_pct", "sector_top2_name", "sector_top2_pct", "ret_1y_pct",
    "ret_3y_ann_pct", "ret_5y_ann_pct", "max_dd_pct", "vol_ann_pct", "info_ratio",
    "pe_pctile", "pb_pctile", "earnings_growth_pct", "dividend_yield_pct",
    "fee_pct", "launch_date", "as_of", "source",
]


# ---------------------------------------------------------------- 解析辅助
def num(row, key):
    raw = (row.get(key) or "").strip()
    if raw.lower() in UNKNOWN:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", "").replace("％", "%"))
    return float(m.group()) if m else None


def txt(row, key):
    raw = (row.get(key) or "").strip()
    if raw.lower() in UNKNOWN:
        return None
    return re.split(r"[（(]", raw)[0].strip()


def higher_better(v, points):
    """points: [(阈值, 分数)] 按阈值升序，分数随阈值升高。低于最低阈值取最低分。"""
    if v is None:
        return None
    pts = sorted(points)
    score = pts[0][1]
    for t, s in pts:
        if v >= t:
            score = s
    return score


def lower_better(v, points):
    """points: [(阈值, 分数)] 按阈值升序，分数随阈值下降。高于最高阈值取最低分。"""
    if v is None:
        return None
    pts = sorted(points)
    for t, s in pts:
        if v <= t:
            return s
    return pts[-1][1]


def wavg(pairs):
    """pairs: [(value_or_None, weight)] —— 只对已知值归一化。"""
    total_w = sum(w for v, w in pairs if v is not None)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in pairs if v is not None) / total_w


def plain_avg(vals):
    known = [v for v in vals if v is not None]
    return sum(known) / len(known) if known else None


# ---------------------------------------------------------------- 五个维度
def dim_liquidity(row, flags):
    scale = num(row, "scale_yi")
    amount = num(row, "avg_amount_yi")
    if scale is not None and scale < 1:
        s_scale = 0.0
        flags.append("规模 %.2f 亿元 < 1 亿元 —— 按筛选规则淘汰" % scale)
    else:
        s_scale = higher_better(scale, [(1, 40), (2, 55), (5, 70), (20, 85), (50, 100)])
        if scale is not None and scale < 5:
            flags.append("规模偏小（%.2f 亿元）—— 存在清盘与流动性风险" % scale)
    s_amount = higher_better(amount, [(0.05, 20), (0.2, 40), (0.5, 60), (2, 75), (5, 90), (10, 100)])
    if amount is not None and amount < 0.2:
        flags.append("近20日均成交额 %.2f 亿元偏低 —— 买卖冲击成本高" % amount)
    return wavg([(s_scale, 0.6), (s_amount, 0.4)])


def dim_design(row, flags):
    cons = num(row, "constituents")
    cap = num(row, "weight_cap_pct")
    reb = txt(row, "rebalance")
    s_cons = higher_better(cons, [(0, 35), (20, 45), (30, 60), (50, 75), (100, 90), (200, 100)])
    s_cap = lower_better(cap, [(5, 100), (10, 85), (15, 65), (20, 50), (1000, 30)])
    s_reb = None
    if reb:
        if "月" in reb or "季" in reb:
            s_reb = 100.0
        elif "半年" in reb or "半年度" in reb:
            s_reb = 85.0
        elif "年" in reb:
            s_reb = 60.0
        else:
            s_reb = 50.0
    if cons is not None and cons < 30:
        flags.append("成分股仅 %d 只 —— 个股风险敞口大" % int(cons))
    if cap is not None and cap > 15:
        flags.append("单股权重上限 %.0f%% 偏高 —— 龙头股影响力大" % cap)
    return plain_avg([s_cons, s_cap, s_reb])


def dim_concentration(row, flags):
    top10 = num(row, "top10_pct")
    sector1 = num(row, "sector_top1_pct")
    s_top10 = lower_better(top10, [(25, 100), (35, 85), (45, 70), (55, 55), (70, 40), (80, 30), (100, 15)])
    s_sector = lower_better(sector1, [(30, 100), (40, 85), (50, 70), (60, 55), (75, 40), (999, 25)])
    if top10 is not None and top10 > 75:
        flags.append("前十大权重 %.2f%% —— 指数被少数龙头主导" % top10)
    if sector1 is not None and sector1 > 75:
        name = txt(row, "sector_top1_name") or "单一细分行业"
        flags.append("第一大行业（%s）权重 %.2f%% —— 近似单一板块押注" % (name, sector1))
    return wavg([(s_top10, 0.6), (s_sector, 0.4)])


def dim_performance(row, flags):
    r1 = num(row, "ret_1y_pct")
    r3 = num(row, "ret_3y_ann_pct")
    r5 = num(row, "ret_5y_ann_pct")
    dd = num(row, "max_dd_pct")
    vol = num(row, "vol_ann_pct")
    ir = num(row, "info_ratio")
    s_ret = plain_avg([
        higher_better(r1, [(-20, 10), (0, 30), (10, 50), (20, 70), (35, 90), (60, 100)]),
        higher_better(r3, [(-10, 10), (0, 30), (8, 50), (15, 70), (25, 90), (40, 100)]),
        higher_better(r5, [(-10, 10), (0, 30), (8, 50), (15, 70), (25, 90), (40, 100)]),
    ])
    s_risk = plain_avg([
        lower_better(dd, [(20, 100), (30, 80), (40, 60), (50, 40), (70, 20), (200, 5)]),
        lower_better(vol, [(15, 100), (20, 85), (25, 70), (30, 55), (40, 40), (200, 20)]),
    ])
    s_ir = higher_better(ir, [(0, 20), (0.15, 35), (0.3, 50), (0.5, 70), (0.7, 85), (1.0, 100)])
    if dd is not None and dd > 50:
        flags.append("历史最大回撤 %.1f%% —— 极端行情抗跌能力弱" % dd)
    if vol is not None and vol > 30:
        flags.append("年化波动率 %.1f%% —— 高弹性伴随高波动" % vol)
    if ir is not None and ir <= 0.3:
        flags.append("信息比率 %.2f 偏低 —— 承担主动风险未获有效超额" % ir)
    return wavg([(s_ret, 0.40), (s_risk, 0.35), (s_ir, 0.25)])


def dim_valuation(row, flags):
    pe = num(row, "pe_pctile")
    pb = num(row, "pb_pctile")
    growth = num(row, "earnings_growth_pct")
    dy = num(row, "dividend_yield_pct")
    pct_pts = [(20, 100), (40, 85), (60, 65), (80, 45), (100, 25)]
    s_val = plain_avg([lower_better(pe, pct_pts), lower_better(pb, pct_pts)])
    s_growth = higher_better(growth, [(0, 25), (5, 40), (10, 55), (20, 75), (30, 90), (50, 100)])
    s_dy = higher_better(dy, [(1, 45), (2, 60), (3, 70), (4, 85), (5, 100)])
    if pe is not None and pe > 80:
        flags.append("PE 分位 %.0f%% —— 估值处于历史高位" % pe)
    if pb is not None and pb > 80:
        flags.append("PB 分位 %.0f%% —— 估值处于历史高位" % pb)
    return wavg([(s_val, 0.40), (s_growth, 0.35), (s_dy, 0.25)])


DIMS = {
    "liquidity": dim_liquidity,
    "design": dim_design,
    "concentration": dim_concentration,
    "performance": dim_performance,
    "valuation": dim_valuation,
}


# ---------------------------------------------------------------- 画像标签
def profile_tags(row):
    tags = []
    top10 = num(row, "top10_pct")
    sector1 = num(row, "sector_top1_pct")
    top1 = num(row, "top1_pct")
    scale = num(row, "scale_yi")
    amount = num(row, "avg_amount_yi")
    pe = num(row, "pe_pctile")
    pb = num(row, "pb_pctile")
    growth = num(row, "earnings_growth_pct")
    dy = num(row, "dividend_yield_pct")
    vol = num(row, "vol_ann_pct")

    if top10 is not None or sector1 is not None:
        concentrated = (top10 is not None and top10 >= 60) or (sector1 is not None and sector1 >= 60)
        balanced = (top10 is not None and top10 <= 45) and (sector1 is not None and sector1 <= 45)
        if balanced:
            tags.append("分散均衡")
        elif concentrated:
            tags.append("集中高弹性")
    if top1 is not None and top1 >= 15:
        tags.append("龙头主导")
    if pe is not None and growth is not None and pe <= 30 and growth >= 15:
        tags.append("低估值成长")
    if dy is not None and (dy >= 4 or (dy >= 3 and vol is not None and vol <= 20)):
        tags.append("高股息防御")
    if (scale is not None and scale < 5) or (amount is not None and amount < 0.2):
        tags.append("流动性/清盘风险")
    if (pe is not None and pe > 80) or (pb is not None and pb > 80):
        tags.append("估值偏高")
    return tags


# ---------------------------------------------------------------- 打分主逻辑
def score_row(row):
    flags = []
    dims = {k: fn(row, flags) for k, fn in DIMS.items()}
    known = {k: v for k, v in dims.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in known)
    if not known or total_w <= 0:
        return {
            "dims": dims, "total": None, "coverage": 0.0, "flags": flags,
            "tags": profile_tags(row), "excluded": True,
            "exclude_reason": "无可用评分字段",
        }
    weighted = sum(known[k] * WEIGHTS[k] for k in known) / total_w
    coverage = sum(WEIGHTS[k] for k in known)  # 已覆盖的权重占比
    scale = num(row, "scale_yi")
    excluded = scale is not None and scale < 1
    return {
        "dims": dims,
        "total": round(weighted, 1),
        "coverage": round(coverage, 3),
        "flags": flags,
        "tags": profile_tags(row),
        "excluded": excluded,
        "exclude_reason": "规模 < 1 亿元" if excluded else "",
    }


def analyse(rows):
    results = []
    for row in rows:
        s = score_row(row)
        s["row"] = row
        results.append(s)
    ranked = sorted([r for r in results if not r["excluded"] and r["total"] is not None],
                    key=lambda r: r["total"], reverse=True)
    dropped = [r for r in results if r not in ranked]
    def is_missing(r, f):
        return (r["row"].get(f) or "").strip().lower() in UNKNOWN

    def all_missing(f):
        return all(is_missing(r, f) for r in ranked + dropped)

    missing_all = sorted(f for f in FIELDS if all_missing(f))
    missing_partial = sorted(f for f in FIELDS if any(is_missing(r, f) for r in ranked + dropped) and not all_missing(f))
    return ranked, dropped, missing_all, missing_partial


# ---------------------------------------------------------------- 输出
def fmt(v, unit="", nd=1):
    if v is None:
        return "待补"
    if nd == 0:
        return "%d%s" % (int(round(v)), unit)
    return "%s%s" % (round(v, nd), unit)


def to_markdown(ranked, dropped, missing_all, missing_partial, as_of=""):
    out = []
    out.append("# ETF 候选池打分结果")
    out.append("")
    out.append("> 数据截止：%s ｜ 参评 %d 只 ｜ 淘汰 %d 只" % (as_of or "待补", len(ranked), len(dropped)))
    out.append("> 分数仅用于**同赛道横向排序**，不跨赛道比较，不构成投资建议。")
    out.append("")
    if not ranked:
        out.append("没有可排名的候选（全部缺失关键字段或被淘汰）。")
        return "\n".join(out)

    max_cov = max(r["coverage"] for r in ranked)
    if max_cov < 0.6:
        out.append("> ⚠ 最高数据覆盖率仅 %.0f%%（<60%%）：本表只能反映**已知维度**的相对形态，" % (max_cov * 100))
        out.append("> 补齐规模/成交额、历史表现、估值字段后再据此决策。")
        out.append("")

    out.append("## 一、排名")
    out.append("")
    header = ["排名", "名称", "跟踪指数", "综合分", "数据覆盖率"] + [DIM_LABEL[k] for k in WEIGHTS] + ["画像标签"]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for i, r in enumerate(ranked, 1):
        dim_cells = [fmt(r["dims"][k], nd=0) for k in WEIGHTS]
        row = [
            str(i),
            r["row"].get("name", "") or "待补",
            r["row"].get("track_index", "") or "-",
            "**%.1f**" % r["total"],
            "%.0f%%" % (r["coverage"] * 100),
        ] + dim_cells + ["、".join(r["tags"]) or "-"]
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    out.append("## 二、分项明细")
    out.append("")
    for r in ranked:
        row = r["row"]
        out.append("### %s（%s）" % (row.get("name") or "未命名", row.get("code") or "无代码"))
        out.append("")
        out.append("- 跟踪指数：%s" % (row.get("track_index") or "待补"))
        if txt(row, "selection_rule"):
            out.append("- 选样规则：%s" % txt(row, "selection_rule"))
        out.append("- 规模 / 成交额：%s 亿元 / %s 亿元" % (fmt(num(row, "scale_yi"), nd=2), fmt(num(row, "avg_amount_yi"), nd=2)))
        out.append("- 编制：成分股 %s 只｜单股权重上限 %s｜调仓 %s" % (
            fmt(num(row, "constituents"), nd=0),
            fmt(num(row, "weight_cap_pct"), "%"),
            row.get("rebalance") or "待补"))
        out.append("- 集中度：前十大 %s｜第一大行业 %s（%s）" % (
            fmt(num(row, "top10_pct"), "%"),
            fmt(num(row, "sector_top1_pct"), "%"),
            row.get("sector_top1_name") or "待补"))
        out.append("- 表现：近1年 %s｜近3年年化 %s｜最大回撤 %s｜波动率 %s｜信息比率 %s" % (
            fmt(num(row, "ret_1y_pct"), "%"), fmt(num(row, "ret_3y_ann_pct"), "%"),
            fmt(num(row, "max_dd_pct"), "%"), fmt(num(row, "vol_ann_pct"), "%"),
            fmt(num(row, "info_ratio"), nd=2)))
        out.append("- 价值：PE分位 %s｜PB分位 %s｜盈利增速 %s｜股息率 %s" % (
            fmt(num(row, "pe_pctile"), "%"), fmt(num(row, "pb_pctile"), "%"),
            fmt(num(row, "earnings_growth_pct"), "%"), fmt(num(row, "dividend_yield_pct"), "%")))
        if r["tags"]:
            out.append("- 画像：%s" % "、".join(r["tags"]))
        if r["flags"]:
            out.append("- **预警**：%s" % "；".join(r["flags"]))
        out.append("")

    if dropped:
        out.append("## 三、已淘汰 / 无法评分")
        out.append("")
        out.append("| 名称 | 跟踪指数 | 原因 |")
        out.append("| --- | --- | --- |")
        for r in dropped:
            out.append("| %s | %s | %s |" % (
                r["row"].get("name") or "未命名",
                r["row"].get("track_index") or "-",
                r["exclude_reason"] or "无可用评分字段"))
        out.append("")

    out.append("## 四、数据完整性与提示")
    out.append("")
    if missing_all:
        out.append("- 全部候选都缺失的字段：%s" % "、".join(missing_all))
    if missing_partial:
        out.append("- 部分候选缺失的字段：%s（对应维度按剩余权重重归一化）" % "、".join(missing_partial))
    if not missing_all and not missing_partial:
        out.append("- 候选池字段完整。")
    out.append("- 数据覆盖率低于 60% 时，排序只反映已知维度的相对形态，请先补齐数据再决策。")
    out.append("- 提醒：估值分位受成分股调整影响会失真；盈利预测可能存在样本偏差；规模与成交额需注明时点。")
    out.append("- 本结果为公开数据整理与分析，不构成投资建议。")
    return "\n".join(out)


def to_csv(ranked, dropped):
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "code", "name", "track_index", "total", "coverage"] + list(WEIGHTS) + ["tags", "flags"])
    for i, r in enumerate(ranked, 1):
        w.writerow([i, r["row"].get("code", ""), r["row"].get("name", ""), r["row"].get("track_index", ""),
                    r["total"], r["coverage"]] +
                   [r["dims"][k] for k in WEIGHTS] +
                   ["|".join(r["tags"]), "|".join(r["flags"])])
    for r in dropped:
        w.writerow(["excluded", r["row"].get("code", ""), r["row"].get("name", ""),
                    r["row"].get("track_index", ""), "", "", "", "", "", "", "",
                    "|".join(r["tags"]), r["exclude_reason"]])
    return buf.getvalue()


def to_json(ranked, dropped, missing_all, missing_partial):
    def pack(r):
        return {
            "name": r["row"].get("name"), "code": r["row"].get("code"),
            "track_index": r["row"].get("track_index"),
            "total": r["total"], "coverage": r["coverage"],
            "dims": r["dims"], "tags": r["tags"], "flags": r["flags"],
            "excluded": r["excluded"], "exclude_reason": r["exclude_reason"],
        }
    return json.dumps({
        "weights": WEIGHTS,
        "missing_fields_all": missing_all,
        "missing_fields_partial": missing_partial,
        "ranked": [pack(r) for r in ranked],
        "dropped": [pack(r) for r in dropped],
        "disclaimer": "公开数据整理与分析，不构成投资建议。",
    }, ensure_ascii=False, indent=2)


TEMPLATE_ROWS = [
    # 仅演示字段格式与单位，代码/名称为占位符，请替换为真实数据
    ["000000", "示例ETF", "示例指数", "母指数内按市值与成交额选样，未抽样复制", "12.5", "0.85", "30", "15", "半年度",
     "75.78", "15.7", "食品饮料", "87.45", "医药生物", "4.1", "28.4", "12.6", "9.8",
     "42.6", "26.3", "0.62", "35", "40", "18", "3.1", "0.60", "2013-02-06", "2026-06-30", "基金季报/指数公司官网"],
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="行业/主题 ETF 五维打分卡")
    ap.add_argument("csv_path", nargs="?", help="候选池 CSV 路径")
    ap.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    ap.add_argument("--out", help="输出文件路径；缺省打印到标准输出")
    ap.add_argument("--template", action="store_true", help="打印 CSV 模板并退出")
    ap.add_argument("--as-of", default="", help="报告中的数据截止日")
    args = ap.parse_args(argv)

    if args.template:
        w = csv.writer(sys.stdout)
        w.writerow(FIELDS)
        w.writerow(TEMPLATE_ROWS[0])
        return 0

    if not args.csv_path:
        ap.error("需要提供 CSV 路径（或使用 --template 生成模板）")

    with open(args.csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]

    if not rows:
        print("CSV 中没有有效数据行。", file=sys.stderr)
        return 1

    ranked, dropped, missing_all, missing_partial = analyse(rows)
    as_of = args.as_of or next(
        (r["row"].get("as_of", "").strip() for r in ranked + dropped
         if re.search(r"\d{4}[-/年]\d{1,2}", r["row"].get("as_of", "") or "")), "")
    if args.format == "markdown":
        text = to_markdown(ranked, dropped, missing_all, missing_partial, as_of)
    elif args.format == "json":
        text = to_json(ranked, dropped, missing_all, missing_partial)
    else:
        text = to_csv(ranked, dropped)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        print("已写入 %s（%d 行参评 / %d 行淘汰）" % (args.out, len(ranked), len(dropped)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
