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
    send_feishu_card, color_chg, fmt_price, is_trading_day, make_logger,
    US_TECH_LEADERS, fetch_qt_quotes,
)

LOG_FILE = Path("/tmp/uzi_morning_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
log = make_logger(LOG_FILE)

# ── 1. 竞价概览（指数 + 隔夜美股参考）──
def get_overview():
    indices = _index_quotes()
    # 隔夜美股
    us_indices = fetch_qt_quotes([("NDX", "U"), ("SPX", "U"), ("DJI", "U")])
    us_idx_map = {}
    for q in us_indices:
        us_idx_map[q["code"]] = q
    log(f"指数: {len(indices)} 条, 美股指数: {len(us_indices)} 条")
    return indices, us_idx_map

# ── 2. 题材热度（昨日涨停行业 + 连板题材 + 新浪行业）──
def get_theme_heat():
    heat = _theme_heat_safe()
    log(f"题材热度: 涨停{heat['up_count']}/{heat['strong_count']}连板")
    return heat

# ── 3. 竞价异动（活跃股）──
def get_auction_stocks():
    stocks = _a_share_active_stocks()
    valid = [s for s in stocks if s.get("change_pct") is not None]
    up = sorted(valid, key=lambda x: -x["change_pct"])[:8]
    down = sorted(valid, key=lambda x: x["change_pct"])[:8]
    return up, down

# ── 推送卡片 ──
def card_overview(indices, us_idx_map):
    lines = ["**📊 竞价·大盘指数**\n"]
    if indices:
        for idx in indices:
            lines.append(f"• **{idx['name']}** {fmt_price(idx.get('price'))}  {color_chg(idx.get('chg_pct'))}")
    else:
        lines.append("<font color='orange'>⚠️ 指数数据获取失败</font>")

    lines.append("\n**🇺🇸 隔夜美股**")
    for code, name in [("DJI", "道指"), ("SPX", "标普500"), ("NDX", "纳指100")]:
        q = us_idx_map.get(code)
        if q and q.get("change_pct") is not None:
            lines.append(f"• {name}  {color_chg(q.get('change_pct'))}")
        else:
            lines.append(f"• {name}  <font color='grey'>--</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：新浪行业 · 腾讯 qt  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "📊 竞价概览", "\n".join(lines), "blue", log=log)


def card_theme_heat(heat):
    lines = ["**🔥 题材热度**\n"]

    # 涨停行业分布（昨日）
    lines.append("**涨停行业分布**（昨日）")
    up_ind = heat.get("up_industries", [])
    if up_ind:
        for ind, cnt, stocks in up_ind[:6]:
            top_names = "、".join(s["name"] for s in stocks[:3])
            lines.append(f"• {ind}：{cnt} 只 （{top_names}）")
    else:
        lines.append("<font color='grey'>昨日无涨停数据</font>")

    # 连板题材
    strong = heat.get("strong_themes", {})
    if strong:
        top_strong = sorted(strong.items(), key=lambda x: -x[1]["count"])[:4]
        lines.append("\n**连板题材**（持续热点）")
        for ind, info in top_strong:
            top_names = "、".join(s["name"] for s in info["stocks"][:3])
            lines.append(f"• {ind}：{info['count']} 只 （{top_names}）")

    # 新浪行业领涨/领跌
    hot = heat.get("hot_sectors", [])
    cold = heat.get("cold_sectors", [])
    if hot or cold:
        lines.append("\n**行业板块**（实时）")
        if hot:
            lines.append("🟢 领涨")
            for s in hot:
                lines.append(f"  • {s['name']} {color_chg(s.get('chg_pct'))}  <font color='grey'>龙头 {s['lead_stock']}</font>")
        if cold:
            lines.append("🔴 领跌")
            for s in cold:
                lines.append(f"  • {s['name']} {color_chg(s.get('chg_pct'))}  <font color='grey'>龙头 {s['lead_stock']}</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：新浪行业 · akshare 涨停池  |  仅供参考</font>")
    send_feishu_card(FEISHU_WEBHOOK, "🔥 题材热度", "\n".join(lines), "orange", log=log)


def card_auction(up, down):
    lines = ["**⚡ 竞价异动**\n"]
    if not up and not down:
        lines.append("<font color='orange'>⚠️ 异动数据获取失败</font>")
    else:
        if up:
            lines.append("**领涨活跃股** 🟢")
            for s in up:
                lines.append(f"• {s['name']}({s['code']}) {fmt_price(s.get('price'))}  {color_chg(s.get('change_pct'))}")
        if down:
            lines.append("\n**领跌活跃股** 🔴")
            for s in down:
                lines.append(f"• {s['name']}({s['code']}) {fmt_price(s.get('price'))}  {color_chg(s.get('change_pct'))}")
    lines.append("\n---")
    lines.append("<font color='grey'>45 只活跃股权重股代理  |  数据：腾讯</font>")
    send_feishu_card(FEISHU_WEBHOOK, "⚡ 竞价异动", "\n".join(lines), "green", log=log)


def card_judgment(indices, heat, up, down):
    if indices:
        sh_idx = next((i for i in indices if "上证" in (i.get("name") or "")), None)
        sh_chg = sh_idx.get("chg_pct") if sh_idx else None
        if sh_chg is not None:
            if sh_chg > 0.5:
                sentiment, color = "竞价高开高走，指数强势", "green"
            elif sh_chg > 0:
                sentiment, color = "竞价小幅高开，情绪温和", "green"
            elif sh_chg > -0.5:
                sentiment, color = "竞价小幅低开，关注承接", "orange"
            else:
                sentiment, color = "竞价大幅低开，注意风险", "red"
        else:
            sentiment, color = "数据获取失败", "orange"
    else:
        sentiment, color = "数据获取失败", "orange"

    up_n = len(up)
    down_n = len(down)
    # 热门题材
    up_ind = heat.get("up_industries", [])
    hot_themes = "、".join(ind for ind, _, _ in up_ind[:3]) if up_ind else "暂无"

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

---
数据来源：新浪行业 · 腾讯财经 · akshare
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_feishu_card(FEISHU_WEBHOOK, "🧠 早盘研判", body, "purple", log=log)


def main():
    log("=" * 60)
    log("早盘竞价+大盘分析启动 v3")
    if not is_trading_day():
        log("今日非交易日，跳过")
        return

    indices, us_idx_map = get_overview()
    heat = get_theme_heat()
    up, down = get_auction_stocks()

    card_overview(indices, us_idx_map)
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