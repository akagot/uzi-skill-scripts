#!/usr/bin/env python3
"""
周末/假期消息面汇总自动化 (22:00)
- 每天晚上 22:00 执行
- 当 昨天非交易日 且 明天是交易日 时触发
- 收集周末/假期消息面
- 按 利好/利空/中性 映射到 A 股标的
- 发送飞书 4 卡片：本周复盘 → 周末消息 → 题材预判 → 下周策略
"""

import os
import sys
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── 共享工具 ──
sys.path.insert(0, str(Path(__file__).parent))
from uzi_common import (
    _http_get_text, send_feishu_card, send_card as _send_card, is_trading_day, make_logger,
    _theme_heat_safe, _index_quotes, color_chg, fmt_price,
    fetch_latest_skill,
    _index_tech_analysis, _index_tech_card,
    _market_breadth, _market_breadth_card,
    _cninfo_announcements, _ths_north_bound,
)

# ── 配置 ──
LOG_FILE = Path("/tmp/uzi_weekend_news.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000
log = make_logger(LOG_FILE)

# ── 工具函数 ──
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ── 飞书卡片（转发到 uzi_common） ──
send_card = lambda title, content, template="blue": _send_card(FEISHU_WEBHOOK, title, content, template, MAX_BYTES, log_func=log)

def send_text(title, content):
    try:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        log(f"飞书文本推送成功: {resp.read().decode()}")
    except Exception as e:
        log(f"飞书文本推送失败: {e}")

# ── 交易日判断（uzi_common 已支持 akshare fallback） ──
def is_trading_day_local(date_str):
    """date_str 格式 YYYYMMDD"""
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(date_str, "%Y%m%d")
        return is_trading_day(dt)
    except Exception as e:
        log(f"交易日判断失败: {e}")
        # 周末直接返回 False
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(date_str, "%Y%m%d")
            return d.weekday() < 5
        except:
            return True

# ── 消息面收集（使用 uzi_common 带重试的 HTTP 客户端） ──
def _fetch_json(url, headers=None, timeout=15):
    """JSON 拉取，失败返回 None"""
    text = _http_get_text(url, timeout=timeout, retries=2, extra_headers=headers)
    if text is None:
        log(f"HTTP请求失败 {url[:80]}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log(f"JSON解析失败 {url[:80]}: {e}")
        return None

def collect_weekend_news():
    """收集周末消息面：新浪4频道 + 金十数据 + 东财全球资讯
    使用 uzi_common._collect_all_news 统一聚合（V4.2 重构，符合 a-stock-data SKILL）"""
    log("开始收集周末消息面...")
    from uzi_common import _collect_all_news

    # 新浪4频道 + 金十 + 东财全球资讯，统一聚合自动去重
    news = _collect_all_news(
        sources=["jin10", "em_global", "sina_yaowen", "sina_agu", "sina_chanjing", "sina_global"],
        max_per_source={
            "jin10": 20,
            "em_global": 15,
            "sina_yaowen": 15,
            "sina_agu": 15,
            "sina_chanjing": 15,
            "sina_global": 15,
        }
    )

    # 按来源映射 category（保持向后兼容）
    source_to_cat = {
        "金十数据": "财经快讯",
        "东财全球资讯": "全球资讯",
        "新浪财经要闻": "财经要闻",
        "新浪A股聚焦": "A股聚焦",
        "新浪产经动态": "产经动态",
        "新浪全球宏观": "全球宏观",
    }
    for n in news:
        n["category"] = source_to_cat.get(n.get("source", ""), "综合资讯")

    log(f"收集到 {len(news)} 条消息（去重后，6源聚合）")
    return news

# ── 消息面 → 标的映射 ──
# 格式: (keyword_groups, [(direction, stock, chain, logic)])
STOCK_MAP = [
    # ═══ AI/算力 ═══
    ((("AI智能体",), ("智能体互联",), ("Agent", "智能体"), ("AI应用", "标准"), ("人工智能标准",), ("AI", "智能体")), [
        ("利好", "科大讯飞(002230)", "AI应用/语音平台", "智能体国标发布，利好国内AI Agent平台落地"),
        ("利好", "拓尔思(300229)", "AI应用/NLP", "智能体交互标准利好NLP技术应用"),
        ("利好", "汉得信息(300170)", "AI应用/企业服务", "Agent工具调用标准利好企业级AI落地"),
    ]),
    ((("华工正源",), ("光模块", "800G"), ("光模块", "出口"), ("算力出海",), ("光模块", "1.6T")), [
        ("利好", "华工科技(000988)", "光模块制造", "800G光模块出口同比增长超100倍，直接受益"),
        ("利好", "新易盛(300502)", "光模块/光器件", "算力出海核心标的"),
        ("利好", "中际旭创(300308)", "光模块龙头", "全球光模块龙头，800G/1.6T核心供应商"),
        ("利好", "天孚通信(300394)", "光器件", "光模块上游核心器件供应商"),
        ("利好", "光迅科技(002281)", "光通信器件", "受益于算力出海和光通信需求爆发"),
    ]),
    ((("太空算力",), ("智算", "大会"), ("智算", "年会"), ("算力大会",)), [
        ("利好", "中科曙光(603019)", "AI算力服务器", "太空算力大会催化，国产算力龙头"),
        ("利好", "浪潮信息(000977)", "AI服务器", "智算产业生态年会催化，AI服务器龙头"),
    ]),
    # ═══ 半导体 ═══
    ((("半导体", "涨价"), ("存储", "涨价"), ("HBM",), ("DRAM", "涨价"), ("NAND", "涨价"), ("美光",)), [
        ("利好", "兆易创新(603986)", "存储芯片", "存储芯片涨价周期，国产存储替代"),
        ("利好", "江波龙(301308)", "存储模组", "存储模组受益涨价"),
        ("利好", "北方华创(002371)", "半导体设备", "存储扩产带动设备需求"),
    ]),
    ((("光刻机",), ("半导体", "设备"), ("芯片", "国产化"), ("国产替代", "芯片")), [
        ("利好", "北方华创(002371)", "半导体设备", "国产半导体设备龙头"),
        ("利好", "中微公司(688012)", "半导体设备", "国产刻蚀机龙头"),
        ("利好", "中芯国际(688981)", "晶圆代工", "国产晶圆代工龙头"),
        ("利好", "华海清科(688120)", "半导体设备", "国产CMP设备"),
    ]),
    # ═══ PCB/封装 ═══
    ((("PCB", "涨价"), ("印制电路板",), ("封装", "涨价"), ("先进封装",)), [
        ("利好", "沪电股份(002463)", "PCB制造", "PCB涨价，AI服务器PCB龙头"),
        ("利好", "胜宏科技(300476)", "PCB制造", "高端PCB受益AI算力需求"),
        ("利好", "深南电路(002916)", "PCB/封装基板", "封装基板龙头"),
    ]),
    # ═══ 机器人 ═══
    ((("人形机器人",), ("Optimus",), ("机器人", "量产"), ("机器人", "订单"), ("机器人", "大会")), [
        ("利好", "拓普集团(601689)", "特斯拉供应链", "人形机器人零部件核心供应商"),
        ("利好", "三花智控(002050)", "热管理", "人形机器人热管理供应商"),
        ("利好", "绿的谐波(688017)", "谐波减速器", "人形机器人核心零部件"),
        ("利好", "鸣志电器(603728)", "电机", "机器人电机供应商"),
    ]),
    # ═══ 航天/卫星 ═══
    ((("卫星互联网",), ("星链",), ("低空经济",), ("商业航天",), ("航天", "发射")), [
        ("利好", "中国卫星(600118)", "卫星制造", "卫星互联网受益"),
        ("利好", "航天电子(600879)", "航天电子", "航天电子系统供应商"),
        ("利好", "中航高科(600862)", "航空材料", "低空经济受益"),
    ]),
    # ═══ 锂电 ═══
    ((("锂电", "涨价"), ("碳酸锂", "涨价"), ("锂电池", "扩产"), ("固态电池",)), [
        ("利好", "宁德时代(300750)", "动力电池", "锂电涨价周期龙头"),
        ("利好", "赣锋锂业(002460)", "锂资源", "碳酸锂涨价直接受益"),
        ("利好", "天齐锂业(002466)", "锂资源", "碳酸锂涨价直接受益"),
    ]),
    # ═══ 能源/煤炭 ═══
    ((("煤炭", "涨价"), ("煤价", "上涨"), ("动力煤",)), [
        ("利好", "中国神华(601088)", "煤炭", "煤价上涨直接受益"),
        ("利好", "陕西煤业(601225)", "煤炭", "煤价上涨直接受益"),
    ]),
    ((("原油", "上涨"), ("油价", "上涨"), ("OPEC", "减产")), [
        ("利好", "中国海油(600938)", "石油", "油价上涨利好上游"),
        ("利好", "中国石油(601857)", "石油", "油价上涨利好上游"),
        ("利空", "东方航空(600115)", "航空", "油价上涨利空航空"),
    ]),
    # ═══ 金融 ═══
    ((("降息",), ("降准",), ("宽松", "货币政策"), ("美联储", "降息")), [
        ("利好", "中信证券(600030)", "券商", "降息周期利好券商"),
        ("利好", "东方财富(300059)", "券商", "降息周期利好券商"),
        ("利好", "招商银行(600036)", "银行", "降息周期估值修复"),
        ("利好", "山东黄金(600547)", "黄金", "降息周期利好黄金"),
    ]),
    # ═══ 消费电子 ═══
    ((("Vision Pro",), ("折叠屏", "出货"), ("消费电子", "新机"), ("苹果", "新机"), ("华为", "新机")), [
        ("利好", "立讯精密(002475)", "消费电子代工", "苹果新品代工"),
        ("利好", "蓝思科技(300433)", "消费电子玻璃", "苹果新品供应链"),
        ("利好", "长盈精密(300115)", "消费电子精密件", "折叠屏/苹果结构件"),
    ]),
    # ═══ 光伏 ═══
    ((("光伏", "涨价"), ("硅料", "涨价"), ("光伏", "扩产"), ("光伏", "大会")), [
        ("利好", "隆基绿能(601012)", "光伏组件", "光伏龙头"),
        ("利好", "通威股份(600438)", "硅料/电池", "硅料涨价直接受益"),
        ("利好", "阳光电源(300274)", "逆变器", "光伏装机增长带动逆变器"),
    ]),
    # ═══ 医药 ═══
    ((("创新药",), ("GLP-1",), ("减肥药",), ("ADC", "药物"), ("生物医药", "突破")), [
        ("利好", "恒瑞医药(600276)", "创新药", "创新药龙头"),
        ("利好", "百济神州(688235)", "创新药", "创新药出海代表"),
        ("利好", "信达生物(01801.HK)", "创新药", "GLP-1减肥药概念"),
    ]),
    # ═══ 消费/白酒 ═══
    ((("白酒", "涨价"), ("茅台", "涨价"), ("茅台", "提价"), ("飞天", "涨价")), [
        ("利好", "贵州茅台(600519)", "白酒", "茅台提价预期"),
        ("利好", "五粮液(000858)", "白酒", "白酒板块提价"),
        ("利好", "泸州老窖(000568)", "白酒", "白酒板块提价"),
    ]),
]

def map_news_to_stocks(news_items):
    """将消息映射到标的 — 每只股票自带方向"""
    results = []
    for item in news_items:
        text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
        stock_dedup = {}

        for keyword_groups, stocks in STOCK_MAP:
            matched_kw = None
            for kw_tuple in keyword_groups:
                all_match = True
                for kw in kw_tuple:
                    if len(kw) <= 3 and kw.isupper() and kw.isascii():
                        if not re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
                            all_match = False
                            break
                    else:
                        if kw.lower() not in text:
                            all_match = False
                            break
                if all_match:
                    matched_kw = kw_tuple[0]
                    break

            if matched_kw:
                for direction, stock_name, chain, logic in stocks:
                    if stock_name not in stock_dedup:
                        stock_dedup[stock_name] = (stock_name, chain, logic, direction, matched_kw)

        if stock_dedup:
            results.append((item, list(stock_dedup.values())))

    return results

# ── 方向卡片 ──
def build_direction_cards(news_stock_map, direction, template_color):
    matching = []
    for item, stocks in news_stock_map:
        dir_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == direction]
        if dir_stocks:
            matching.append((item, dir_stocks))

    if not matching:
        title_map = {"利好": "🟢 利好方向", "利空": "🔴 利空方向", "中性": "🟡 中性 / 待观察"}
        send_card(title_map[direction], "暂无该方向消息", template_color)
        return

    title_map = {"利好": "🟢 利好方向", "利空": "🔴 利空方向", "中性": "🟡 中性 / 待观察"}
    section_title = title_map.get(direction, direction)

    lines = []
    for idx, (item, stocks) in enumerate(matching):
        title = item.get("title", "")[:80]
        if idx > 0:
            lines.append("")
        lines.append(f"**{idx + 1}. {title}**")
        lines.append("")
        lines.append("| 方向 | 标的 | 产业链 | 逻辑 |")
        lines.append("|------|------|--------|------|")
        for stock_name, chain, logic, d, kw in stocks:
            color = {"利好": "red", "利空": "green", "中性": "orange"}[d]
            arrow = {"利好": "🔴", "利空": "🟢", "中性": "🟡"}[d]
            lines.append(f"| <font color='{color}'>{arrow} {d}</font> | **{stock_name}** | {chain} | {logic} |")

    content = "\n".join(lines)
    test_body = json.dumps({"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": section_title}, "template": template_color}, "elements": [{"tag": "markdown", "content": content}]}}, ensure_ascii=False).encode("utf-8")
    if len(test_body) > MAX_BYTES:
        mid = len(matching) // 2
        _send_half(matching[:mid], section_title, template_color, 1)
        time.sleep(0.3)
        _send_half(matching[mid:], section_title, template_color, mid + 1)
    else:
        send_card(f"{section_title}（{len(matching)}条）", content, template_color)

def _send_half(items, base_title, template_color, start_num):
    lines = []
    for idx, (item, stocks) in enumerate(items):
        title = item.get("title", "")[:80]
        if idx > 0:
            lines.append("")
        lines.append(f"**{start_num + idx}. {title}**")
        for stock_name, chain, logic, d, kw in stocks:
            color = {"利好": "red", "利空": "green", "中性": "orange"}[d]
            arrow = {"利好": "🔴", "利空": "🟢", "中性": "🟡"}[d]
            lines.append(f"  <font color='{color}'>{arrow} {d}</font> **{stock_name}**  {chain} · {logic}")
    send_card(f"{base_title}（续）", "\n".join(lines), template_color)

# ═══════════════════════════════════════════════════════════════
# 卡片 1: 本周复盘
# ═══════════════════════════════════════════════════════════════
def card_weekly_review(last_trade_date_str):
    """
    本周复盘卡片 - 展示本周最后一个交易日大盘指数表现
    """
    log("生成卡片1: 本周复盘...")
    try:
        indices = _index_quotes()
    except Exception as e:
        log(f"获取指数数据失败: {e}")
        send_card("📊 本周复盘", "指数数据获取失败，请稍后重试", "blue")
        return

    if not indices:
        send_card("📊 本周复盘", "暂无指数数据", "blue")
        return

    # 解析日期
    try:
        dt = datetime.strptime(last_trade_date_str, "%Y%m%d")
        weekdays_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        date_display = dt.strftime("%Y年%m月%d日") + f" {weekdays_cn[dt.weekday()]}"
    except:
        date_display = last_trade_date_str

    # 计算涨跌统计
    up_count = sum(1 for idx in indices if (idx.get("chg_pct") or 0) > 0)
    down_count = sum(1 for idx in indices if (idx.get("chg_pct") or 0) < 0)
    flat_count = len(indices) - up_count - down_count

    if up_count >= 5:
        sentiment = "市场情绪偏乐观，主要指数普遍上涨"
        sentiment_color = "red"
    elif down_count >= 5:
        sentiment = "市场情绪偏谨慎，主要指数普遍下跌"
        sentiment_color = "green"
    elif up_count > down_count:
        sentiment = "市场情绪分化，多数指数收涨但仍有分化"
        sentiment_color = "red"
    elif down_count > up_count:
        sentiment = "市场情绪偏弱，多数指数收跌"
        sentiment_color = "green"
    else:
        sentiment = "市场情绪中性，指数涨跌互现"
        sentiment_color = "orange"

    lines = [
        f"**{date_display} 大盘指数表现**",
        "",
        "| 指数 | 点位 | 涨跌幅 |",
        "|------|------|--------|",
    ]

    for idx in indices:
        name = idx["name"]
        price = fmt_price(idx.get("price"))
        chg = idx.get("chg_pct")
        chg_str = color_chg(chg) if chg is not None else "<font color='grey'>--</font>"
        lines.append(f"| **{name}** | {price} | {chg_str} |")

    lines.append("")
    lines.append(f"**市场情绪判断：**")
    lines.append(f"<font color='{sentiment_color}'>{sentiment}</font>")
    lines.append(f"上涨 {up_count} 只 / 下跌 {down_count} 只 / 持平 {flat_count} 只")

    # 上证技术分析
    tech = _index_tech_analysis()
    tech_card = _index_tech_card(tech)
    if tech_card:
        lines.append(tech_card)

    # 市场情绪
    breadth = _market_breadth()
    breadth_card = _market_breadth_card(breadth)
    if breadth_card:
        lines.append(breadth_card)

    content = "\n".join(lines)
    send_card("📊 本周复盘", content, "blue")


# ═══════════════════════════════════════════════════════════════
# 卡片 2: 周末消息
# ═══════════════════════════════════════════════════════════════
def card_news(news_stock_map):
    """
    周末消息卡片 - 合并利好/利空为一张卡片
    """
    log("生成卡片2: 周末消息...")

    # 收集利好和利空
    bullish_items = []
    bearish_items = []
    for item, stocks in news_stock_map:
        b_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == "利好"]
        if b_stocks:
            bullish_items.append((item, b_stocks))
        br_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == "利空"]
        if br_stocks:
            bearish_items.append((item, br_stocks))

    lines = []

    # ── 利好消息 ──
    lines.append("**🔴 利好消息及标的**")
    lines.append("")
    if bullish_items:
        for idx, (item, stocks) in enumerate(bullish_items):
            title = item.get("title", "")[:80]
            lines.append(f"**{idx + 1}. {title}**")
            lines.append("")
            lines.append("| 标的 | 产业链 | 逻辑 |")
            lines.append("|------|--------|------|")
            for stock_name, chain, logic, d, kw in stocks:
                lines.append(f"| **{stock_name}** | {chain} | {logic} |")
            lines.append("")
    else:
        lines.append("暂无利好消息")

    # ── 利空消息 ──
    lines.append("")
    lines.append("**🟢 利空消息及标的**")
    lines.append("")
    if bearish_items:
        for idx, (item, stocks) in enumerate(bearish_items):
            title = item.get("title", "")[:80]
            lines.append(f"**{idx + 1}. {title}**")
            lines.append("")
            lines.append("| 标的 | 产业链 | 逻辑 |")
            lines.append("|------|--------|------|")
            for stock_name, chain, logic, d, kw in stocks:
                lines.append(f"| **{stock_name}** | {chain} | {logic} |")
            lines.append("")
    else:
        lines.append("暂无利空消息")

    content = "\n".join(lines)

    # 检查是否需要分两半发送
    test_body = json.dumps({"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "周末消息面汇总"}, "template": "green"}, "elements": [{"tag": "markdown", "content": content}]}}, ensure_ascii=False).encode("utf-8")

    if len(test_body) > MAX_BYTES:
        # 分两半：利好部分 + 利空部分
        log("周末消息内容过长，分两半发送")

        # 上半：利好
        half1_lines = []
        half1_lines.append("**🔴 利好消息及标的**")
        half1_lines.append("")
        if bullish_items:
            for idx, (item, stocks) in enumerate(bullish_items):
                title = item.get("title", "")[:80]
                half1_lines.append(f"**{idx + 1}. {title}**")
                half1_lines.append("")
                half1_lines.append("| 标的 | 产业链 | 逻辑 |")
                half1_lines.append("|------|--------|------|")
                for stock_name, chain, logic, d, kw in stocks:
                    half1_lines.append(f"| **{stock_name}** | {chain} | {logic} |")
                half1_lines.append("")
        else:
            half1_lines.append("暂无利好消息")

        send_card("📰 周末消息面汇总（上）", "\n".join(half1_lines), "green")
        time.sleep(0.3)

        # 下半：利空
        half2_lines = []
        half2_lines.append("**🟢 利空消息及标的**")
        half2_lines.append("")
        if bearish_items:
            for idx, (item, stocks) in enumerate(bearish_items):
                title = item.get("title", "")[:80]
                half2_lines.append(f"**{idx + 1}. {title}**")
                half2_lines.append("")
                half2_lines.append("| 标的 | 产业链 | 逻辑 |")
                half2_lines.append("|------|--------|------|")
                for stock_name, chain, logic, d, kw in stocks:
                    half2_lines.append(f"| **{stock_name}** | {chain} | {logic} |")
                half2_lines.append("")
        else:
            half2_lines.append("暂无利空消息")

        send_card("📰 周末消息面汇总（下）", "\n".join(half2_lines), "green")
    else:
        stats = f"利好 {len(bullish_items)} 条 / 利空 {len(bearish_items)} 条"
        send_card(f"📰 周末消息面汇总（{stats}）", content, "green")


# ═══════════════════════════════════════════════════════════════
# 卡片 3: 题材预判
# ═══════════════════════════════════════════════════════════════
def card_theme_preview(news_stock_map, last_trade_date_str):
    """
    题材预判卡片 - 结合消息面 + 上周涨停数据预判下周题材
    """
    log("生成卡片3: 题材预判...")

    # ── 1. 从利好消息中提取题材关键词 ──
    news_topics = set()
    for item, stocks in news_stock_map:
        for stock_name, chain, logic, d, kw in stocks:
            if d == "利好":
                news_topics.add(kw)
    news_topics_list = sorted(news_topics) if news_topics else []

    # ── 2. 获取涨停/连板题材 ──
    try:
        heat = _theme_heat_safe(last_trade_date_str)
    except Exception as e:
        log(f"获取题材热度失败: {e}")
        heat = None

    up_industries = heat.get("up_industries", []) if heat else []
    strong_themes = heat.get("strong_themes", {}) if heat else {}
    hot_sectors = heat.get("hot_sectors", []) if heat else []

    lines = []

    # ── 消息面热点方向 ──
    lines.append("**消息面热点方向**")
    lines.append("")
    if news_topics_list:
        # 将关键词映射到题材大类
        topic_map = {
            "AI智能体": "AI应用", "智能体互联": "AI应用", "光模块": "算力/光通信",
            "算力出海": "算力/光通信", "太空算力": "算力", "智算": "算力",
            "半导体": "半导体", "存储": "半导体/存储", "光刻机": "半导体设备",
            "PCB": "PCB/封装", "先进封装": "PCB/封装",
            "人形机器人": "机器人", "机器人": "机器人",
            "卫星互联网": "航天/卫星", "低空经济": "低空经济", "商业航天": "航天/卫星",
            "锂电": "锂电", "碳酸锂": "锂电", "固态电池": "锂电",
            "煤炭": "煤炭", "原油": "石油", "油价": "石油",
            "降息": "金融", "降准": "金融", "美联储": "金融",
            "折叠屏": "消费电子", "消费电子": "消费电子",
            "光伏": "光伏", "硅料": "光伏",
            "创新药": "医药", "减肥药": "医药",
            "白酒": "白酒/消费", "茅台": "白酒/消费",
        }
        mega_topics = {}
        for kw in news_topics_list:
            mega = topic_map.get(kw, kw)
            if mega not in mega_topics:
                mega_topics[mega] = []
            mega_topics[mega].append(kw)

        lines.append("| 题材大类 | 关键词 |")
        lines.append("|----------|--------|")
        for mega, kws in sorted(mega_topics.items(), key=lambda x: -len(x[1])):
            lines.append(f"| **{mega}** | {', '.join(kws[:3])} |")
    else:
        lines.append("未识别到明确热点方向")

    # ── 上周概念题材热度 ──
    lines.append("")
    lines.append("**概念题材热度（上周五）**")
    lines.append("")
    concepts = heat.get("concepts") if heat else None
    if concepts:
        lines.append("| 题材 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for tag, cnt, stocks in concepts[:8]:
            top_names = ", ".join(s["name"] for s in stocks[:3])
            lines.append(f"| **{tag}** | {cnt} | {top_names} |")
    else:
        up_industries = heat.get("up_industries", []) if heat else []
        if up_industries:
            lines.append("| 行业 | 涨停数 | 领涨股 |")
            lines.append("|------|--------|--------|")
            for ind, cnt, stocks in up_industries[:8]:
                top_names = ", ".join(s["name"] for s in stocks[:3])
                lines.append(f"| **{ind}** | {cnt} | {top_names} |")
        else:
            if heat:
                lines.append(f"涨停池数据源: {heat.get('up_src', '未知')}，涨停 {heat.get('up_count', 0)} 只")
            else:
                lines.append("题材数据获取失败")

    # ── 连板持续题材 ──
    lines.append("")
    lines.append("**连板持续题材**")
    lines.append("")
    if strong_themes:
        sorted_themes = sorted(strong_themes.items(), key=lambda x: -x[1]["count"])
        lines.append("| 题材 | 只数 | 领涨股 |")
        lines.append("|------|------|--------|")
        for ind, info in sorted_themes[:6]:
            names = ", ".join(s["name"] for s in info["stocks"][:3])
            lines.append(f"| **{ind}** | {info['count']} | {names} |")
    else:
        lines.append("暂无连板持续题材")

    # ── 题材延续性判断 ──
    lines.append("")
    lines.append("**题材延续性判断**")
    lines.append("")

    # 将消息关键词映射到题材大类
    news_mega_topics = set()
    topic_map = {
        "AI智能体": "AI应用", "智能体互联": "AI应用", "光模块": "算力/光通信",
        "算力出海": "算力/光通信", "太空算力": "算力", "智算": "算力",
        "半导体": "半导体", "存储": "半导体/存储", "光刻机": "半导体设备",
        "PCB": "PCB/封装", "先进封装": "PCB/封装",
        "人形机器人": "机器人", "机器人": "机器人",
        "卫星互联网": "航天/卫星", "低空经济": "低空经济", "商业航天": "航天/卫星",
        "锂电": "锂电", "碳酸锂": "锂电", "固态电池": "锂电",
        "煤炭": "煤炭", "原油": "石油", "油价": "石油",
        "降息": "金融", "降准": "金融", "美联储": "金融",
        "折叠屏": "消费电子", "消费电子": "消费电子",
        "光伏": "光伏", "硅料": "光伏",
        "创新药": "医药", "减肥药": "医药",
        "白酒": "白酒/消费", "茅台": "白酒/消费",
    }
    for kw in news_topics_list:
        mega = topic_map.get(kw, kw)
        news_mega_topics.add(mega)

    # 涨停行业
    limit_industries = set(ind for ind, _, _ in up_industries) if up_industries else set()

    # 连板行业
    strong_industries = set(strong_themes.keys()) if strong_themes else set()

    # 共振判断
    resonance = []
    if news_mega_topics and concepts:
        concept_tags = set(tag for tag, _, _ in concepts)
        for news_topic in news_mega_topics:
            for tag in concept_tags:
                if news_topic in tag or tag in news_topic or any(
                    kw in tag for kw in news_topic.split("/")
                ):
                    resonance.append(news_topic)
                    break
        resonance = list(set(resonance))

    if resonance:
        lines.append(f"<font color='red'>消息面 + 上周概念题材共振：{', '.join(resonance)}</font>")
        lines.append("以上题材下周延续概率较高，重点关注龙头")
    else:
        lines.append("<font color='orange'>消息面与上周概念题材无明显共振</font>")
        lines.append("关注消息面驱动的新题材启动机会")

    # 连板题材延续性
    if strong_industries:
        # 检查连板题材与消息面的持续性
        sustained = []
        for ind in strong_industries:
            for news_topic in news_mega_topics:
                if news_topic in ind or ind in news_topic or any(
                    kw in ind for kw in news_topic.split("/")
                ):
                    sustained.append(ind)
                    break
        sustained = list(set(sustained))

        if sustained:
            lines.append(f"<font color='red'>连板题材+消息面共振：{', '.join(sustained)}</font>")
            lines.append("这些题材有消息面支撑，连板行情可能延续")
        else:
            lines.append(f"<font color='orange'>连板题材（{', '.join(list(strong_industries)[:5])}）与消息面关联较弱</font>")
            lines.append("连板题材或以纯情绪博弈为主，需警惕高位风险")

    content = "\n".join(lines)
    send_card("🔮 题材预判", content, "orange")


# ═══════════════════════════════════════════════════════════════
# 卡片 4: 下周策略
# ═══════════════════════════════════════════════════════════════
def card_next_week_strategy(news_stock_map, last_trade_date_str):
    """
    下周策略卡片 - 综合研判
    """
    log("生成卡片4: 下周策略...")

    # ── 统计多空 ──
    all_stocks = []
    for item, stocks in news_stock_map:
        all_stocks.extend(stocks)
    bullish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利好")
    bearish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利空")
    news_with_bullish = sum(1 for item, stocks in news_stock_map if any(d == "利好" for _, _, _, d, _ in stocks))
    news_with_bearish = sum(1 for item, stocks in news_stock_map if any(d == "利空" for _, _, _, d, _ in stocks))

    # ── 市场情绪判断 ──
    if bullish_count > bearish_count * 2:
        sentiment = "消息面偏多，下周开盘情绪乐观"
        sentiment_color = "red"
        bias = "偏多"
    elif bearish_count > bullish_count * 2:
        sentiment = "消息面偏空，下周开盘需谨慎"
        sentiment_color = "green"
        bias = "偏空"
    elif bullish_count > bearish_count:
        sentiment = "消息面多空交织，整体偏多但需关注利空扰动"
        sentiment_color = "red"
        bias = "偏多"
    elif bearish_count > bullish_count:
        sentiment = "消息面多空交织，偏空因素略多"
        sentiment_color = "green"
        bias = "偏空"
    else:
        sentiment = "消息面多空均衡，方向不明确"
        sentiment_color = "orange"
        bias = "中性"

    # ── 提取消息题材关注方向 ──
    news_topics = set()
    for item, stocks in news_stock_map:
        for stock_name, chain, logic, d, kw in stocks:
            if d == "利好":
                news_topics.add(kw)

    topic_map = {
        "AI智能体": "AI应用", "智能体互联": "AI应用", "光模块": "算力/光通信",
        "算力出海": "算力/光通信", "太空算力": "算力", "智算": "算力",
        "半导体": "半导体", "存储": "半导体/存储", "光刻机": "半导体设备",
        "PCB": "PCB/封装", "先进封装": "PCB/封装",
        "人形机器人": "机器人", "机器人": "机器人",
        "卫星互联网": "航天/卫星", "低空经济": "低空经济", "商业航天": "航天/卫星",
        "锂电": "锂电", "碳酸锂": "锂电", "固态电池": "锂电",
        "煤炭": "煤炭", "原油": "石油", "油价": "石油",
        "降息": "金融", "降准": "金融", "美联储": "金融",
        "折叠屏": "消费电子", "消费电子": "消费电子",
        "光伏": "光伏", "硅料": "光伏",
        "创新药": "医药", "减肥药": "医药",
        "白酒": "白酒/消费", "茅台": "白酒/消费",
    }
    mega_topics = {}
    for kw in news_topics:
        mega = topic_map.get(kw, kw)
        if mega not in mega_topics:
            mega_topics[mega] = set()
        mega_topics[mega].add(kw)

    focus_directions = sorted(mega_topics.keys(), key=lambda x: -len(mega_topics[x]))

    # ── 组装卡片 ──
    try:
        dt = datetime.strptime(last_trade_date_str, "%Y%m%d")
        weekdays_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        date_display = dt.strftime("%Y年%m月%d日") + f" {weekdays_cn[dt.weekday()]}"
    except:
        date_display = last_trade_date_str

    lines = [
        f"**综合研判 · {date_display} 收盘**",
        "",
        "**市场情绪判断**",
        f"<font color='{sentiment_color}'>{sentiment}</font>",
        f"",
        f"**多空分布：**",
        f"| 类型 | 消息数 | 标的数 |",
        f"|------|--------|--------|",
        f"| <font color='red'>利好</font> | {news_with_bullish} | {bullish_count} |",
        f"| <font color='green'>利空</font> | {news_with_bearish} | {bearish_count} |",
        "",
        "**下周关注方向**",
    ]

    if focus_directions:
        lines.append("| 方向 | 逻辑 |")
        lines.append("|------|------|")
        for i, d in enumerate(focus_directions[:6]):
            lines.append(f"| **{d}** | 消息面驱动，关注龙头表现 |")
    else:
        if bias == "偏多":
            lines.append("• 关注消息面利好板块的补涨机会")
        elif bias == "偏空":
            lines.append("• 关注防御性板块（银行、公用事业、红利）")
        else:
            lines.append("• 关注消息面个股机会，整体以震荡思路对待")

    lines.append("")
    lines.append("**操作思路**")
    lines.append("")

    if bias == "偏多":
        lines.append("• 开盘关注利好板块龙头股表现，观察量能配合")
        lines.append("• 高开勿追，回调低吸为主")
        lines.append("• 关注连板题材的延续性，龙头断板即减仓")
        lines.append("• 仓位可适度提升至 6-7 成")
    elif bias == "偏空":
        lines.append("• 开盘后注意利空板块风险，及时减仓或回避")
        lines.append("• 关注防御性板块（银行、公用事业、红利ETF）")
        lines.append("• 仓位控制在 3-4 成，等待企稳信号")
        lines.append("• 连板高标注意高位风险，避免追高")
    else:
        lines.append("• 多空交织，控制仓位在 5 成左右")
        lines.append("• 关注消息面驱动板块的轮动节奏")
        lines.append("• 利好板块高开不追，等回调确认")
        lines.append("• 利空板块若低开过多可关注超跌反弹")

    content = "\n".join(lines)
    send_card("🧠 下周策略", content, "purple")


# ═══════════════════════════════════════════════════════════════
# 查找最近一个交易日
# ═══════════════════════════════════════════════════════════════
def _find_last_trading_day(today):
    """从 today 往前找最近一个交易日，返回 YYYYMMDD 字符串"""
    for days_back in range(1, 15):
        d = today - timedelta(days=days_back)
        ds = d.strftime("%Y%m%d")
        try:
            if is_trading_day(d):
                log(f"最近交易日: {ds}")
                return ds
        except Exception as e:
            log(f"交易日判断失败 {ds}: {e}")
            if d.weekday() < 5:
                return ds
    # 兜底：返回上周五
    d = today - timedelta(days=max(1, today.weekday() - 4))
    return d.strftime("%Y%m%d")


def card_announcements(news_stock_map):
    """公告卡片：关注标的的最新巨潮公告"""
    # 收集所有映射出的标的
    all_codes = set()
    for item, stocks in news_stock_map:
        for stock_name, chain, logic, d, kw in stocks:
            # 提取6位代码
            import re
            m = re.search(r'(\d{6})', stock_name)
            if m:
                all_codes.add(m.group(1))

    if not all_codes:
        return

    lines = ["**📋 明日关注标的 · 最新公告**\n"]
    lines.append("| 标的 | 公告 | 日期 |")
    lines.append("|------|------|------|")

    found = 0
    for code in list(all_codes)[:10]:
        anns = _cninfo_announcements(code, page_size=3)
        if anns:
            for a in anns[:3]:
                name = next((s[0] for _, stocks in news_stock_map for s in stocks if code in s[0]), code)
                short_name = name.split("(")[0] if "(" in name else name
                title = a["title"][:30]
                if len(a["title"]) > 30:
                    title += "..."
                date_str = a["date"][-5:] if a["date"] else "-"
                lines.append(f"| {short_name} | {title} | {date_str} |")
                found += 1
                if found >= 15:
                    break
        if found >= 15:
            break

    if found == 0:
        return

    lines.append(f"\n<font color='grey'>共 {found} 条公告，数据来源：巨潮资讯</font>")
    send_card("📋 公告速览", "\n".join(lines), "purple")


# ── 主流程 ──
def main():
    log("=" * 60)
    log("周末消息面汇总启动 v4")

    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    yesterday = (today - timedelta(days=1)).strftime("%Y%m%d")
    tomorrow = (today + timedelta(days=1)).strftime("%Y%m%d")

    # 判断是否触发：昨天非交易日 且 明天是交易日
    try:
        y_is_td = is_trading_day(yesterday)
        t_is_td = is_trading_day(tomorrow)
        log(f"昨天 {yesterday} 交易日: {y_is_td}, 明天 {tomorrow} 交易日: {t_is_td}")
    except Exception as e:
        log(f"交易日判断异常: {e}")
        y_is_td = False
        t_is_td = True

    # 简化判断：周五/周六晚 = 周末消息；周日晚 = 下周一开盘
    weekday = today.weekday()
    is_weekend_window = weekday in (4, 5)  # 周五、周六晚 22:00
    is_sunday_night = weekday == 6  # 周日 22:00
    holiday_eve = not y_is_td and t_is_td  # 假期前夜

    if not (is_weekend_window or is_sunday_night or holiday_eve):
        log(f"非周末/假期窗口 (今天周{weekday+1})，跳过")
        return

    log("触发周末/假期消息面汇总")

    # 1. 收集消息
    news_items = collect_weekend_news()

    if not news_items:
        log("未收集到消息，跳过")
        return

    # 2. 映射
    news_stock_map = map_news_to_stocks(news_items)

    all_stocks = []
    for item, stocks in news_stock_map:
        all_stocks.extend(stocks)

    bullish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利好")
    bearish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利空")
    neutral_count = sum(1 for _, _, _, d, _ in all_stocks if d == "中性")

    log(f"消息统计: 利好 {bullish_count} 只, 利空 {bearish_count} 只, 中性 {neutral_count} 只")

    # 3. 查找最近一个交易日（用于指数和题材数据）
    last_trade_date = _find_last_trading_day(today)

    # 4. 卡片 1: 本周复盘 (blue)
    card_weekly_review(last_trade_date)
    time.sleep(0.5)

    # 5. 卡片 2: 周末消息 (green) - 合并利好/利空
    card_news(news_stock_map)
    time.sleep(0.5)

    # 6. 卡片 3: 题材预判 (orange)
    card_theme_preview(news_stock_map, last_trade_date)
    time.sleep(0.5)

    # 7. 卡片 4: 下周策略 (purple)
    card_next_week_strategy(news_stock_map, last_trade_date)
    time.sleep(0.5)

    # 8. 卡片 5: 公告速览 (purple)
    card_announcements(news_stock_map)

    log("周末消息面汇总完成（5 卡片）")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)