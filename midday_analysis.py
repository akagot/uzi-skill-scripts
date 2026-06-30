#!/usr/bin/env python3
"""
午盘大盘分析自动化 (11:31) · v3
- 交易日 11:31 执行
- 4 卡片：午盘概览 / 题材轮动 / 热门标的 / 下午研判
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
    _fmt_seal, _get_concept_tags, build_ladder_card, _ths_north_bound,
    send_feishu_card, color_chg, fmt_price, fmt_amount_yi, is_trading_day, make_logger,
    _index_tech_analysis, _index_tech_card,
    _market_breadth, _market_breadth_card,
    _em_hot_rank_v2,
)

LOG_FILE = Path("/tmp/uzi_midday_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000
log = make_logger(LOG_FILE)

# 共享 send_card 本地包装
from uzi_common import send_card as _send_card
send_card = lambda title, content, template="blue": _send_card(FEISHU_WEBHOOK, title, content, template, MAX_BYTES, log_func=log)

# ── 1. 午盘概览 ──
def get_overview():
    indices = _index_quotes()
    tech = _index_tech_analysis()
    breadth = _market_breadth()
    log(f"指数: {len(indices)} 条, 技术分析: {'有' if tech else '无'}, 情绪: {'有' if breadth else '无'}")
    return indices, tech, breadth

# ── 2. 题材轮动 ──
def get_theme_heat():
    heat = _theme_heat_safe()
    log(f"题材: 涨停{heat['up_count']} 跌停{heat['down_count']} 连板{heat['strong_count']}")
    return heat

# ── 3. 热门标的 ──
def get_hot_stocks():
    up, up_src = _fetch_limit_pool_safe()
    strong = _fetch_strong_pool_em()
    return up, up_src, strong

# ── 推送卡片 ──
def card_overview(indices, tech=None, breadth=None):
    lines = ["**📊 午盘·大盘指数**\n"]
    if indices:
        lines.append("| 指数 | 点位 | 涨跌幅 |")
        lines.append("|------|------|--------|")
        for idx in indices:
            lines.append(f"| **{idx['name']}** | {fmt_price(idx.get('price'))} | {color_chg(idx.get('chg_pct'))} |")
    else:
        lines.append("<font color='orange'>⚠️ 数据获取失败</font>")
    # 上证技术分析
    tech_card = _index_tech_card(tech)
    if tech_card:
        lines.append(tech_card)
    # 市场情绪
    breadth_card = _market_breadth_card(breadth)
    if breadth_card:
        lines.append(breadth_card)
    # 北向资金
    nb = _ths_north_bound()
    if nb:
        nb_dir = "🔴净流入" if nb["total"] >= 0 else "🟢净流出"
        lines.append(f"\n**北向资金** {nb_dir} {abs(nb['total']):.2f}亿 （沪股通 {nb['sh']:+.2f}亿 / 深股通 {nb['sz']:+.2f}亿）")
        if nb.get("consecutive", 0) > 0:
            lines.append(f"连续{nb['consecutive']}日{nb['direction']} | 5日均值：{nb['ma5']:+.2f}亿")
    lines.append("\n---")
    lines.append("<font color='grey'>数据：腾讯 qt · 同花顺  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "📊 午盘概览", "\n".join(lines), "blue", log=log)


def card_theme_rotation(heat):
    lines = ["**🔄 概念题材轮动**\n"]

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

    # 跌停行业分布
    down_ind = heat.get("down_industries", [])
    if down_ind:
        lines.append("\n**🔴 跌停行业**（资金流出）")
        lines.append("| 行业 | 只数 | 领跌股 |")
        lines.append("|------|------|--------|")
        for ind, cnt, stocks in down_ind[:5]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"| {ind} | {cnt} | {top_names} |")

    # 人气榜
    hot_rank = heat.get("hot_rank")
    if hot_rank is None:
        hot_rank = _em_hot_rank_v2(5)
    if hot_rank:
        lines.append("\n**🔥 人气飙升**")
        lines.append("| 排名 | 名称 | 涨跌幅 | 概念 |")
        lines.append("|------|------|--------|------|")
        for s in hot_rank[:5]:
            tags = "、".join(s.get("concepts", [])[:2])
            tags = tags if tags else "-"
            pop = s.get("pop_tag", "")
            if pop:
                tags = f"{pop} · {tags}" if tags != "-" else pop
            lines.append(f"| #{s['rank']} | {s['name']} | {s['pct']}% | {tags} |")

    # 资金流向判断
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
    send_feishu_card(FEISHU_WEBHOOK, "🔄 题材轮动", "\n".join(lines), "orange", log=log)


def card_hot_stocks(up, up_src, strong, ths_limit=None):
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
        for s in up[:8]:
            extra = ""
            if up_src == "akshare":
                cons = s.get("consecutive", 0)
                if cons > 1:
                    extra += f"{cons}连板"
                seal = s.get("seal_amount", 0)
                if seal > 0:
                    if extra:
                        extra += " "
                    extra += f"封板{seal/1e8:.1f}亿"
                ind = s.get("industry", "")
                if ind:
                    if extra:
                        extra += " "
                    extra += f"{ind}"
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    if extra:
                        extra += " "
                    extra += f"封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    if extra:
                        extra += " "
                    extra += f"{bt}"
                reason = ths_info.get("reason", "")
                if reason:
                    if extra:
                        extra += " "
                    extra += f"<font color='grey'>{reason}</font>"
            extra = extra if extra else "-"
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('chg_pct'))} | {extra} |")

    # 连板池
    if strong:
        lines.append(f"\n**连板/强势股**")
        lines.append("| 名称 | 代码 | 现价 | 涨跌幅 | 统计/行业/原因 |")
        lines.append("|------|------|------|--------|----------------|")
        for s in strong[:6]:
            zt_stat = s.get("zt_stat", "")
            reason = s.get("reason", "")
            ind = s.get("industry", "")
            extra = f"{zt_stat}" if zt_stat else ""
            if ind:
                extra += f" {ind}" if extra else ind
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    extra += f" 封板率{sr*100:.0f}%" if extra else f"封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    extra += f" {bt}" if extra else bt
                ths_reason = ths_info.get("reason", "")
                if ths_reason:
                    extra += f" <font color='grey'>{ths_reason}</font>" if extra else f"<font color='grey'>{ths_reason}</font>"
            extra = extra if extra else "-"
            lines.append(f"| {s['name']} | {s['code']} | {fmt_price(s.get('price'))} | {color_chg(s.get('chg_pct'))} | {extra} |")

    if not up and not strong:
        lines.append("<font color='grey'>暂无涨停数据</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：同花顺涨停揭秘 · 东财涨停池/连板池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🎯 热门标的", "\n".join(lines), "green", log=log)


def card_judgment(indices, heat):
    if indices:
        sh_idx = next((i for i in indices if "上证" in (i.get("name") or "")), None)
        sh_chg = sh_idx.get("chg_pct") if sh_idx else None
        if sh_chg is not None:
            if sh_chg > 1.0:
                sentiment, color = "午盘强势上涨", "red"
            elif sh_chg > 0:
                sentiment, color = "午盘小幅上涨", "red"
            elif sh_chg > -1.0:
                sentiment, color = "午盘震荡偏弱", "orange"
            else:
                sentiment, color = "午盘明显下跌", "green"
        else:
            sentiment, color = "数据获取失败", "orange"
    else:
        sentiment, color = "数据获取失败", "orange"

    up_n = heat.get("up_count", 0)
    down_n = heat.get("down_count", 0)
    strong_n = heat.get("strong_count", 0)
    concepts = heat.get("concepts")
    strong_themes = heat.get("strong_themes", {})
    hot_rank = heat.get("hot_rank")

    # ── 组装卡片 ──
    lines = [
        f"**🧠 下午研判**",
        "",
        f"午盘情绪：<font color='{color}'>{sentiment}</font>",
        "",
        "**关键数据**",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 涨停 | {up_n} 只 |",
        f"| 跌停 | {down_n} 只 |",
        f"| 连板 | {strong_n} 只 |",
    ]

    # 北向资金
    nb = _ths_north_bound()
    if nb:
        nb_label = "北向净流入" if nb["total"] >= 0 else "北向净流出"
        lines.append(f"| {nb_label} | {nb['total']:+.2f}亿 （沪{nb['sh']:+.2f} / 深{nb['sz']:+.2f}） |")

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

    lines.append("")

    # ── 下午关注（表格化）──
    lines.append("**下午关注**")
    lines.append("")
    lines.append("| 关注点 | 判断标准 |")
    lines.append("|--------|----------|")
    lines.append("| 封板率 | 封板资金/炸板次数是否维持 |")
    lines.append("| 题材扩散 | 热门题材是否蔓延至同板块后排 |")
    lines.append("| 尾盘信号 | 是否有抢筹/跳水迹象 |")
    lines.append("| 跌停止跌 | 跌停行业是否出现止跌信号 |")
    lines.append("")

    # ── 操作思路（表格化）──
    lines.append("**操作思路**")
    lines.append("")
    lines.append("| 场景 | 策略 |")
    lines.append("|------|------|")
    lines.append("| 题材扩散+封板率>80% | 板块效应强，下午可跟踪后排 |")
    lines.append("| 连板数减少 | 题材热度降温，注意高位股风险 |")
    lines.append("| 跌停集中在某一行业 | 该行业有利空，回避 |")
    lines.append("| 上午涨停集中在少数题材 | 聚焦主线，忽略杂毛 |")

    content = "\n".join(lines)
    send_card("🧠 下午研判", content, "purple")


def main():
    log("=" * 60)
    log("午盘大盘分析启动 v4")
    if not is_trading_day():
        log("今日非交易日，跳过")
        return

    indices, tech, breadth = get_overview()
    heat = get_theme_heat()
    up, up_src, strong = get_hot_stocks()

    card_overview(indices, tech, breadth)
    time.sleep(0.5)
    card_theme_rotation(heat)
    time.sleep(0.5)
    card_hot_stocks(up, up_src, strong, heat.get("ths_limit"))
    time.sleep(0.5)
    build_ladder_card(webhook=FEISHU_WEBHOOK, log_func=log)
    time.sleep(0.5)
    card_judgment(indices, heat)
    log("午盘分析完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)