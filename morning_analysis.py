#!/usr/bin/env python3
"""
早盘竞价+大盘分析自动化 (9:26) · v3
- 交易日 9:26 执行
- 4 卡片：竞价概览 / 题材热度 / 竞价异动 / 早盘研判
- 数据源：新浪行业板块 + 腾讯 qt + akshare 涨停池/连板池
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from uzi_common import (
    _index_quotes, _sector_spot_sina, _a_share_active_stocks,
    _us_sector_etf_quotes, _theme_heat_safe,
    _ths_north_bound, _sector_rank_safe, _hot_rank_safe,
    _mootdx_quotes, A_SHARE_BLUE_CHIPS,
    send_feishu_card, color_chg, fmt_price, is_trading_day, make_logger,
    fetch_latest_skill,
    US_TECH_LEADERS, fetch_qt_quotes,
    _index_tech_analysis, _index_tech_card,
    _market_breadth, _market_breadth_card,
    _em_hot_rank_v2,
)

LOG_FILE = Path("/tmp/uzi_morning_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
log = make_logger(LOG_FILE)

# ── 1. 竞价概览（指数 + 隔夜美股参考 + 技术分析）──
def get_overview():
    indices = _index_quotes()
    # 隔夜美股
    us_indices = fetch_qt_quotes([("NDX", "U"), ("SPX", "U"), ("DJI", "U")])
    us_idx_map = {}
    for q in us_indices:
        us_idx_map[q["code"]] = q
    # 上证技术分析
    tech = _index_tech_analysis()
    # 市场情绪
    breadth = _market_breadth()
    log(f"指数: {len(indices)} 条, 美股指数: {len(us_indices)} 条, 技术分析: {'有' if tech else '无'}, 情绪: {'有' if breadth else '无'}")
    return indices, us_idx_map, tech, breadth

# ── 2. 题材热度（昨日涨停行业 + 连板题材 + 新浪行业）──
def get_theme_heat():
    heat = _theme_heat_safe()
    log(f"题材热度: 涨停{heat['up_count']}/{heat['strong_count']}连板")
    return heat

# ── 3. 竞价异动（活跃股）──
def get_auction_stocks():
    stocks = _a_share_active_stocks()
    if not stocks:
        log("活跃股数据为空，使用 mootdx fallback")
        codes = [c for c, _ in A_SHARE_BLUE_CHIPS]
        quotes = _mootdx_quotes(codes)
        if quotes:
            stocks = []
            for code, name in A_SHARE_BLUE_CHIPS:
                q = quotes.get(code)
                if q and q.get("price"):
                    prev_close = q.get("prev_close")
                    price = q.get("price")
                    change_pct = None
                    if prev_close and prev_close > 0:
                        change_pct = round((price - prev_close) / prev_close * 100, 2)
                    stocks.append({
                        "name": name,
                        "code": code,
                        "price": price,
                        "change_pct": change_pct,
                    })
    valid = [s for s in stocks if s.get("change_pct") is not None]
    up = sorted(valid, key=lambda x: -x["change_pct"])[:8]
    down = sorted(valid, key=lambda x: x["change_pct"])[:8]
    return up, down

# ── 推送卡片 ──
def card_overview(indices, us_idx_map, tech=None, breadth=None):
    lines = ["**📊 竞价·大盘指数**\n"]
    if indices:
        lines.append("| 指数 | 点位 | 涨跌幅 |")
        lines.append("|------|------|--------|")
        for idx in indices:
            lines.append(f"| **{idx['name']}** | {fmt_price(idx.get('price'))} | {color_chg(idx.get('chg_pct'))} |")
    else:
        lines.append("<font color='orange'>⚠️ 指数数据获取失败</font>")

    # 上证技术分析
    tech_card = _index_tech_card(tech)
    if tech_card:
        lines.append(tech_card)

    # 市场情绪
    breadth_card = _market_breadth_card(breadth)
    if breadth_card:
        lines.append(breadth_card)

    # 北向资金流向
    north = _ths_north_bound()
    if north:
        lines.append("\n**🇨🇳 北向资金**")
        lines.append(f"沪股通：{north['sh']:+.2f}亿 | 深股通：{north['sz']:+.2f}亿 | 合计：{north['total']:+.2f}亿")
        if north.get("consecutive", 0) > 0:
            lines.append(f"连续{north['consecutive']}日{north['direction']} | 5日均值：{north['ma5']:+.2f}亿")
    else:
        lines.append("\n**🇨🇳 北向资金**")
        lines.append("北向资金数据暂不可用")

    lines.append("\n**🇺🇸 隔夜美股**")
    lines.append("| 名称 | 代码 | 现价 | 涨跌幅 |")
    lines.append("|------|------|------|--------|")
    for code, name in [("DJI", "道指"), ("SPX", "标普500"), ("NDX", "纳指100")]:
        q = us_idx_map.get(code)
        if q and q.get("change_pct") is not None:
            price_str = fmt_price(q.get('price')) if q.get('price') is not None else "-"
            lines.append(f"| {name} | {code} | {price_str} | {color_chg(q.get('change_pct'))} |")
        else:
            lines.append(f"| {name} | {code} | - | <font color='grey'>--</font> |")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：新浪行业 · 腾讯 qt  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "📊 竞价概览", "\n".join(lines), "blue", log=log)


def card_theme_heat(heat):
    lines = ["**🔥 概念题材热度**\n"]

    # 主数据：同花顺概念题材排名
    concepts = heat.get("concepts")
    if concepts:
        lines.append("**📊 题材热度排名**（同花顺强势股归因）")
        lines.append("| 概念 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for tag, cnt, stocks in concepts[:10]:
            top_names = "、".join(s["name"] for s in stocks[:3])
            lines.append(f"| **{tag}** | {cnt} | {top_names} |")
    else:
        lines.append("<font color='orange'>⚠️ 同花顺热点数据不可用</font>")

    # 连板题材（持续热点）
    strong = heat.get("strong_themes", {})
    if strong:
        top_strong = sorted(strong.items(), key=lambda x: -x[1]["count"])[:4]
        lines.append("\n**🔗 连板题材**（持续热点）")
        lines.append("| 题材 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for ind, info in top_strong:
            top_names = "、".join(s["name"] for s in info["stocks"][:3])
            lines.append(f"| {ind} | {info['count']} | {top_names} |")

    # 涨停行业分布（昨日，辅助参考）
    up_ind = heat.get("up_industries", [])
    if up_ind:
        lines.append("\n**涨停行业分布**（昨日，参考）")
        lines.append("| 行业 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for ind, cnt, stocks in up_ind[:4]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"| {ind} | {cnt} | {top_names} |")

    # 人气榜
    hot_rank = heat.get("hot_rank")
    if hot_rank is None:
        hot_rank = _hot_rank_safe()
    if hot_rank is None:
        # fallback to v2
        hot_rank = _em_hot_rank_v2(5)
    if hot_rank:
        lines.append("\n**🔥 人气榜 TOP5**")
        lines.append("| 排名 | 名称 | 涨跌幅 | 概念 |")
        lines.append("|------|------|--------|------|")
        for s in hot_rank[:5]:
            tags = "、".join(s.get("concepts", [])[:2])
            pop = s.get("pop_tag", "")
            concept_parts = []
            if pop:
                concept_parts.append(pop)
            if tags:
                concept_parts.append(f"[{tags}]")
            concept_str = " · ".join(concept_parts) if concept_parts else "-"
            lines.append(f"| #{s['rank']} | {s['name']} | {s['pct']}% | {concept_str} |")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：同花顺热点 · akshare 涨停池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🔥 概念题材", "\n".join(lines), "orange", log=log)


def card_auction(up, down):
    lines = ["**⚡ 竞价异动**\n"]
    if not up and not down:
        lines.append("<font color='orange'>⚠️ 异动数据获取失败</font>")
    else:
        if up:
            lines.append("**领涨活跃股** 🔴")
            lines.append("| 名称 | 代码 | 现价 | 涨跌幅 |")
            lines.append("|------|------|------|--------|")
            for s in up:
                price_str = fmt_price(s.get('price')) if s.get('price') is not None else "-"
                lines.append(f"| {s['name']} | {s['code']} | {price_str} | {color_chg(s.get('change_pct'))} |")
        if down:
            lines.append("\n**领跌活跃股** 🟢")
            lines.append("| 名称 | 代码 | 现价 | 涨跌幅 |")
            lines.append("|------|------|------|--------|")
            for s in down:
                price_str = fmt_price(s.get('price')) if s.get('price') is not None else "-"
                lines.append(f"| {s['name']} | {s['code']} | {price_str} | {color_chg(s.get('change_pct'))} |")
    lines.append("\n---")
    lines.append("<font color='grey'>45 只活跃股权重股代理  |  数据：腾讯</font>")
    send_feishu_card(FEISHU_WEBHOOK, "⚡ 竞价异动", "\n".join(lines), "green", log=log)


def card_judgment(indices, heat, up, down):
    if indices:
        sh_idx = next((i for i in indices if "上证" in (i.get("name") or "")), None)
        sh_chg = sh_idx.get("chg_pct") if sh_idx else None
        if sh_chg is not None:
            if sh_chg > 0.5:
                sentiment, color = "竞价高开高走，指数强势", "red"
            elif sh_chg > 0:
                sentiment, color = "竞价小幅高开，情绪温和", "red"
            elif sh_chg > -0.5:
                sentiment, color = "竞价小幅低开，关注承接", "orange"
            else:
                sentiment, color = "竞价大幅低开，注意风险", "green"
        else:
            sentiment, color = "数据获取失败", "orange"
    else:
        sentiment, color = "数据获取失败", "orange"

    up_n = len(up)
    down_n = len(down)
    # 热门题材
    concepts = heat.get("concepts")
    hot_themes = "、".join(tag for tag, _, _ in concepts[:4]) if concepts else "暂无"

    body = f"""**🧠 早盘研判**

竞价情绪：<font color='{color}'>{sentiment}</font>

**关键数据：**
• 上涨活跃股：{up_n} 只
• 下跌活跃股：{down_n} 只
• 昨日涨停方向：{hot_themes}

**关注要点：**
• 开盘后 30 分钟量能是否放大
• 昨日涨停题材是否延续（重点看封板率）
• 竞价异动股是否有板块联动
• 隔夜美股对 A 股情绪传导

**题材方向提示：**
• 若涨停行业持续扩散 → 板块效应确认，可跟踪龙头
• 若竞价异动与涨停方向重合 → 资金共识强
• 若竞价大幅低开 → 关注防御性板块（医药/消费/公用事业）

"""
    send_feishu_card(FEISHU_WEBHOOK, "🧠 早盘研判", body, "purple", log=log)


def main():
    log("=" * 60)
    log("早盘竞价+大盘分析启动 v4")
    if not is_trading_day():
        log("今日非交易日，跳过")
        return

    indices, us_idx_map, tech, breadth = get_overview()
    heat = get_theme_heat()
    up, down = get_auction_stocks()

    card_overview(indices, us_idx_map, tech, breadth)
    time.sleep(0.5)
    card_theme_heat(heat)
    time.sleep(0.5)
    card_auction(up, down)
    time.sleep(0.5)
    card_judgment(indices, heat, up, down)
    log("早盘竞价+大盘分析完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)