#!/usr/bin/env python3
"""
每日早盘简报自动化 (8:40)
- 交易日早上 8:40 执行
- 收集美股隔夜行情 + 盘前消息面
- 消息面利好/利空/中性映射到 A 股标的
- 发送 4 张飞书分卡片：隔夜外盘 / 盘前消息 / 题材预判 / 早盘研判
"""

import os
import sys
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 共享工具 ──
sys.path.insert(0, str(Path(__file__).parent))
from uzi_common import _http_get_text, send_feishu_card, is_trading_day, make_logger, _theme_heat_safe, _index_quotes

# ── 配置 ──
LOG_FILE = Path("/tmp/uzi_morning_briefing.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000
log = make_logger(LOG_FILE)

# ── 日志 ──
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
def send_card(title, content, template="blue"):
    """包装 uzi_common.send_feishu_card"""
    return send_feishu_card(FEISHU_WEBHOOK, title, content, template, MAX_BYTES, log=log)

# ── 工具函数（使用 uzi_common 带重试的 HTTP 客户端） ──
def _fetch_json(url, timeout=15, headers=None):
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

def _color_tag(direction):
    color_map = {"利好": "red", "利空": "green", "中性": "orange"}
    arrow_map = {"利好": "🔴", "利空": "🟢", "中性": "🟡"}
    return f"<font color='{color_map[direction]}'>{arrow_map[direction]} {direction}</font>"

# ── 交易日判断（uzi_common 已支持 akshare fallback） ──
def is_trading_day_local(date_str):
    """date_str 格式 YYYYMMDD"""
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(date_str, "%Y%m%d")
        return is_trading_day(dt)
    except Exception as e:
        log(f"交易日判断失败: {e}")
        return True

# ── 美股隔夜行情（腾讯 qt 三市场）──
def get_us_overnight():
    """美股隔夜：5 大指数 + 13 科技龙头 + 11 板块 ETF
    数据源：qt.gtimg.cn (uzicommon) 一次拉取，无需 yfinance
    """
    from uzi_common import US_SECTOR_ETFS, US_TECH_LEADERS, _us_quotes_batch, _us_sector_etf_quotes
    from datetime import datetime
    result = {"indices": [], "stocks": [], "sectors": [], "is_closed": False}
    try:
        # 美股周六周日 closed
        result["is_closed"] = datetime.now().weekday() >= 5

        # 1. 美股 5 大指数
        index_codes = ["DJI", "IXIC", "SPX", "VIX", "RUT"]
        index_names = {"DJI": "道琼斯", "IXIC": "纳斯达克100", "SPX": "标普500", "VIX": "VIX恐慌指数", "RUT": "罗素2000"}
        pairs = [(c, "U") for c in index_codes]
        qs = _us_quotes_batch([c for c, _ in pairs])
        for q in qs:
            cn = index_names.get(q.get("code", ""), q.get("name", ""))
            result["indices"].append({"name": cn, "price": q.get("price"), "chg_pct": q.get("change_pct")})
        log(f"美股指数: {len(result['indices'])} 个")

        # 2. 美股 13 科技龙头
        tech_codes = [c for c, _ in US_TECH_LEADERS]
        tech_qs = _us_quotes_batch(tech_codes)
        by_code = {q["code"]: q for q in tech_qs}
        for c, name in US_TECH_LEADERS:
            q = by_code.get(c)
            if q:
                result["stocks"].append({
                    "code": c, "name": name,
                    "price": q.get("price"), "chg_pct": q.get("change_pct"),
                })
        log(f"美股重点个股: {len(result['stocks'])} 只")

        # 3. 美股板块 ETF
        for s in _us_sector_etf_quotes():
            result["sectors"].append({"name": s["name"], "chg_pct": s.get("chg_pct")})
        log(f"美股板块ETF: {len(result['sectors'])} 个")
    except Exception as e:
        log(f"美股行情获取失败: {e}")
    return result

# ── 盘前消息收集 ──
def collect_premarket_news():
    """收集盘前消息：金十数据 + 东财快讯 + 东财全球资讯 + 新浪财经"""
    log("收集盘前消息...")
    news_items = []

    # 金十数据
    try:
        url = "https://www.jin10.com/flash_newest.js"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"})
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'var newest = (\[.*?\]);', raw, re.DOTALL)
        if match:
            items = json.loads(match.group(1))
            for item in items[:20]:
                news_items.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("content", "") or item.get("title", ""),
                    "source": "金十数据",
                    "time": item.get("time", "")
                })
            log(f"金十数据: {min(20, len(items))} 条")
    except Exception as e:
        log(f"金十数据失败: {e}")

    # 东财快讯
    try:
        url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_101_ajaxResult_50_1_.html"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://kuaixun.eastmoney.com/"})
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'var ajaxResult=({.*?});', raw, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            for item in data.get("LivesList", [])[:15]:
                news_items.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("digest", "") or item.get("title", ""),
                    "source": "东财快讯",
                    "time": item.get("showtime", "")
                })
            log(f"东财快讯: {min(15, len(data.get('LivesList', [])))} 条")
    except Exception as e:
        log(f"东财快讯失败: {e}")

    # 东财全球资讯（7x24，替代财联社）
    try:
        from uzi_common import _eastmoney_global_news
        items = _eastmoney_global_news(page_size=15)
        if items:
            for item in items[:15]:
                news_items.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("summary", "") or item.get("title", ""),
                    "source": "东财全球资讯",
                })
            log(f"东财全球资讯: {min(15, len(items))} 条")
    except Exception as e:
        log(f"东财全球资讯失败: {e}")

    # 新浪全球宏观
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=10&page=1"
        data = _fetch_json(url)
        if data and data.get("result", {}).get("data"):
            for item in data["result"]["data"]:
                news_items.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("intro", "") or item.get("title", ""),
                    "source": "新浪全球宏观"
                })
            log(f"新浪全球宏观: {len(data['result']['data'])} 条")
    except Exception as e:
        log(f"新浪全球宏观失败: {e}")

    # 去重
    seen = set()
    unique = []
    for item in news_items:
        key = item.get("title", "")[:30]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    log(f"盘前消息合计: {len(unique)} 条（去重后）")
    return unique

# ── 消息面 → 标的映射 ──
# 格式: (keyword_groups, [(direction, stock, chain, logic)])
STOCK_MAP = [
    # ═══ AI/算力/半导体 ═══
    ((("英伟达",), ("NVDA",), ("英伟达", "财报"), ("英伟达", "AI"), ("英伟达", "合作"), ("英伟达", "发布"), ("GPU", "出口"), ("Blackwell",)), [
        ("利好", "寒武纪(688256)", "AI芯片", "英伟达映射，国产AI芯片替代核心标的"),
        ("利好", "海光信息(688041)", "AI芯片/DCU", "英伟达映射，国产GPU替代"),
        ("利好", "中际旭创(300308)", "光模块", "英伟达GPU配套光模块，算力产业链联动"),
        ("利好", "工业富联(601138)", "AI服务器", "英伟达GPU服务器代工"),
    ]),
    ((("英伟达", "禁令"), ("AI芯片", "禁令"), ("英伟达", "限制"), ("GPU", "限制"), ("英伟达", "禁运"), ("芯片", "限售"), ("光刻机", "限制"), ("半导体", "出口限制"), ("ASML", "限制")), [
        ("利好", "寒武纪(688256)", "AI芯片", "AI芯片禁令加速国产替代"),
        ("利好", "海光信息(688041)", "AI芯片/DCU", "AI芯片禁令加速国产替代"),
        ("利好", "北方华创(002371)", "半导体设备", "国产半导体设备替代"),
        ("利好", "中微公司(688012)", "半导体设备", "国产光刻机替代"),
        ("利好", "中芯国际(688981)", "晶圆代工", "国产晶圆代工替代"),
        ("利空", "中际旭创(300308)", "光模块", "AI芯片禁令利空外销型算力产业链"),
        ("利空", "工业富联(601138)", "AI服务器", "AI芯片禁令利空GPU代工"),
    ]),
    ((("AMD",), ("AMD", "财报"), ("AMD", "芯片"), ("苏姿丰",)), [
        ("利好", "通富微电(002156)", "先进封装", "AMD核心封测合作伙伴"),
        ("利好", "中科曙光(603019)", "服务器", "AMD EPYC服务器合作"),
    ]),
    ((("苹果", "AI"), ("Apple", "AI"), ("苹果", "WWDC"), ("苹果", "发布"), ("Vision Pro",)), [
        ("利好", "立讯精密(002475)", "消费电子代工", "苹果AI布局，核心代工商"),
        ("利好", "蓝思科技(300433)", "消费电子玻璃", "苹果供应链，AI终端创新"),
        ("利好", "长盈精密(300115)", "消费电子精密件", "苹果MR/AR结构件"),
    ]),
    ((("特斯拉", "FSD"), ("特斯拉", "自动驾驶"), ("特斯拉", "Robotaxi"), ("特斯拉", "机器人"), ("Optimus",)), [
        ("利好", "拓普集团(601689)", "特斯拉供应链", "特斯拉机器人/汽车零部件核心供应商"),
        ("利好", "三花智控(002050)", "热管理", "特斯拉热管理供应商"),
        ("利好", "伯特利(603596)", "汽车制动", "智能驾驶线控制动龙头"),
        ("利好", "德赛西威(002920)", "智能驾驶域控", "智能驾驶域控制器龙头"),
    ]),
    ((("特斯拉", "召回"), ("特斯拉", "事故"), ("特斯拉", "下跌"), ("特斯拉", "调查")), [
        ("利空", "拓普集团(601689)", "特斯拉供应链", "特斯拉负面消息短期利空供应链"),
        ("利空", "旭升集团(603305)", "特斯拉供应链", "特斯拉负面消息短期利空"),
    ]),
    ((("美光",), ("美光", "财报"), ("美光", "存储"), ("美光", "涨价"), ("MU",), ("HBM",), ("存储芯片", "涨价")), [
        ("利好", "兆易创新(603986)", "存储芯片", "美光业绩亮眼，存储行业涨价周期"),
        ("利好", "江波龙(301308)", "存储模组", "存储模组受益涨价"),
        ("利好", "北方华创(002371)", "半导体设备", "存储扩产带动设备需求"),
        ("利好", "韦尔股份(603501)", "CIS芯片", "存储涨价周期下半导体板块联动"),
        ("利好", "卓胜微(300782)", "射频芯片", "存储涨价周期下半导体板块联动"),
    ]),
    # ═══ 美联储/宏观 ═══
    ((("美联储", "降息"), ("美联储", "宽松"), ("降息", "预期"), ("通胀", "回落"), ("美联储", "鸽派"), ("降息", "窗口")), [
        ("利好", "山东黄金(600547)", "黄金", "降息周期利好黄金"),
        ("利好", "紫金矿业(601899)", "黄金/铜", "降息周期利好大宗商品"),
        ("利好", "中信证券(600030)", "券商", "降息周期利好券商"),
        ("利好", "万科A(000002)", "地产", "降息周期利好地产"),
        ("利好", "保利发展(600048)", "地产", "降息周期利好地产"),
        ("利好", "招商银行(600036)", "银行", "降息周期估值修复"),
        ("利空", "中国海油(600938)", "石油", "降息预期利空资源股估值"),
    ]),
    # ═══ 油价 ═══
    ((("原油", "大涨"), ("油价", "大涨"), ("原油", "暴涨"), ("油价", "暴涨"), ("原油", "上涨"), ("油价", "上涨"), ("OPEC", "减产"), ("油价", "走高"), ("原油", "走高")), [
        ("利好", "中国海油(600938)", "石油", "油价上涨利好上游油气"),
        ("利好", "中国石油(601857)", "石油", "油价上涨利好上游油气"),
        ("利好", "中国船舶(600150)", "造船", "油价上涨带动船舶需求"),
        ("利空", "东方航空(600115)", "航空", "油价上涨利空航空运营成本"),
        ("利空", "中国国航(601111)", "航空", "油价上涨利空航空运营成本"),
        ("利空", "顺丰控股(002352)", "物流", "油价上涨利空物流成本"),
    ]),
    ((("原油", "大跌"), ("油价", "大跌"), ("原油", "暴跌"), ("油价", "暴跌"), ("原油", "下跌"), ("油价", "下跌"), ("油价", "回落"), ("原油", "回落"), ("OPEC", "增产")), [
        ("利好", "东方航空(600115)", "航空", "油价下跌利好航空运营成本"),
        ("利好", "中国国航(601111)", "航空", "油价下跌利好航空运营成本"),
        ("利好", "顺丰控股(002352)", "物流", "油价下跌利好物流成本"),
        ("利空", "中国海油(600938)", "石油", "油价下跌利空上游油气"),
        ("利空", "中国石油(601857)", "石油", "油价下跌利空上游油气"),
    ]),
    # ═══ 人民币/汇率 ═══
    ((("人民币", "升值"), ("人民币", "汇率"), ("人民币", "上调"), ("美元", "走弱"), ("美元指数", "下跌"), ("美元指数", "跌至"), ("美元指数", "回落"), ("人民币", "走强"), ("人民币", "新高")), [
        ("利好", "东方航空(600115)", "航空", "人民币升值利好航空"),
        ("利好", "中国国航(601111)", "航空", "人民币升值利好航空"),
        ("利好", "晨鸣纸业(000488)", "造纸", "人民币升值利好造纸进口原料"),
        ("利好", "北向资金", "外资", "人民币升值吸引北向资金流入"),
    ]),
    # ═══ 中概股 ═══
    ((("中概股",), ("阿里", "大涨"), ("拼多多", "大涨"), ("中概", "反弹"), ("中概", "上涨"), ("阿里巴巴", "涨"), ("拼多多", "涨"), ("京东", "涨")), [
        ("利好", "阿里巴巴(BABA)", "中概互联", "中概股反弹映射"),
        ("利好", "腾讯控股(00700.HK)", "中概互联", "中概股反弹映射"),
        ("利好", "美团(03690.HK)", "中概互联", "中概股反弹映射"),
    ]),
    # ═══ 政策 ═══
    ((("国常会",), ("国务院", "消费"), ("扩内需",), ("促消费",), ("稳增长",), ("消费", "政策"), ("国务院", "政策")), [
        ("利好", "中信证券(600030)", "券商", "政策利好资本市场"),
        ("利好", "东方财富(300059)", "券商", "政策利好资本市场"),
        ("利好", "贵州茅台(600519)", "白酒", "促消费政策利好消费"),
        ("利好", "伊利股份(600887)", "乳制品", "促消费政策利好消费"),
        ("利好", "海尔智家(600690)", "家电", "促消费政策利好家电"),
    ]),
    # ═══ 中东/地缘 ═══
    ((("中东", "紧张"), ("伊朗",), ("以色列",), ("中东", "冲突"), ("中东", "升级"), ("中东", "战"), ("地缘", "风险")), [
        ("利好", "中国海油(600938)", "石油", "中东冲突推升油价"),
        ("利好", "山东黄金(600547)", "黄金", "避险情绪推升金价"),
        ("利好", "中国船舶(600150)", "造船", "地缘风险推升船舶需求"),
        ("利空", "东方航空(600115)", "航空", "地缘风险利空航空"),
        ("利空", "中国国航(601111)", "航空", "地缘风险利空航空"),
    ]),
    # ═══ 白酒/消费 ═══
    ((("茅台", "涨价"), ("白酒", "涨价"), ("白酒", "提价"), ("五粮液", "涨价"), ("茅台", "上调"), ("茅台", "提价"), ("白酒", "上调"), ("飞天", "涨价"), ("飞天", "提价")), [
        ("利好", "贵州茅台(600519)", "白酒", "茅台出厂价上调，板块提价预期"),
        ("利好", "五粮液(000858)", "白酒", "白酒板块提价预期"),
        ("利好", "泸州老窖(000568)", "白酒", "白酒板块提价预期"),
    ]),
    # ═══ AI 智能体 ═══
    ((("AI", "标准"), ("人工智能", "标准"), ("智能体", "国标"), ("AI", "政策"), ("人工智能", "政策"), ("AI智能体",), ("AI", "智能体")), [
        ("利好", "科大讯飞(002230)", "AI应用/语音平台", "智能体国标发布，AI应用平台受益"),
        ("利好", "拓尔思(300229)", "AI应用/NLP", "智能体交互标准利好NLP技术应用"),
        ("利好", "汉得信息(300170)", "AI应用/企业服务", "Agent工具调用标准利好企业级AI"),
    ]),
    # ═══ 消费电子 ═══
    ((("Vision Pro",), ("苹果", "量产"), ("苹果", "代工"), ("折叠屏",), ("消费电子", "新机"), ("苹果", "新机")), [
        ("利好", "立讯精密(002475)", "消费电子代工", "苹果新品代工"),
        ("利好", "蓝思科技(300433)", "消费电子玻璃", "苹果新品供应链"),
        ("利好", "长盈精密(300115)", "消费电子精密件", "苹果新品结构件"),
    ]),
]

def map_news_to_stocks(news_items):
    """将消息映射到标的 — 每只股票自带方向，同一消息可同时利好/利空不同标的"""
    results = []
    for item in news_items:
        text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
        stock_dedup = {}

        for keyword_groups, stocks in STOCK_MAP:
            matched_kw = None
            for kw_tuple in keyword_groups:
                all_match = True
                for kw in kw_tuple:
                    # 短大写关键词（如 ticker: MU, AMD, NVDA）使用词边界匹配
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

# ── 美股 → A股映射卡片 ──
def build_us_mapping(us_data):
    lines = []
    if us_data.get("is_closed"):
        now = datetime.now()
        wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        lines.append(f"**美股{wd}休市**\n数据为前一交易日收盘，部分API可能暂无数据")
        return "\n".join(lines)

    if us_data.get("indices"):
        lines.append("**三大指数**")
        for idx in us_data["indices"]:
            color = "red" if idx["chg_pct"] >= 0 else "green"
            arrow = "📈" if idx["chg_pct"] >= 0 else "📉"
            lines.append(f"  {arrow} <font color='{color}'>{idx['name']}</font>: {idx['price']:.2f} ({idx['chg_pct']:+.2f}%)")
        lines.append("")

    if us_data.get("sectors"):
        lines.append("**板块ETF**")
        for s in us_data["sectors"][:8]:
            color = "red" if s["chg_pct"] >= 0 else "green"
            lines.append(f"  <font color='{color}'>{s['name']}</font> {s['chg_pct']:+.2f}%")
        lines.append("")

    if us_data.get("stocks"):
        lines.append("**重点个股 → A股映射**")
        us_to_a = {
            "NVDA": ("寒武纪(688256)", "中际旭创(300308)"),
            "AAPL": ("立讯精密(002475)", "蓝思科技(300433)"),
            "TSLA": ("拓普集团(601689)", "三花智控(002050)"),
            "MSFT": ("金山办公(688111)", "科大讯飞(002230)"),
            "MU": ("兆易创新(603986)", "江波龙(301308)"),
            "AMD": ("通富微电(002156)", "中科曙光(603019)"),
            "ASML": ("北方华创(002371)", "中微公司(688012)"),
            "GOOGL": ("中文在线(300364)", "蓝色光标(300058)"),
            "META": ("蓝色光标(300058)", "易点天下(301171)"),
            "AMZN": ("跨境通(002640)", "焦点科技(002315)"),
            "BABA": ("阿里巴巴(BABA)", "腾讯控股(00700.HK)"),
            "PDD": ("阿里巴巴(BABA)", "京东(JD)"),
            "JD": ("京东(JD)", "阿里巴巴(BABA)"),
            "NIO": ("比亚迪(002594)", "长城汽车(601633)"),
            "XPEV": ("比亚迪(002594)", "长城汽车(601633)"),
            "BIDU": ("中文在线(300364)", "三六零(601360)"),
        }
        for stock in us_data["stocks"][:10]:
            code = stock["code"]
            a_stocks = us_to_a.get(code, [])
            if a_stocks:
                color = "red" if stock["chg_pct"] >= 0 else "green"
                arrow = "📈" if stock["chg_pct"] >= 0 else "📉"
                lines.append(f"  {arrow} <font color='{color}'>{stock['name']}({code})</font> {stock['chg_pct']:+.2f}% → {', '.join(a_stocks)}")

    return "\n".join(lines) if lines else "美股数据获取失败"

# ── 方向卡片（按股票方向分组） ──
def build_direction_cards(news_stock_map, direction, template_color):
    """按股票方向分组构建卡片 — 同一消息可能同时出现在多个方向卡片中"""
    matching = []
    for item, stocks in news_stock_map:
        dir_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == direction]
        if dir_stocks:
            matching.append((item, dir_stocks))

    if not matching:
        title_map = {"利好": "🔴 利好方向", "利空": "🟢 利空方向", "中性": "🟡 中性 / 待观察"}
        send_card(title_map[direction], "暂无该方向消息", template_color)
        return

    title_map = {"利好": "🔴 利好方向", "利空": "🟢 利空方向", "中性": "🟡 中性 / 待观察"}
    section_title = title_map.get(direction, direction)

    lines = []
    for idx, (item, stocks) in enumerate(matching):
        title = item.get("title", "")[:80]
        if idx > 0:
            lines.append("")
        lines.append(f"**{idx + 1}. {title}**")
        for stock_name, chain, logic, d, kw in stocks:
            color_tag = _color_tag(d)
            lines.append(f"  {color_tag} **{stock_name}**  {chain} · {logic}")

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
    """分半发送卡片内容"""
    lines = []
    for idx, (item, stocks) in enumerate(items):
        title = item.get("title", "")[:80]
        if idx > 0:
            lines.append("")
        lines.append(f"**{start_num + idx}. {title}**")
        for stock_name, chain, logic, d, kw in stocks:
            lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
    send_card(f"{base_title}（续）", "\n".join(lines), template_color)

# ── 卡片1: 隔夜外盘（合并早盘概览 + 美股映射） ──
def card_overview(us_data, news_items, all_stocks, bullish_count, bearish_count, neutral_count):
    """合并原来的 Card 1 (早盘概览) + Card 2 (美股映射) 为一张卡片"""
    # ── 美股情绪判断 ──
    major_indices = [idx for idx in us_data.get("indices", []) if idx["name"] in ("道琼斯", "纳斯达克100", "标普500")]
    if major_indices:
        avg_chg = sum(idx["chg_pct"] for idx in major_indices) / len(major_indices)
        if avg_chg > 0.3:
            us_verdict = "美股隔夜全面收涨，情绪偏暖，利好A股开盘"
        elif avg_chg < -0.3:
            us_verdict = "美股隔夜全面收跌，情绪偏冷，A股开盘承压"
        else:
            us_verdict = "美股隔夜窄幅震荡，情绪中性，A股开盘震荡"
    elif us_data.get("is_closed"):
        us_verdict = "美股周末休市，关注上周五收盘数据"
    else:
        us_verdict = "美股行情数据获取中..."

    # ── 日期 ──
    today = datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[today.weekday()]
    date_display = today.strftime("%Y年%m月%d日") + f" {wd}"

    # ── 构建内容 ──
    lines = []
    lines.append(f"**{date_display} 早盘简报**")
    lines.append("")
    lines.append(f"**{us_verdict}**")
    lines.append("")

    # 盘前消息统计
    lines.append(f"盘前消息：{len(news_items)} 条 | 关联标的：{len(set(s for s, _, _, _, _ in all_stocks))} 只")
    lines.append(f"<font color='red'>▸ 利好：{bullish_count} 只标的</font>")
    lines.append(f"<font color='green'>▸ 利空：{bearish_count} 只标的</font>")
    lines.append(f"<font color='orange'>▸ 中性：{neutral_count} 只标的</font>")
    lines.append("")

    # ── 美股三大指数 ──
    if us_data.get("indices"):
        lines.append("**━━━ 美股三大指数 ━━━**")
        for idx in us_data["indices"]:
            if idx["name"] in ("道琼斯", "纳斯达克100", "标普500"):
                color = "red" if idx["chg_pct"] >= 0 else "green"
                arrow = "📈" if idx["chg_pct"] >= 0 else "📉"
                if idx["price"] is not None and idx["chg_pct"] is not None:
                    lines.append(f"  {arrow} <font color='{color}'>{idx['name']}</font>: {idx['price']:.2f} ({idx['chg_pct']:+.2f}%)")
                elif idx["chg_pct"] is not None:
                    lines.append(f"  {arrow} <font color='{color}'>{idx['name']}</font>: {idx['chg_pct']:+.2f}%")
        lines.append("")

    # ── 美股科技龙头 → A股映射 ──
    us_to_a = {
        "NVDA": ("寒武纪(688256)", "中际旭创(300308)"),
        "AAPL": ("立讯精密(002475)", "蓝思科技(300433)"),
        "TSLA": ("拓普集团(601689)", "三花智控(002050)"),
        "MSFT": ("金山办公(688111)", "科大讯飞(002230)"),
        "MU": ("兆易创新(603986)", "江波龙(301308)"),
        "AMD": ("通富微电(002156)", "中科曙光(603019)"),
        "ASML": ("北方华创(002371)", "中微公司(688012)"),
        "GOOGL": ("中文在线(300364)", "蓝色光标(300058)"),
        "META": ("蓝色光标(300058)", "易点天下(301171)"),
        "AMZN": ("跨境通(002640)", "焦点科技(002315)"),
        "BABA": ("阿里巴巴(BABA)", "腾讯控股(00700.HK)"),
        "PDD": ("阿里巴巴(BABA)", "京东(JD)"),
        "JD": ("京东(JD)", "阿里巴巴(BABA)"),
        "NIO": ("比亚迪(002594)", "长城汽车(601633)"),
        "XPEV": ("比亚迪(002594)", "长城汽车(601633)"),
        "BIDU": ("中文在线(300364)", "三六零(601360)"),
    }
    if us_data.get("stocks"):
        lines.append("**━━━ 科技龙头 → A股映射 ━━━**")
        for stock in us_data["stocks"][:10]:
            code = stock["code"]
            a_stocks = us_to_a.get(code, [])
            if a_stocks and stock["chg_pct"] is not None:
                color = "red" if stock["chg_pct"] >= 0 else "green"
                arrow = "📈" if stock["chg_pct"] >= 0 else "📉"
                lines.append(f"  {arrow} <font color='{color}'>{stock['name']}({code})</font> {stock['chg_pct']:+.2f}% → {', '.join(a_stocks)}")
        lines.append("")

    # ── 板块ETF ──
    if us_data.get("sectors"):
        lines.append("**━━━ 板块ETF ━━━**")
        for s in us_data["sectors"][:8]:
            if s["chg_pct"] is not None:
                color = "red" if s["chg_pct"] >= 0 else "green"
                lines.append(f"  <font color='{color}'>{s['name']}</font> {s['chg_pct']:+.2f}%")
        lines.append("")

    lines.append("数据来源：金十数据 | 东财快讯 | 东财全球资讯 | 新浪全球宏观 | 自动化生成")
    content = "\n".join(lines)
    send_card("🌍 隔夜外盘", content, "blue")


# ── 卡片2: 盘前消息（合并利好/利空/中性三张卡片为一张） ──
def card_news(news_stock_map):
    """合并原来的 3 张方向卡片为一张盘前消息卡片"""
    # 分别收集利好/利空/中性
    bullish = []
    bearish = []
    neutral = []
    for item, stocks in news_stock_map:
        bull_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == "利好"]
        bear_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == "利空"]
        neut_stocks = [(s, c, l, d, k) for s, c, l, d, k in stocks if d == "中性"]
        if bull_stocks:
            bullish.append((item, bull_stocks))
        if bear_stocks:
            bearish.append((item, bear_stocks))
        if neut_stocks:
            neutral.append((item, neut_stocks))

    lines = []

    # ── 利好消息 ──
    lines.append("**━━━ 🟢 利好消息及标的 ━━━**")
    if bullish:
        for idx, (item, stocks) in enumerate(bullish):
            title = item.get("title", "")[:80]
            if idx > 0:
                lines.append("")
            lines.append(f"**{idx + 1}. {title}**")
            for stock_name, chain, logic, d, kw in stocks:
                lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
    else:
        lines.append("暂无利好消息")
    lines.append("")

    # ── 利空消息 ──
    lines.append("**━━━ 🔴 利空消息及标的 ━━━**")
    if bearish:
        for idx, (item, stocks) in enumerate(bearish):
            title = item.get("title", "")[:80]
            if idx > 0:
                lines.append("")
            lines.append(f"**{idx + 1}. {title}**")
            for stock_name, chain, logic, d, kw in stocks:
                lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
    else:
        lines.append("暂无利空消息")
    lines.append("")

    # ── 中性消息 ──
    lines.append("**━━━ 🟡 中性 / 待观察 ━━━**")
    if neutral:
        for idx, (item, stocks) in enumerate(neutral):
            title = item.get("title", "")[:80]
            if idx > 0:
                lines.append("")
            lines.append(f"**{idx + 1}. {title}**")
            for stock_name, chain, logic, d, kw in stocks:
                lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
    else:
        lines.append("暂无中性消息")

    content = "\n".join(lines)

    # 检查长度，超长则分两半发送
    test_body = json.dumps({"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "📰 盘前消息"}, "template": "green"}, "elements": [{"tag": "markdown", "content": content}]}}, ensure_ascii=False).encode("utf-8")
    if len(test_body) > MAX_BYTES:
        # 分两半：先发利好+利空前半，再发利空后半+中性
        # 按总条目数平分
        total_entries = len(bullish) + len(bearish) + len(neutral)
        if total_entries == 0:
            send_card("📰 盘前消息", "暂无消息", "green")
            return

        # 尝试前半部分：利好 + 利空
        half_lines = []
        half_lines.append("**━━━ 🟢 利好消息及标的 ━━━**")
        if bullish:
            for idx, (item, stocks) in enumerate(bullish):
                title = item.get("title", "")[:80]
                if idx > 0:
                    half_lines.append("")
                half_lines.append(f"**{idx + 1}. {title}**")
                for stock_name, chain, logic, d, kw in stocks:
                    half_lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
        else:
            half_lines.append("暂无利好消息")

        half_content = "\n".join(half_lines)
        send_card(f"📰 盘前消息（上）", half_content, "green")
        time.sleep(0.3)

        # 后半部分：利空 + 中性
        half2_lines = []
        half2_lines.append("**━━━ 🔴 利空消息及标的 ━━━**")
        if bearish:
            for idx, (item, stocks) in enumerate(bearish):
                title = item.get("title", "")[:80]
                if idx > 0:
                    half2_lines.append("")
                half2_lines.append(f"**{idx + 1}. {title}**")
                for stock_name, chain, logic, d, kw in stocks:
                    half2_lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
        else:
            half2_lines.append("暂无利空消息")
        half2_lines.append("")
        half2_lines.append("**━━━ 🟡 中性 / 待观察 ━━━**")
        if neutral:
            for idx, (item, stocks) in enumerate(neutral):
                title = item.get("title", "")[:80]
                if idx > 0:
                    half2_lines.append("")
                half2_lines.append(f"**{idx + 1}. {title}**")
                for stock_name, chain, logic, d, kw in stocks:
                    half2_lines.append(f"  {_color_tag(d)} **{stock_name}**  {chain} · {logic}")
        else:
            half2_lines.append("暂无中性消息")

        half2_content = "\n".join(half2_lines)
        send_card("📰 盘前消息（下）", half2_content, "green")
    else:
        send_card("📰 盘前消息", content, "green")


# ── 卡片3: 题材预判（新增） ──
def card_theme_preview(news_stock_map, us_data):
    """新增题材预判卡片：消息面热点方向 + 昨日涨停方向 + 连板题材 + 共振判断"""
    lines = []

    # ── 1. 消息面热点方向 ──
    lines.append("**━━━ 📡 消息面热点方向 ━━━**")
    # 从利好消息中提取题材关键词（chain 字段）
    theme_keywords = {}
    for item, stocks in news_stock_map:
        for stock_name, chain, logic, d, kw in stocks:
            if d == "利好":
                if chain not in theme_keywords:
                    theme_keywords[chain] = []
                theme_keywords[chain].append(stock_name)

    if theme_keywords:
        # 按股票数量排序，取前 5 个热门题材
        sorted_themes = sorted(theme_keywords.items(), key=lambda x: -len(x[1]))
        for chain, stocks in sorted_themes[:5]:
            stock_str = "、".join(stocks[:3])
            if len(stocks) > 3:
                stock_str += f" 等{len(stocks)}只"
            lines.append(f"  🔥 **{chain}**：{stock_str}")
    else:
        lines.append("  暂无明确消息面热点")
    lines.append("")

    # ── 2. 同花顺概念题材热度 ──
    lines.append("**━━━ 📈 概念题材热度（昨日）━━━**")
    try:
        from datetime import datetime, timedelta
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y%m%d")
        heat = _theme_heat_safe(date_str)
        concepts = heat.get("concepts")
        if concepts:
            for tag, cnt, stocks in concepts[:8]:
                stock_names = [s.get("name", "") for s in stocks[:3]]
                stock_str = "、".join(stock_names)
                if len(stocks) > 3:
                    stock_str += f" 等{cnt}只"
                lines.append(f"  📊 **{tag}**（{cnt}只）：{stock_str}")
        else:
            lines.append("  同花顺热点数据暂无")
    except Exception as e:
        log(f"概念题材获取失败: {e}")
        lines.append("  题材数据获取暂不可用")
    lines.append("")

    # ── 3. 连板持续题材 ──
    lines.append("**━━━ 🔗 连板持续题材 ━━━**")
    try:
        strong_themes = heat.get("strong_themes", {})
        if strong_themes:
            sorted_strong = sorted(strong_themes.items(), key=lambda x: -x[1]["count"])
            for ind, info in sorted_strong[:5]:
                stock_names = [s.get("name", "") for s in info["stocks"][:3]]
                stock_str = "、".join(stock_names)
                if len(info["stocks"]) > 3:
                    stock_str += f" 等{info['count']}只"
                lines.append(f"  🔗 **{ind}**（{info['count']}只连板）：{stock_str}")
        else:
            lines.append("  暂无连板题材数据")
    except Exception as e:
        log(f"连板题材获取失败: {e}")
        lines.append("  连板数据获取暂不可用")
    lines.append("")

    # ── 4. 题材延续性判断 ──
    lines.append("**━━━ 🎯 题材延续性判断 ━━━**")
    try:
        # 消息面题材关键词集合
        news_themes = set(theme_keywords.keys())
        # 连板题材集合
        strong_industries = set(strong_themes.keys()) if strong_themes else set()

        # 共振判断：消息面热点与概念题材/连板题材是否重叠
        resonance_news_concept = set()
        if concepts:
            concept_tags = set(tag for tag, _, _ in concepts)
            for news_topic in news_themes:
                for concept in concept_tags:
                    if news_topic in concept or concept in news_topic:
                        resonance_news_concept.add(news_topic)
                        break
        resonance_news_strong = news_themes & strong_industries  # 消息面 ∩ 连板
        resonance_all = resonance_news_concept & strong_industries  # 三者共振

        if resonance_all:
            lines.append(f"  ✅ **强共振**：{', '.join(resonance_all)} 消息面+概念题材+连板三重共振，题材延续性极强")
        elif resonance_news_strong:
            lines.append(f"  🔥 **消息面+连板共振**：{', '.join(resonance_news_strong)} 消息面与连板题材共振，题材有望延续")
        elif resonance_news_concept:
            lines.append(f"  📊 **消息面+概念题材共振**：{', '.join(list(resonance_news_concept)[:5])} 消息面与昨日概念热度共振")
        else:
            lines.append("  ⚠️ 消息面热点与昨日概念/连板方向暂无共振，关注新题材启动")

        # 美股情绪对题材的影响
        major_indices = [idx for idx in us_data.get("indices", []) if idx["name"] in ("道琼斯", "纳斯达克100", "标普500")]
        if major_indices:
            avg_chg = sum(idx["chg_pct"] for idx in major_indices) / len(major_indices)
            if avg_chg > 0.5:
                lines.append("  💡 美股科技领涨，关注AI/半导体/消费电子题材映射")
            elif avg_chg < -0.5:
                lines.append("  💡 美股科技承压，题材追高需谨慎，关注防御板块（黄金/高股息）")
    except Exception as e:
        log(f"题材预判异常: {e}")
        lines.append("  题材预判暂不可用")

    content = "\n".join(lines)
    send_card("🎯 题材预判", content, "orange")


# ── 主流程 ──
def main():
    log("=" * 60)
    log("每日早盘简报启动")

    today = datetime.now()
    today_str = today.strftime("%Y%m%d")

    # 1. 交易日判断
    is_td = is_trading_day(today_str)
    log(f"{today_str} 是否为交易日: {is_td}")
    if not is_td:
        log("非交易日，跳过")
        return

    # 2. 美股隔夜
    us_data = get_us_overnight()
    time.sleep(0.5)

    # 3. 盘前消息
    news_items = collect_premarket_news()

    # 4. 消息映射
    news_stock_map = map_news_to_stocks(news_items)

    all_stocks = []
    for item, stocks in news_stock_map:
        all_stocks.extend(stocks)

    bullish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利好")
    bearish_count = sum(1 for _, _, _, d, _ in all_stocks if d == "利空")
    neutral_count = sum(1 for _, _, _, d, _ in all_stocks if d == "中性")

    log(f"统计: 利好{bullish_count}标 / 利空{bearish_count}标 / 中性{neutral_count}标")

    # 5. 卡片1: 隔夜外盘 (blue) — 合并原来的早盘概览 + 美股映射
    card_overview(us_data, news_items, all_stocks, bullish_count, bearish_count, neutral_count)
    time.sleep(0.5)

    # 6. 卡片2: 盘前消息 (green) — 合并原来的利好/利空/中性三张卡片
    card_news(news_stock_map)
    time.sleep(0.5)

    # 7. 卡片3: 题材预判 (orange) — 新增
    card_theme_preview(news_stock_map, us_data)
    time.sleep(0.5)

    # 8. 卡片4: 早盘研判 (purple)
    # 美股情绪判断
    major_indices = [idx for idx in us_data.get("indices", []) if idx["name"] in ("道琼斯", "纳斯达克100", "标普500")]
    if major_indices:
        avg_chg = sum(idx["chg_pct"] for idx in major_indices) / len(major_indices)
        if avg_chg > 0.3:
            us_verdict = "美股隔夜收涨，情绪偏暖"
        elif avg_chg < -0.3:
            us_verdict = "美股隔夜收跌，情绪偏冷"
        else:
            us_verdict = "美股隔夜窄幅震荡，情绪中性"
    elif us_data.get("is_closed"):
        us_verdict = "美股周末休市"
    else:
        us_verdict = "美股数据待获取"

    # 提取题材方向提示
    theme_direction = ""
    try:
        theme_keywords = {}
        for item, stocks in news_stock_map:
            for stock_name, chain, logic, d, kw in stocks:
                if d == "利好":
                    if chain not in theme_keywords:
                        theme_keywords[chain] = 0
                    theme_keywords[chain] += 1
        if theme_keywords:
            top_themes = sorted(theme_keywords.items(), key=lambda x: -x[1])[:3]
            theme_direction = "、".join(f"{t}({c}只)" for t, c in top_themes)
            theme_direction = f"消息面热点题材：{theme_direction}\n\n"
    except:
        pass

    judgment = f"""**今日关注要点：**

**外围：**
• 隔夜美股三大指数走势
• 美债收益率变化
• 大宗商品（原油/黄金/铜）走势
• 中概股表现

**国内：**
• 盘前消息面情绪
• 北向资金动向
• 昨日涨停板晋级情况

**{theme_direction}风格判断：**
▸ 美股情绪传导：{us_verdict}
▸ A股开盘30分钟跟随美股方向
▸ 消息面主导：利好/利空方向决定日内板块轮动
▸ 关注开盘后资金流向确认

---
数据来源：金十数据、东财快讯、新浪财经、东方财富
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_card("🧠 早盘研判", judgment, "purple")

    log("每日早盘简报完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)