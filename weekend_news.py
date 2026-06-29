#!/usr/bin/env python3
"""
周末/假期消息面汇总自动化 (22:00)
- 每天晚上 22:00 执行
- 当 昨天非交易日 且 明天是交易日 时触发
- 收集周末/假期消息面
- 按 利好/利空/中性 映射到 A 股标的
- 发送飞书分卡片
"""

import os
import sys
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──
LOG_FILE = Path("/tmp/uzi_weekend_news.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000

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

def send_card(title, content, template="blue"):
    try:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
                "elements": [{"tag": "markdown", "content": content}]
            }
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_BYTES:
            paras = content.split("\n\n")
            half = ""
            for p in paras:
                test = half + "\n\n" + p if half else p
                test_body = json.dumps({"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": title}, "template": template}, "elements": [{"tag": "markdown", "content": test}]}}, ensure_ascii=False).encode("utf-8")
                if len(test_body) > MAX_BYTES:
                    break
                half = test
            if half:
                body2 = json.dumps({"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": title}, "template": template}, "elements": [{"tag": "markdown", "content": half + "\n\n(内容过长，已截断)"}]}}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(FEISHU_WEBHOOK, data=body2, headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=30)
                log(f"飞书推送(截断)成功: {resp.read().decode()}")
            return
        req = urllib.request.Request(FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        log(f"飞书推送成功: {resp.read().decode()}")
    except Exception as e:
        log(f"飞书推送失败: {e}")

def send_text(title, content):
    try:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        log(f"飞书文本推送成功: {resp.read().decode()}")
    except Exception as e:
        log(f"飞书文本推送失败: {e}")

# ── 交易日判断 ──
def is_trading_day(date_str):
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set()
        for d in df["trade_date"].values:
            if hasattr(d, 'strftime'):
                trade_dates.add(d.strftime("%Y-%m-%d"))
            else:
                trade_dates.add(str(d)[:10])
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.strftime("%Y-%m-%d") in trade_dates
    except Exception as e:
        log(f"交易日判断失败: {e}")
        try:
            url = "http://qt.gtimg.cn/q=sh000001"
            resp = urllib.request.urlopen(url, timeout=10).read().decode("gbk")
            return '"' in resp and len(resp) > 100
        except:
            dt = datetime.strptime(date_str, "%Y%m%d")
            return dt.weekday() < 5

# ── 消息面收集 ──
def _fetch_json(url, headers=None, timeout=15):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except Exception as e:
        log(f"HTTP请求失败 {url[:80]}: {e}")
        return None

def collect_weekend_news():
    log("开始收集周末消息面...")
    news_items = []

    sources = [
        ("财经要闻", "2509"),
        ("A股聚焦", "2512"),
        ("产经动态", "2515"),
        ("全球宏观", "2516"),
    ]

    for cat_name, lid in sources:
        try:
            data = _fetch_json(f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&k=&num=15&page=1")
            if data and data.get("result", {}).get("data"):
                for item in data["result"]["data"]:
                    news_items.append({
                        "category": cat_name,
                        "title": item.get("title", ""),
                        "snippet": item.get("intro", "") or item.get("title", ""),
                        "url": item.get("url", ""),
                        "source": "新浪财经"
                    })
                log(f"新浪财经{cat_name}: {len(data['result']['data'])} 条")
        except Exception as e:
            log(f"新浪财经{cat_name}失败: {e}")

    # 去重
    seen = set()
    unique = []
    for item in news_items:
        key = item.get("title", "")[:30]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    news_items = unique
    log(f"收集到 {len(news_items)} 条消息（去重后）")
    return news_items

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
        for stock_name, chain, logic, d, kw in stocks:
            color = {"利好": "green", "利空": "red", "中性": "orange"}[d]
            arrow = {"利好": "🟢", "利空": "🔴", "中性": "🟡"}[d]
            lines.append(f"  <font color='{color}'>{arrow} {d}</font> **{stock_name}**  {chain} · {logic}")

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
            color = {"利好": "green", "利空": "red", "中性": "orange"}[d]
            arrow = {"利好": "🟢", "利空": "🔴", "中性": "🟡"}[d]
            lines.append(f"  <font color='{color}'>{arrow} {d}</font> **{stock_name}**  {chain} · {logic}")
    send_card(f"{base_title}（续）", "\n".join(lines), template_color)

# ── 主流程 ──
def main():
    log("=" * 60)
    log("周末消息面汇总启动")

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

    news_with_bullish = sum(1 for item, stocks in news_stock_map if any(d == "利好" for _, _, _, d, _ in stocks))
    news_with_bearish = sum(1 for item, stocks in news_stock_map if any(d == "利空" for _, _, _, d, _ in stocks))
    news_with_neutral = sum(1 for item, stocks in news_stock_map if any(d == "中性" for _, _, _, d, _ in stocks))

    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[today.weekday()]
    date_display = today.strftime("%Y年%m月%d日") + f" {wd}"

    # 3. 卡片1: 概览
    overview = f"""**{date_display} 周末/假期消息面汇总**

窗口：周末/假期 → 开盘前

消息总数：{len(news_items)} 条 | 关联标的：{len(set(s for s, _, _, _, _ in all_stocks))} 只

<font color='green'>▸ 利好：{news_with_bullish} 条消息 | {bullish_count} 只标的</font>
<font color='red'>▸ 利空：{news_with_bearish} 条消息 | {bearish_count} 只标的</font>
<font color='orange'>▸ 中性/待观察：{news_with_neutral} 条消息 | {neutral_count} 只标的</font>

数据来源：新浪财经 | 自动化生成"""
    send_card("📊 周末消息面概览", overview, "blue")
    time.sleep(0.5)

    # 4. 卡片2-4: 方向
    build_direction_cards(news_stock_map, "利好", "green")
    time.sleep(0.5)
    build_direction_cards(news_stock_map, "利空", "red")
    time.sleep(0.5)
    build_direction_cards(news_stock_map, "中性", "orange")
    time.sleep(0.5)

    # 5. 卡片5: 研判
    if bullish_count > bearish_count * 2:
        sentiment = "消息面偏多，下周开盘情绪乐观"
        color = "green"
    elif bearish_count > bullish_count * 2:
        sentiment = "消息面偏空，下周开盘需谨慎"
        color = "red"
    else:
        sentiment = "消息面多空交织，关注具体板块机会"
        color = "orange"

    judgment = f"""**下周开盘研判**

综合情绪：<font color='{color}'>{sentiment}</font>

**利好/利空分布：**
• 利好消息：{news_with_bullish} 条 → 涉及 {bullish_count} 只标的
• 利空消息：{news_with_bearish} 条 → 涉及 {bearish_count} 只标的
• 中性消息：{news_with_neutral} 条 → 涉及 {neutral_count} 只标的

**操作建议：**
▸ 利好板块：开盘关注龙头股表现
▸ 利空板块：开盘后注意风险，避开弱势股
▸ 利好/利空并存：根据产业链上下游判断传导
▸ 开盘后30分钟：观察量能是否配合

---
数据来源：新浪财经
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_card("🧠 下周研判", judgment, "purple")

    log("周末消息面汇总完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)
