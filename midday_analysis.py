#!/usr/bin/env python3
"""
midday_analysis.py - 午盘大盘分析自动化 (11:31) · v3
- 交易日 11:31 执行
- 4 卡片：午盘概览 / 题材轮动 / 热门标的 / 下午研判
- 数据源：腾讯 qt 指数 + 同花顺热点/涨停揭秘 + akshare 涨停池/连板池
"""

import sys
import time
from datetime import datetime
from pathlib import Path

# ── 共享工具 ──
sys.path.insert(0, str(Path(__file__).parent))
from uzi_common import (
    _index_quotes, _theme_heat_safe,
    _fetch_limit_pool_safe, _fetch_strong_pool_ak,
    send_feishu_card, color_chg, fmt_price, is_trading_day, make_logger,
)

# ── 配置 ──
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
    lines = ["**🔄 概念题材轮动**\n"]

    # 主数据：同花顺概念题材排名
    concepts = heat.get("concepts")
    if concepts:
        lines.append("**📊 题材热度排名**（同花顺强势股归因）")
        for tag, cnt, stocks in concepts[:10]:
            top_names = "、".join(s["name"] for s in stocks[:3])
            lines.append(f"• **{tag}**：{cnt} 只 （{top_names}）")
    else:
        lines.append("<font color='orange'>⚠️ 同花顺热点数据不可用</font>")

    # 连板题材
    strong = heat.get("strong_themes", {})
    if strong:
        top_strong = sorted(strong.items(), key=lambda x: -x[1]["count"])[:4]
        lines.append("\n**🔗 连板题材**（持续热点）")
        for ind, info in top_strong:
            top_names = "、".join(s["name"] for s in info["stocks"][:3])
            lines.append(f"• {ind}：{info['count']} 只 （{top_names}）")

    # 跌停行业分布
    down_ind = heat.get("down_industries", [])
    if down_ind:
        lines.append("\n**🔴 跌停行业**（资金流出）")
        for ind, cnt, stocks in down_ind[:5]:
            top_names = "、".join(s["name"] for s in stocks[:2])
            lines.append(f"• {ind}：{cnt} 只 （{top_names}）")

    # 人气榜
    hot_rank = heat.get("hot_rank")
    if hot_rank:
        lines.append("\n**🔥 人气飙升**")
        for s in hot_rank[:5]:
            tags = "、".join(s.get("concepts", [])[:2])
            pop = s.get("pop_tag", "")
            extra = f" · {pop}" if pop else ""
            if tags:
                extra += f" · [{tags}]"
            lines.append(f"• #{s['rank']} {s['name']} {s['pct']}%{extra}")

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
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    extra += f" · 封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    extra += f" · {bt}"
                reason = ths_info.get("reason", "")
                if reason:
                    extra += f"\n  <font color='grey'>└ {reason}</font>"
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
            # 同花顺涨停揭秘增强
            ths_info = ths_map.get(s.get("code", ""))
            if ths_info:
                sr = ths_info.get("seal_rate")
                if sr is not None:
                    extra += f" · 封板率{sr*100:.0f}%"
                bt = ths_info.get("board_type", "")
                if bt:
                    extra += f" · {bt}"
                ths_reason = ths_info.get("reason", "")
                if ths_reason:
                    extra += f"\n  <font color='grey'>└ {ths_reason}</font>"
            lines.append(f"• {s['name']}({s['code']}) {fmt_price(s.get('price'))}  {color_chg(s.get('chg_pct'))}{extra}")

    if not up and not strong:
        lines.append("<font color='grey'>暂无涨停数据</font>")

    lines.append("\n---")
    lines.append("<font color='grey'>数据：同花顺涨停揭秘 · akshare 涨停池/连板池  |  仅供参考</font>")
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
    hot_themes = "、".join(tag for tag, _, _ in concepts[:3]) if concepts else "暂无"

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
    card_hot_stocks(up, up_src, strong, heat.get("ths_limit"))
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