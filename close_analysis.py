#!/usr/bin/env python3
"""
收盘复盘+尾盘抢筹自动化 (15:01) · v3
- 交易日 15:01 执行
- 4 卡片：收盘概览 / 题材资金 / 热门标的 / 明日策略
- 数据源：新浪行业板块 + akshare 涨停池/连板池 + 腾讯 qt
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from uzi_common import (
    _index_quotes, _sector_spot_sina, _theme_heat_safe,
    _fetch_limit_pool_safe, _fetch_dt_pool_safe, _fetch_strong_pool_em,
    limit_up_sentiment, em_zt_pool, em_zb_pool, em_dt_pool, em_yzt_pool,
    _ths_limit_up_pool, _fmt_zt_time,
    fetch_latest_skill,
    _fmt_seal, _get_concept_tags, build_ladder_card, _ths_north_bound, _em_lhb_daily,
    _a_share_active_stocks,
    send_feishu_card, color_chg, fmt_price, fmt_amount_yi, is_trading_day, make_logger,
    _index_tech_analysis, _index_tech_card,
    _market_breadth, _market_breadth_card,
    _limit_up_sentiment_v2, _em_hot_rank_v2,
    _us_a_linkage, _us_a_linkage_card,
)

LOG_FILE = Path("/tmp/uzi_close_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000
log = make_logger(LOG_FILE)

# 共享 send_card 本地包装
from uzi_common import send_card as _send_card
send_card = lambda title, content, template="blue": _send_card(FEISHU_WEBHOOK, title, content, template, MAX_BYTES, log_func=log)

# ── 1. 收盘概览 ──
def get_overview():
    indices = _index_quotes()
    tech = _index_tech_analysis()
    breadth = _market_breadth()
    log(f"指数: {len(indices)} 条, 技术分析: {'有' if tech else '无'}, 情绪: {'有' if breadth else '无'}")
    return indices, tech, breadth

# ── 2. 题材资金 ──
def get_theme_heat():
    heat = _theme_heat_safe()
    log(f"题材: 涨停{heat['up_count']} 跌停{heat['down_count']} 连板{heat['strong_count']}")
    return heat

# ── 3. 热门标的 ──
def get_hot_stocks():
    up, up_src = _fetch_limit_pool_safe()
    down, down_src = _fetch_dt_pool_safe()
    strong = _fetch_strong_pool_em()
    return up, up_src, down, down_src, strong

# ── 4. 尾盘抢筹 ──
def get_late_stocks():
    stocks = _a_share_active_stocks()
    valid = [s for s in stocks if s.get("change_pct") is not None and 5.0 <= s["change_pct"] < 9.5]
    return sorted(valid, key=lambda x: -x["change_pct"])[:8]

# ── 推送卡片 ──
def card_overview(indices, tech=None, breadth=None):
    lines = ["**📊 收盘·大盘指数**\n"]
    if indices:
        lines.append("| 指数 | 点位 | 涨跌幅 |")
        lines.append("|------|------|--------|")
        for idx in indices:
            price = fmt_price(idx.get('price'))
            chg = idx.get('chg_pct')
            chg_str = color_chg(chg) if chg is not None else "-"
            lines.append(f"| **{idx['name']}** | {price} | {chg_str} |")
    else:
        lines.append("<font color='orange'>⚠️ 数据获取失败</font>")

    # 涨跌概况
    if indices:
        up_count = sum(1 for i in indices if (i.get("chg_pct") or 0) > 0)
        total = len(indices)
        lines.append(f"\n<font color='grey'>指数上涨 {up_count}/{total}，市场情绪偏{'强' if up_count >= total/2 else '弱'}</font>")

    # 上证技术分析
    tech_card = _index_tech_card(tech)
    if tech_card:
        lines.append(tech_card)

    # 市场情绪
    breadth_card = _market_breadth_card(breadth)
    if breadth_card:
        lines.append(breadth_card)

    lines.append("\n---")
    lines.append("<font color='grey'>数据：腾讯 qt  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "📊 收盘概览", "\n".join(lines), "blue", log=log)


def card_theme_capital(heat, late_stocks):
    lines = ["**💰 概念题材资金流向**\n"]

    # 主数据：同花顺概念题材排名
    concepts = heat.get("concepts")
    if concepts:
        lines.append("**📊 题材热度排名**（同花顺强势股归因）")
        lines.append("| 题材 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for tag, cnt, stocks in concepts[:10]:
            top_names = "、".join(s["name"] for s in stocks[:3])
            lines.append(f"| **{tag}** | {cnt} | {top_names} |")
    else:
        lines.append("<font color='orange'>⚠️ 同花顺热点数据不可用</font>")

    # 连板题材
    strong = heat.get("strong_themes", {})
    if strong:
        top_strong = sorted(strong.items(), key=lambda x: -x[1]["count"])[:4]
        lines.append("\n**🔗 连板题材**（持续热点）")
        lines.append("| 题材 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for ind, info in top_strong:
            top_names = "、".join(s["name"] for s in info["stocks"][:3])
            lines.append(f"| {ind} | {info['count']} | {top_names} |")

    # 跌停行业（资金流出）
    down_ind = heat.get("down_industries", [])
    if down_ind:
        lines.append("\n**🔴 跌停行业**（资金流出方向）")
        lines.append("| 行业 | 只数 | 领跌股 |")
        lines.append("|------|------|--------|")
        for ind, cnt, stocks in down_ind[:5]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"| {ind} | {cnt} | {top_names} |")

    # 尾盘抢筹
    if late_stocks:
        lines.append("\n**🏃 尾盘抢筹**（涨幅 5-9.5%）")
        lines.append("| 名称 | 代码 | 现价 | 涨跌幅 |")
        lines.append("|------|------|------|--------|")
        for s in late_stocks[:5]:
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('change_pct'))} |")

    # 人气榜
    hot_rank = heat.get("hot_rank")
    if hot_rank:
        lines.append("\n**🔥 人气榜 TOP5**")
        lines.append("| 排名 | 名称 | 涨跌幅 | 概念 |")
        lines.append("|------|------|--------|------|")
        for s in hot_rank[:5]:
            tags = "、".join(s.get("concepts", [])[:2])
            pop = s.get("pop_tag", "")
            concept_cell = pop if pop else "-"
            if tags:
                concept_cell += f" [{tags}]" if concept_cell != "-" else f"[{tags}]"
            lines.append(f"| #{s['rank']} | {s['name']} | {s['pct']}% | {concept_cell} |")

    # 资金判断
    lines.append("\n**💡 资金流向判断**")
    if concepts:
        top3_tags = "、".join(tag for tag, _, _ in concepts[:3])
        lines.append(f"<font color='grey'>今日资金主攻方向：{top3_tags}</font>")
    if strong:
        top_strong = sorted(strong.items(), key=lambda x: -x[1]["count"])[:3]
        strong_names = "、".join(ind for ind, _ in top_strong)
        lines.append(f"<font color='grey'>持续热点：{strong_names}</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：同花顺热点 · akshare 涨停池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "💰 题材资金", "\n".join(lines), "orange", log=log)


def card_hot_stocks(up, up_src, down, down_src, strong, ths_limit=None):
    lines = ["**🎯 热门标的**\n"]

    # 构建涨停揭秘 code→info 映射
    ths_map = {}
    if ths_limit:
        for s in ths_limit:
            code = s.get("code", "")
            if code:
                ths_map[code] = s

    # 涨停前排
    if up:
        lines.append(f"**涨停前排** ({up_src})")
        lines.append("| 名称 | 代码 | 现价 | 涨跌幅 | 连板/封板/原因 |")
        lines.append("|------|------|------|--------|----------------|")
        for s in up[:10]:
            extra = ""
            if up_src == "akshare":
                cons = s.get("consecutive", 0)
                if cons > 1:
                    extra += f"<br>{cons}连板"
                seal = s.get("seal_amount", 0)
                if seal > 0:
                    extra += f"<br>封板{seal/1e8:.1f}亿"
                ind = s.get("industry", "")
                if ind:
                    extra += f"<br>{ind}"
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    extra += f"<br>封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    extra += f"<br>{bt}"
                reason = ths_info.get("reason", "")
                if reason:
                    extra += f"<br><font color='grey'>└ {reason}</font>"
            extra_cell = extra.lstrip("<br>") if extra else "-"
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('chg_pct'))} | {extra_cell} |")

    # 连板/强势股
    if strong:
        lines.append(f"\n**连板/强势股**（持续热点）")
        lines.append("| 名称 | 代码 | 现价 | 涨跌幅 | 统计/行业/原因 |")
        lines.append("|------|------|------|--------|----------------|")
        for s in strong[:6]:
            zt_stat = s.get("zt_stat", "")
            reason = s.get("reason", "")
            ind = s.get("industry", "")
            extra = f"<br>{zt_stat}" if zt_stat else ""
            extra += f"<br>{ind}" if ind else ""
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    extra += f"<br>封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    extra += f"<br>{bt}"
                ths_reason = ths_info.get("reason", "")
                if ths_reason:
                    extra += f"<br><font color='grey'>└ {ths_reason}</font>"
            extra_cell = extra.lstrip("<br>") if extra else "-"
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('chg_pct'))} | {extra_cell} |")

    # 跌停
    if down:
        lines.append(f"\n**跌停** ({down_src})")
        lines.append("| 名称 | 代码 | 现价 | 涨跌幅 | 行业 |")
        lines.append("|------|------|------|--------|------|")
        for s in down[:5]:
            ind = s.get("industry", "")
            extra_cell = ind if ind else "-"
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('chg_pct'))} | {extra_cell} |")

    if not up and not strong:
        lines.append("<font color='grey'>暂无涨停数据</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：同花顺涨停揭秘 · 东财涨停池/连板池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🎯 热门标的", "\n".join(lines), "green", log=log)


def card_strategy(indices, heat, late_stocks):
    if indices:
        sh_idx = next((i for i in indices if "上证" in (i.get("name") or "")), None)
        sh_chg = sh_idx.get("chg_pct") if sh_idx else None
        if sh_chg is not None:
            if sh_chg > 1.5:
                sentiment, color = "强势收涨，多头主导", "red"
            elif sh_chg > 0:
                sentiment, color = "小幅收涨，情绪偏多", "red"
            elif sh_chg > -1.0:
                sentiment, color = "震荡偏弱，多空胶着", "orange"
            else:
                sentiment, color = "明显收跌，注意风险", "green"
        else:
            sentiment, color = "数据获取失败", "orange"
    else:
        sentiment, color = "数据获取失败", "orange"

    up_n = heat.get("up_count", 0)
    down_n = heat.get("down_count", 0)
    strong_n = heat.get("strong_count", 0)
    late_n = len(late_stocks)
    concepts = heat.get("concepts")
    strong_themes = heat.get("strong_themes", {})
    hot_rank = heat.get("hot_rank")

    # 打板情绪 V2
    sent_v2 = _limit_up_sentiment_v2()

    # ── 组装卡片 ──
    lines = [
        f"**🧠 明日策略**",
        "",
        f"今日情绪：<font color='{color}'>{sentiment}</font>",
        "",
        "**关键数据**",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 涨停 | {up_n} 只 |",
        f"| 跌停 | {down_n} 只 |",
        f"| 连板 | {strong_n} 只 |",
        f"| 尾盘抢筹 | {late_n} 只 |",
    ]

    # 打板情绪 V2 增强
    if sent_v2:
        lines.append(f"| 炸板率 | {sent_v2['break_rate']}% |")
        lines.append(f"| 晋级率 | {sent_v2['advance_rate']}% · 最高{sent_v2['max_height']}板 |")
        if sent_v2.get("profit_effect"):
            pe = sent_v2["profit_effect"]
            lines.append(f"| 赚钱效应 | 均{pe['avg_pct']:+.1f}% · 红盘{pe['red_ratio']}% |")

    # 北向资金
    nb = _ths_north_bound()
    if nb:
        nb_label = "北向净流入" if nb["total"] >= 0 else "北向净流出"
        nb_line = f"| {nb_label} | {nb['total']:+.2f}亿 （沪{nb['sh']:+.2f} / 深{nb['sz']:+.2f}）"
        if nb.get("consecutive", 0) > 0:
            nb_line += f" · 连续{nb['consecutive']}日{nb['direction']} · 5日均值{nb['ma5']:+.2f}亿"
        nb_line += " |"
        lines.append(nb_line)

    # 龙虎榜 TOP5 净买入
    lhb = _em_lhb_daily()
    if lhb:
        lhb_top5 = lhb[:5]
        lhb_names = "、".join(f"{s['name']}({s['net_buy_wan']:.0f}万)" for s in lhb_top5)
        lines.append(f"| 龙虎榜TOP5 | {lhb_names} |")

    # 题材热度
    if concepts:
        hot_themes = "、".join(tag for tag, _, _ in concepts[:3])
        lines.append(f"| 热门方向 | {hot_themes} |")
    else:
        lines.append(f"| 热门方向 | 暂无 |")

    # 连板持续题材
    if strong_themes:
        top_strong = sorted(strong_themes.items(), key=lambda x: -x[1]["count"])[:3]
        strong_names = "、".join(ind for ind, _ in top_strong)
        lines.append(f"| 持续热点 | {strong_names} |")

    # 尾盘抢筹方向
    if late_stocks:
        late_names = "、".join(s["name"] for s in late_stocks[:3])
        lines.append(f"| 尾盘抢筹 | {late_names} |")

    # 美股→A股联动复盘
    linkage = _us_a_linkage()
    if linkage:
        from uzi_common import _us_a_linkage_card
        card = _us_a_linkage_card(linkage, compact=True)
        if card:
            lines.append("")
            lines.append(card)

    lines.append("")

    # ── 明日关注（表格化）──
    lines.append("**明日关注**")
    lines.append("")
    lines.append("| 关注点 | 判断标准 |")
    lines.append("|--------|----------|")
    lines.append("| 封板率 | 封板资金/炸板次数 → 判断题材持续性 |")
    lines.append("| 连板断板 | 高标是否断板 → 高位股风险信号 |")
    lines.append("| 尾盘抢筹次日 | 抢筹标的次日表现 → 资金接力意愿 |")
    lines.append("| 隔夜外盘 | 美股/期货走势 → 情绪传导 |")
    lines.append("")

    # ── 操作思路（表格化）──
    lines.append("**操作思路**")
    lines.append("")
    lines.append("| 场景 | 策略 |")
    lines.append("|------|------|")
    lines.append("| 封板率>80%+连板未断 | 题材延续，可跟踪龙头后排 |")
    lines.append("| 尾盘抢筹与涨停方向一致 | 资金共识强，次日高开概率大 |")
    lines.append("| 跌停集中于某行业扩散 | 行业系统性利空，回避 |")
    lines.append("| 缩量+涨停数减少 | 市场情绪退潮，控制仓位 |")

    content = "\n".join(lines)
    send_card("🧠 明日策略", content, "red")


def main():
    log("=" * 60)
    log("收盘复盘+尾盘抢筹启动 v4")
    if not is_trading_day():
        log("今日非交易日，跳过")
        return

    indices, tech, breadth = get_overview()
    heat = get_theme_heat()
    up, up_src, down, down_src, strong = get_hot_stocks()
    late = get_late_stocks()

    card_overview(indices, tech, breadth)
    time.sleep(0.5)
    card_theme_capital(heat, late)
    time.sleep(0.5)
    card_hot_stocks(up, up_src, down, down_src, strong, heat.get("ths_limit"))
    time.sleep(0.5)
    build_ladder_card(webhook=FEISHU_WEBHOOK, log_func=log)
    time.sleep(0.5)
    card_strategy(indices, heat, late)
    log("收盘复盘完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)