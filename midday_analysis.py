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
    send_feishu_card, color_chg, fmt_price, fmt_amount_yi, is_trading_day, make_logger,
)

LOG_FILE = Path("/tmp/uzi_midday_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
log = make_logger(LOG_FILE)

# ── 1. 午盘概览 ──
def get_overview():
    indices = _index_quotes()
    log(f"指数: {len(indices)} 条")
    return indices

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
def card_overview(indices):
    lines = ["**📊 午盘·大盘指数**\n"]
    if indices:
        lines.append("| 指数 | 点位 | 涨跌幅 |")
        lines.append("|------|------|--------|")
        for idx in indices:
            lines.append(f"| **{idx['name']}** | {fmt_price(idx.get('price'))} | {color_chg(idx.get('chg_pct'))} |")
    else:
        lines.append("<font color='orange'>⚠️ 数据获取失败</font>")
    lines.append("\n---")
    lines.append("<font color='grey'>数据：腾讯 qt  |  仅供参考</font>")
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


def _fmt_seal(amt):
    """封单金额格式化: 1.23亿 / 8210万"""
    if amt is None or amt == 0:
        return "--"
    if amt >= 1e8:
        return f"{amt/1e8:.2f}亿"
    return f"{amt/1e4:.0f}万"

def _get_concept_tags(s, ths_map):
    """获取一只股票的题材标签列表（从同花顺涨停原因拆分）"""
    c = s["code"]
    t = ths_map.get(c, {})
    reason = t.get("reason", "") or s.get("industry", "")
    if not reason:
        return []
    return [r.strip() for r in reason.split("+") if r.strip()]

def card_ladder(date_str=None):
    """连板天梯 — 二板起详细列 + 一板概念统计 + 题材纵深"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        all_zt = em_zt_pool(date_str)
        yzt = em_yzt_pool(date_str)
        ths = _ths_limit_up_pool(date_str)
    except Exception as e:
        log(f"连板天梯数据获取失败: {e}")
        send_feishu_card(FEISHU_WEBHOOK, "📊 连板天梯", "数据获取失败，请稍后重试", "blue", log=log)
        return

    # ── 分组 ──
    groups = {1: [], 2: [], 3: [], 4: []}
    for s in all_zt:
        ld = s.get("limit_days", 1)
        if ld <= 4:
            groups[ld].append(s)
        else:
            groups.setdefault(5, []).append(s)

    zb = em_zb_pool(date_str)
    dt = em_dt_pool(date_str)
    zt_n = len(all_zt)
    zb_n = len(zb)
    dt_n = len(dt)
    br = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0
    max_h = max((s.get("limit_days", 1) for s in all_zt), default=0)

    # ── 同花顺数据映射 ──
    ths_map = {}
    if ths:
        for t in ths:
            ths_map[str(t.get("code", ""))] = t

    # ── 构建概念题材纵深（{概念: {板数: [名称列表]}}）──
    concept_depth = {}
    for level in [1, 2, 3, 4, 5]:
        for s in groups.get(level, []):
            tags = _get_concept_tags(s, ths_map)
            for tag in tags:
                cd = concept_depth.setdefault(tag, {})
                cd.setdefault(level, []).append(s["name"])

    lines = ["**📊 连板天梯**\n"]

    # ── 顶部 Tab 栏 ──
    tabs = [
        f"一板 {len(groups[1])}只",
        f"二板 {len(groups[2])}只",
        f"三板 {len(groups[3])}只",
        f"四板 {len(groups[4])}只",
        f"更高 {(groups.get(5) or []) and len(groups[5]) or 0}只",
    ]
    lines.append("  ".join(tabs))
    lines.append(f"涨停: {zt_n}只 | 炸板: {zb_n}只 | 炸板率: {br}% | 跌停: {dt_n}只 | 最高: {max_h}板\n")

    # ═══════════════════════════════════════════════
    # 二板及以上详细列出
    # ═══════════════════════════════════════════════
    level_names = {2: "二板", 3: "三板", 4: "四板", 5: "更高板"}
    max_per_level = {2: 8, 3: 10, 4: 10, 5: 10}

    for level in [2, 3, 4, 5]:
        stocks = groups.get(level, [])
        if not stocks:
            continue
        stocks = sorted(stocks, key=lambda x: -(x.get("seal_fund") or 0))
        show_n = max_per_level.get(level, 8)
        lines.append(f"**━━━ {level_names[level]}（{len(stocks)}只）━━━**")
        lines.append("| 名称 | 代码 | 标签 | 涨停时间 | 涨停原因 | 封单 |")
        lines.append("|------|------|------|----------|----------|------|")
        for s in stocks[:show_n]:
            c = s["code"]
            t = ths_map.get(c, {})
            tags = []
            if t.get("is_again") == 1:
                tags.append("<font color='red'>回封</font>")
            elif s.get("break_times", 0) > 0 and t.get("is_again") != 1:
                tags.append("<font color='green'>破板</font>")
            tag_str = " ".join(tags) if tags else "-"
            reason = t.get("reason", "") or s.get("industry", "")
            if reason and len(reason) > 12:
                reason = reason[:12] + ".."
            reason = reason if reason else "-"
            seal = _fmt_seal(s.get("seal_fund"))
            first_seal = s.get('first_seal', '-')
            lines.append(
                f"| {s['name']} | {c} | {tag_str} | {first_seal} | {reason} | <font color='red'>{seal}</font> |"
            )
        if len(stocks) > show_n:
            lines.append(f"  <font color='grey'>... 还有 {len(stocks)-show_n} 只</font>")
        lines.append("")

    # ═══════════════════════════════════════════════
    # 一板：按概念题材统计
    # ═══════════════════════════════════════════════
    if groups[1]:
        yb_concepts = {}
        for s in groups[1]:
            tags = _get_concept_tags(s, ths_map)
            for tag in tags:
                yb_concepts[tag] = yb_concepts.get(tag, 0) + 1
        yb_top = sorted(yb_concepts.items(), key=lambda x: -x[1])[:12]
        morning = sum(1 for s in groups[1] if (s.get("first_seal") or "99") < "12:00")
        afternoon = len(groups[1]) - morning
        lines.append(f"**一板（{len(groups[1])}只）** 上午 {morning}只 | 下午 {afternoon}只")
        if yb_top:
            lines.append("| 概念 | 涨停数 |")
            lines.append("|------|--------|")
            for tag, cnt in yb_top:
                lines.append(f"| **{tag}** | {cnt} |")
        lines.append("")

    # ═══════════════════════════════════════════════
    # 题材纵深：某概念在多个板位都有标的
    # ═══════════════════════════════════════════════
    deep_concepts = []
    for tag, levels in concept_depth.items():
        total = sum(len(ns) for ns in levels.values())
        if len(levels) >= 2 and total >= 2:
            deep_concepts.append((tag, levels, total))
    deep_concepts.sort(key=lambda x: (-len(x[1]), -x[2]))

    if deep_concepts:
        lines.append(f"**━━━ 题材纵深（{len(deep_concepts)}个概念横跨多板）━━━**")
        lines.append("")
        lines.append("| 概念 | 板位分布 |")
        lines.append("|------|----------|")
        for tag, levels, total in deep_concepts[:8]:
            parts = []
            for lv in sorted(levels.keys()):
                names = levels[lv]
                part = f"{lv}板: {', '.join(names[:4])}"
                if len(names) > 4:
                    part += f"等{len(names)}只"
                parts.append(part)
            lines.append(f"| <font color='red'>**{tag}**</font> | {'<br>'.join(parts)} |")
        if len(deep_concepts) > 8:
            lines.append(f"  <font color='grey'>... 还有 {len(deep_concepts)-8} 个纵深概念</font>")
        lines.append("")

    # ── 2板及以上未涨停（高标断板）──
    if yzt:
        y2_fail = [s for s in yzt if s.get("y_limit_days", 0) >= 2 and s.get("pct", 0) < 9.8]
        if y2_fail:
            y2_fail = sorted(y2_fail, key=lambda x: -(x.get("pct") or 0))
            lines.append(f"**━━━ 未涨停的高标（昨日{min(s['y_limit_days'] for s in y2_fail)}~{max(s['y_limit_days'] for s in y2_fail)}板，{len(y2_fail)}只）━━━**")
            lines.append("| 名称 | 代码 | 昨板数 | 现价 | 涨跌幅 | 行业 |")
            lines.append("|------|------|--------|------|--------|------|")
            for s in y2_fail[:8]:
                price = s.get("price", 0)
                pct = s.get("pct", 0)
                color = "red" if pct >= 0 else "green"
                y_ld = s.get("y_limit_days", 0)
                ind = s.get("industry", "")
                if ind and len(ind) > 12:
                    ind = ind[:12] + ".."
                ind = ind if ind else "-"
                lines.append(
                    f"| {s['name']} | {s['code']} | {y_ld} | <font color='{color}'>{fmt_price(price)}</font> | <font color='{color}'>{pct:+.2f}%</font> | <font color='grey'>{ind}</font> |"
                )
            if len(y2_fail) > 8:
                lines.append(f"  <font color='grey'>... 还有 {len(y2_fail)-8} 只</font>")

    # ── 晋级率 ──
    yzt_total = len(yzt)
    yzt_continue = sum(1 for s in yzt if s.get("pct", 0) >= 9.8)
    jj_rate = round(yzt_continue / yzt_total * 100, 1) if yzt_total > 0 else 0
    lines.append(f"\n昨涨停 {yzt_total}只 → 今日连板 {yzt_continue}只，晋级率 {jj_rate}%")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：东财涨停板中心 · 同花顺涨停揭秘 | 仅供参考</font>")

    text = "\n".join(lines)
    if len(text.encode("utf-8")) > 16000:
        split_idx = text.find("**━━━ 未涨停的高标")
        if split_idx > 0:
            main_text = text[:split_idx].strip()
            fail_text = text[split_idx:].strip()
            send_feishu_card(FEISHU_WEBHOOK, "📊 连板天梯", main_text, "turquoise", log=log)
            time.sleep(0.3)
            send_feishu_card(FEISHU_WEBHOOK, "📊 连板天梯（续）", fail_text, "turquoise", log=log)
            return
    send_feishu_card(FEISHU_WEBHOOK, "📊 连板天梯", text, "turquoise", log=log)


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

    lines.append("")
    lines.append("---")
    lines.append("数据来源：同花顺热点 · 东财涨停池 · 东财涨停板中心 · 腾讯 qt")
    lines.append("使用 a-stock-data SKILL 最新版本")
    lines.append("声明：以上内容仅供市场信息参考，不构成任何投资建议")

    content = "\n".join(lines)
    send_card("🧠 下午研判", content, "purple")


def main():
    log("=" * 60)
    log("午盘大盘分析启动 v4")
    # 拉取最新 a-stock-data SKILL
    fetch_latest_skill()
    if not is_trading_day():
        log("今日非交易日，跳过")
        return

    indices = get_overview()
    heat = get_theme_heat()
    up, up_src, strong = get_hot_stocks()

    card_overview(indices)
    time.sleep(0.5)
    card_theme_rotation(heat)
    time.sleep(0.5)
    card_hot_stocks(up, up_src, strong, heat.get("ths_limit"))
    time.sleep(0.5)
    card_ladder()
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