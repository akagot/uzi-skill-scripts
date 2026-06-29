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
    _fetch_limit_pool_safe, _fetch_dt_pool_safe, _fetch_strong_pool_ak,
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
    strong = _fetch_strong_pool_ak()
    return up, up_src, strong

# ── 推送卡片 ──
def card_overview(indices):
    lines = ["**📊 午盘·大盘指数**\n"]
    if indices:
        for idx in indices:
            lines.append(f"• **{idx['name']}** {fmt_price(idx.get('price'))}  {color_chg(idx.get('chg_pct'))}")
    else:
        lines.append("<font color='orange'>⚠️ 数据获取失败</font>")
    lines.append("\n---")
    lines.append("<font color='grey'>数据：腾讯 qt  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "📊 午盘概览", "\n".join(lines), "blue", log=log)


def card_theme_rotation(heat):
    lines = ["**🔄 题材轮动**\n"]

    # 涨停行业分布
    up_ind = heat.get("up_industries", [])
    lines.append("**🟢 涨停行业**")
    if up_ind:
        for ind, cnt, stocks in up_ind[:5]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"• {ind}：{cnt} 只 （{top_names}）")
    else:
        lines.append("<font color='grey'>暂无涨停数据</font>")

    # 跌停行业分布
    down_ind = heat.get("down_industries", [])
    if down_ind:
        lines.append("\n**🔴 跌停行业**")
        for ind, cnt, stocks in down_ind[:5]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"• {ind}：{cnt} 只 （{top_names}）")

    # 新浪行业实时
    hot = heat.get("hot_sectors", [])
    cold = heat.get("cold_sectors", [])
    if hot or cold:
        lines.append("\n**📈 行业涨跌**（实时）")
        if hot:
            lines.append("🟢 领涨 TOP5")
            for s in hot:
                lines.append(f"  • {s['name']} {color_chg(s.get('chg_pct'))}  <font color='grey'>龙头 {s['lead_stock']}</font>")
        if cold:
            lines.append("🔴 领跌 TOP5")
            for s in cold:
                lines.append(f"  • {s['name']} {color_chg(s.get('chg_pct'))}  <font color='grey'>龙头 {s['lead_stock']}</font>")

    # 资金流向判断
    lines.append("\n**💡 资金流向判断**")
    if up_ind and down_ind:
        up_total = sum(cnt for _, cnt, _ in up_ind)
        down_total = sum(cnt for _, cnt, _ in down_ind)
        if up_total > down_total * 2:
            flow = "资金明显做多，涨停集中在少数行业"
        elif up_total > down_total:
            flow = "做多为主，但跌停行业需警惕"
        else:
            flow = "多空分歧大，涨停/跌停行业均需关注"
        lines.append(f"<font color='grey'>{flow}</font>")
    if heat.get("strong_themes"):
        top_strong = sorted(heat["strong_themes"].items(), key=lambda x: -x[1]["count"])[:3]
        strong_names = "、".join(ind for ind, _ in top_strong)
        lines.append(f"<font color='grey'>持续热点：{strong_names}</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：新浪行业 · akshare 涨停池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🔄 题材轮动", "\n".join(lines), "orange", log=log)


def card_hot_stocks(up, up_src, strong):
    lines = ["**🎯 热门标的**\n"]

    # 涨停前排
    if up:
        lines.append(f"**涨停前排** ({up_src})")
        for s in up[:8]:
            extra = ""
            if up_src == "akshare":
                cons = s.get("consecutive", 0)
                if cons > 1:
                    extra += f" · {cons}连板"
                seal = s.get("seal_amount", 0)
                if seal > 0:
                    extra += f" · 封板{seal/1e8:.1f}亿"
                ind = s.get("industry", "")
                if ind:
                    extra += f" · {ind}"
            lines.append(f"• {s['name']}({s['code']}) {fmt_price(s.get('price'))}  {color_chg(s.get('chg_pct'))}{extra}")

    # 连板池
    if strong:
        lines.append(f"\n**连板/强势股**")
        for s in strong[:6]:
            zt_stat = s.get("zt_stat", "")
            reason = s.get("reason", "")
            ind = s.get("industry", "")
            extra = f" · {zt_stat}" if zt_stat else ""
            extra += f" · {ind}" if ind else ""
            lines.append(f"• {s['name']}({s['code']}) {fmt_price(s.get('price'))}  {color_chg(s.get('chg_pct'))}{extra}")

    if not up and not strong:
        lines.append("<font color='grey'>暂无涨停数据</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：akshare 涨停池/连板池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🎯 热门标的", "\n".join(lines), "green", log=log)


def card_judgment(indices, heat):
    if indices:
        sh_idx = next((i for i in indices if "上证" in (i.get("name") or "")), None)
        sh_chg = sh_idx.get("chg_pct") if sh_idx else None
        if sh_chg is not None:
            if sh_chg > 1.0:
                sentiment, color = "午盘强势上涨", "green"
            elif sh_chg > 0:
                sentiment, color = "午盘小幅上涨", "green"
            elif sh_chg > -1.0:
                sentiment, color = "午盘震荡偏弱", "orange"
            else:
                sentiment, color = "午盘明显下跌", "red"
        else:
            sentiment, color = "数据获取失败", "orange"
    else:
        sentiment, color = "数据获取失败", "orange"

    up_n = heat.get("up_count", 0)
    down_n = heat.get("down_count", 0)
    strong_n = heat.get("strong_count", 0)
    up_ind = heat.get("up_industries", [])
    hot_themes = "、".join(ind for ind, _, _ in up_ind[:3]) if up_ind else "暂无"

    body = f"""**🧠 下午研判**

午盘情绪：<font color='{color}'>{sentiment}</font>

**关键数据：**
• 涨停：{up_n} 只 | 跌停：{down_n} 只 | 连板：{strong_n} 只
• 热门方向：{hot_themes}

**下午关注：**
• 涨停板封板率是否维持（封板资金/炸板次数）
• 热门题材是否扩散至同板块其他标的
• 尾盘是否有抢筹/跳水迹象
• 跌停行业是否出现止跌信号

**操作思路：**
• 若涨停行业持续扩散且封板率>80% → 板块效应强，下午可跟踪后排
• 若连板数减少 → 题材热度降温，注意高位股风险
• 若跌停集中在某一行业 → 该行业有利空，回避

---
数据来源：新浪行业 · akshare 涨停池 · 腾讯 qt
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_feishu_card(FEISHU_WEBHOOK, "🧠 下午研判", body, "purple", log=log)


def main():
    log("=" * 60)
    log("午盘大盘分析启动 v3")
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
    card_hot_stocks(up, up_src, strong)
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