#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成示例候选池 CSV（消费 ETF 案例）。

用途：演示 `scripts/etf_screen.py` 的输入格式。用 DictWriter 写出，避免手工数逗号导致列错位。
数据来源：用户提供的方法论文档中的示例数值（仅用于演示分析口径），DEMO01 行为纯构造数据。
用法：python3 build_example.py   # 覆盖同目录下的 consumer-etf-candidates.csv
"""
import csv
import os

FIELD_ORDER = [
    "code", "name", "track_index", "selection_rule", "scale_yi", "avg_amount_yi",
    "constituents", "weight_cap_pct", "rebalance", "top10_pct", "top1_pct",
    "sector_top1_name", "sector_top1_pct", "sector_top2_name", "sector_top2_pct",
    "ret_1y_pct", "ret_3y_ann_pct", "ret_5y_ann_pct", "max_dd_pct", "vol_ann_pct",
    "info_ratio", "pe_pctile", "pb_pctile", "earnings_growth_pct", "dividend_yield_pct",
    "fee_pct", "launch_date", "as_of", "source",
]

SRC_DOC = "方法论文档示例数据（仅演示口径）"
SRC_DEMO = "演示用构造数据（仅测试淘汰逻辑）"

ROWS = [
    {
        "name": "消费80指数", "track_index": "消费80指数",
        "selection_rule": "沪市主要消费/可选消费/医药卫生类公司股票",
        "rebalance": "半年度", "top10_pct": 49.96,
        "sector_top1_name": "医药生物", "sector_top1_pct": 32.35,
        "sector_top2_name": "食品饮料", "sector_top2_pct": 31.72,
        "source": SRC_DOC,
    },
    {
        "name": "800消费指数", "track_index": "800消费指数",
        "selection_rule": "仅覆盖中证一级行业中的必选消费公司，采用抽样复制",
        "sector_top1_name": "食品饮料", "sector_top1_pct": 72.49,
        "sector_top2_name": "农林牧渔", "sector_top2_pct": 22.67,
        "source": SRC_DOC,
    },
    {
        "name": "上证消费指数", "track_index": "上证消费指数",
        "selection_rule": "从上证180中选取市值最大的30家消费公司",
        "constituents": 30, "weight_cap_pct": 15, "rebalance": "半年度",
        "top10_pct": 75.78, "top1_pct": 15.7,
        "sector_top1_name": "食品饮料", "sector_top1_pct": 87.45,
        "sector_top2_name": "医药生物", "sector_top2_pct": 4.10,
        "source": SRC_DOC,
    },
    {
        "name": "CS消费50指数", "track_index": "CS消费50指数",
        "selection_rule": "可选消费与主要消费行业中按总市值/营收/ROE/毛利率精选50家",
        "constituents": 50, "rebalance": "半年度", "top1_pct": 15.31,
        "sector_top1_name": "家用电器", "sector_top1_pct": 33.59,
        "sector_top2_name": "食品饮料", "sector_top2_pct": 20.00,
        "source": SRC_DOC,
    },
    {
        "code": "DEMO01", "name": "【示例】小规模消费ETF", "track_index": "上证消费指数",
        "selection_rule": "与上证消费指数相同，但规模不足", "scale_yi": 0.6,
        "avg_amount_yi": 0.03, "source": SRC_DEMO,
    },
]


def main():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "consumer-etf-candidates.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELD_ORDER, restval="")
        w.writeheader()
        for row in ROWS:
            w.writerow(row)
    print("已生成 %s（%d 行）" % (path, len(ROWS)))


if __name__ == "__main__":
    main()
