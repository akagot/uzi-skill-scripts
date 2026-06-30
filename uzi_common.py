#!/usr/bin/env python3
"""
uzi_common.py - 5 个脚本共享的工具函数（v2.0）
- HTTP 重试客户端
- 腾讯 qt.gtimg.cn 批量报价（A/H/U 三市场）
- 新浪 hq.sinajs.cn 批量报价（A/H/U 三市场）
- 上交所 yunhq.sse.com.cn:32041 单股快照
- 交易日判断（akshare 可选）
- 飞书卡片发送

数据源设计参考 UZI-Skill lib/providers/direct_http_provider.py v2.10.3
- akshare 缺失时用 etnet/新浪/etnet 兜底
- 腾讯 qt 国内稳定，国内外都通
"""

import json
import time
import urllib.request
import urllib.error
import urllib.parse
import socket
import re
import sys
import os
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# HTTP 客户端（带重试 + User-Agent）
# ═══════════════════════════════════════════════════════════════
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

def _http_get_bytes(url, timeout=15, retries=3, extra_headers=None):
    headers = dict(DEFAULT_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read()
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.6 * (2 ** attempt))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.6 * (2 ** attempt))
    print(f"[HTTP FAIL] {url[:80]}: {last_err}", file=sys.stderr)
    return None

def _http_get_text(url, timeout=15, retries=3, encoding="utf-8", extra_headers=None):
    raw = _http_get_bytes(url, timeout=timeout, retries=retries, extra_headers=extra_headers)
    if raw is None:
        return None
    for enc in [encoding, "gbk", "utf-8"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")

def _http_get_json(url, timeout=15, retries=3, extra_headers=None):
    text = _http_get_text(url, timeout=timeout, retries=retries, extra_headers=extra_headers)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[JSON FAIL] {url[:80]}: {e}", file=sys.stderr)
        return None

# ═══════════════════════════════════════════════════════════════
# 分级缓存系统（参考 UZI-Skill lib/cache.py）
# ═══════════════════════════════════════════════════════════════
_CACHE_DIR = Path("/tmp/uzi_cache")
_TTL_REALTIME = 60           # 1分钟：实时价格
_TTL_INTRADAY = 300          # 5分钟：K线、资金流
_TTL_DAILY = 7200            # 2小时：龙虎榜、北向、融资融券
_TTL_STATIC = 86400          # 24小时：行业分类、财报

def _cache_key(*parts):
    """生成缓存键：对参数拼接后取MD5前12位"""
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def _cache_get(*parts):
    """读取缓存，过期返回 None"""
    key = _cache_key(*parts)
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("_ts", 0) > data.get("_ttl", _TTL_INTRADAY):
            return None
        return data.get("_val")
    except Exception:
        return None

def _cache_set(value, ttl=_TTL_INTRADAY, *parts):
    """写入缓存"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(*parts)
    path = _CACHE_DIR / f"{key}.json"
    try:
        path.write_text(json.dumps({"_ts": time.time(), "_ttl": ttl, "_val": value}, ensure_ascii=False))
    except Exception:
        pass

def cached(ttl=_TTL_INTRADAY):
    """装饰器：自动缓存函数结果。缓存键 = 函数名 + 参数"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            cache_parts = [fn.__name__] + list(args) + sorted(kwargs.items())
            val = _cache_get(*cache_parts)
            if val is not None:
                return val
            val = fn(*args, **kwargs)
            if val is not None:
                _cache_set(val, ttl, *cache_parts)
            return val
        return wrapper
    return decorator

# ═══════════════════════════════════════════════════════════════
# 飞书推送
# ═══════════════════════════════════════════════════════════════
def send_feishu_card(webhook, title, content, template="blue", max_bytes=18000, log=None):
    try:
        def make_body(text):
            return json.dumps({
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
                    "elements": [{"tag": "markdown", "content": text}]
                }
            }, ensure_ascii=False).encode("utf-8")

        body = make_body(content[:max_bytes])
        if len(body) > max_bytes:
            text = content
            while len(make_body(text)) > max_bytes and len(text) > 100:
                text = text[:int(len(text) * 0.9)] + "\n\n(内容过长，已截断)"
            body = make_body(text)

        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    webhook, data=body,
                    headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": DEFAULT_HEADERS["User-Agent"]}
                )
                resp = urllib.request.urlopen(req, timeout=30)
                resp.read()
                if log:
                    log(f"飞书推送成功: {title}")
                else:
                    print(f"[FEISHU OK] {title}")
                return True
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                else:
                    if log:
                        log(f"飞书推送失败: {title} -> {e}")
                    else:
                        print(f"[FEISHU FAIL] {title}: {e}", file=sys.stderr)
                    return False
        return False
    except Exception as e:
        if log:
            log(f"飞书推送异常: {title} -> {e}")
        else:
            print(f"[FEISHU ERR] {title}: {e}", file=sys.stderr)
        return False

# ═══════════════════════════════════════════════════════════════
# 腾讯 qt.gtimg.cn · 实时报价（A/H/U 三市场）
# 参考 UZI-Skill direct_http_provider.py
# ═══════════════════════════════════════════════════════════════
def _qt_code_for(code, market):
    """把统一代码转成腾讯 qt 格式"""
    code = str(code).strip()
    if market == "A":
        if code.startswith(("6", "9", "5", "1")):
            return f"sh{code}"
        return f"sz{code}"
    elif market == "H":
        return f"hk{code.zfill(5)}"
    elif market == "U":
        # 腾讯美股：usAAPL（不带后缀）· 腾讯自动加交易所后缀
        code_upper = code.upper().replace(".OQ", "").replace(".N", "").replace(".O", "")
        return f"us{code_upper}"
    return code

def _parse_qt_line(line):
    """解析单行 v_xxx='1~name~code~...'
    返回 dict: {name, code, price, prev_close, open, change, change_pct, high, low, volume, amount, market}
    """
    m = re.search(r'v_(\w+)="([^"]+)"', line)
    if not m:
        return None
    full = m.group(1)
    parts = m.group(2).split("~")
    if len(parts) < 6:
        return None
    try:
        # 判断市场
        if full.startswith("us"):
            market = "U"
        elif full.startswith("hk"):
            market = "H"
        else:
            market = "A"
        result = {
            "full": full,
            "market": market,
            "name": parts[1],
            "code": parts[2].split(".")[0],  # 去掉 .OQ/.N 后缀
            "price": float(parts[3]) if parts[3] else None,
            "prev_close": float(parts[4]) if parts[4] else None,
            "open": float(parts[5]) if parts[5] else None,
        }
        # A 股字段：第 6=成交量(手) 7=外盘 8=内盘 9=买一 ... 32=涨跌额 33=涨跌幅 38=最高 39=最低
        # 港股/美股字段略不同，下面做兼容
        if market == "A":
            result.update({
                "volume": float(parts[6]) if len(parts) > 6 and parts[6] else None,  # 手
                "change": float(parts[31]) if len(parts) > 31 and parts[31] else None,
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else None,
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else None,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else None,
                "amount": float(parts[37]) if len(parts) > 37 and parts[37] else None,
            })
        elif market == "H":
            # 港股字段: 0=市场 1=名字 2=代码 3=现价 4=昨收 5=今开 6=最高 7=最低 9=涨跌额 31=涨跌幅 ...
            result.update({
                "high": float(parts[6]) if len(parts) > 6 and parts[6] else None,
                "low": float(parts[7]) if len(parts) > 7 and parts[7] else None,
                "change": float(parts[9]) if len(parts) > 9 and parts[9] else None,
                "change_pct": float(parts[31]) if len(parts) > 31 and parts[31] else None,
            })
        else:  # U
            # 美股字段: 0=市场 1=名字 2=代码 3=现价 4=昨收 5=今开 6=成交量 ... 30=时间 31=涨跌额 32=涨跌幅 33=最高 34=最低
            result.update({
                "change": float(parts[31]) if len(parts) > 31 and parts[31] else None,
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else None,
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else None,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else None,
            })
        return result
    except (ValueError, IndexError):
        return None

def fetch_qt_quotes(codes_with_markets, timeout=15, retries=2):
    """批量获取腾讯实时报价
    codes_with_markets: [(code, market), ...]  e.g. [('000001', 'A'), ('AAPL', 'U')]
    返回 list[dict] · 失败或空缺的不包含
    """
    if not codes_with_markets:
        return []
    qt_codes = [_qt_code_for(c, m) for c, m in codes_with_markets]
    results = []
    # 一次最多 60 个
    for i in range(0, len(qt_codes), 60):
        batch = qt_codes[i:i+60]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        text = _http_get_text(url, timeout=timeout, retries=retries, encoding="gbk")
        if text is None:
            continue
        for line in text.splitlines():
            r = _parse_qt_line(line)
            if r:
                results.append(r)
    return results

def fetch_qt_dict(codes_with_markets, **kwargs):
    """批量拉取并返回 {code: result} 字典
    同一 code 多次传入时取最后一次
    """
    rows = fetch_qt_quotes(codes_with_markets, **kwargs)
    by_code = {}
    for r in rows:
        by_code[(r["code"], r["market"])] = r
    return by_code

# ═══════════════════════════════════════════════════════════════
# 上交所 yunhq.sse.com.cn:32041 · 单股快照
# ═══════════════════════════════════════════════════════════════
def fetch_sse_snap(code, timeout=10, retries=2):
    """上交所单股快照
    snap 字段: [名称, 现价, 昨收, 最高, 最低, 开盘, 涨跌额, 涨跌幅, 成交量, 成交额, ...]
    """
    url = f"http://yunhq.sse.com.cn:32041/v1/sh1/snap/{code}"
    text = _http_get_text(url, timeout=timeout, retries=retries, extra_headers={"Referer": "http://yunhq.sse.com.cn/"})
    if not text:
        return None
    try:
        data = json.loads(text)
        if "snap" not in data or len(data["snap"]) < 10:
            return None
        snap = data["snap"]
        return {
            "code": code,
            "name": snap[0],
            "price": float(snap[1]) if snap[1] else None,
            "prev_close": float(snap[2]) if snap[2] else None,
            "high": float(snap[3]) if snap[3] else None,
            "low": float(snap[4]) if snap[4] else None,
            "open": float(snap[5]) if snap[5] else None,
            "change": float(snap[6]) if snap[6] else None,
            "change_pct": float(snap[7]) if snap[7] else None,
            "volume": int(float(snap[8])) if snap[8] else None,
            "amount": float(snap[9]) if snap[9] else None,
        }
    except (json.JSONDecodeError, ValueError, IndexError):
        return None

# ═══════════════════════════════════════════════════════════════
# 交易日判断
# ═══════════════════════════════════════════════════════════════
def is_trading_day(date_obj=None):
    """判断 date_obj 是否为 A 股交易日
    date_obj 可以是 datetime 对象 或 'YYYYMMDD' 字符串 或 None（=今天）
    优先用 akshare，缺失时退回到简单的周末判断
    """
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, "%Y%m%d")
        except ValueError:
            try:
                date_obj = datetime.strptime(date_obj, "%Y-%m-%d")
            except ValueError:
                date_obj = datetime.now()
    if date_obj is None:
        date_obj = datetime.now()
    if date_obj.weekday() >= 5:
        return False
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is None or len(df) == 0:
            return True
        trade_dates = set(str(d)[:10] for d in df["trade_date"].astype(str))
        return date_obj.strftime("%Y-%m-%d") in trade_dates
    except ImportError:
        return True
    except Exception:
        return True

# ═══════════════════════════════════════════════════════════════
# 涨跌幅颜色标签 + 涨跌幅文本
# ═══════════════════════════════════════════════════════════════
def color_chg(chg):
    if chg is None:
        return "<font color='grey'>--</font>"
    color = "red" if chg >= 0 else "green"
    arrow = "📈" if chg >= 0 else "📉"
    return f"{arrow} <font color='{color}'>{chg:+.2f}%</font>"

def fmt_price(price, decimals=2):
    if price is None:
        return "--"
    return f"{price:,.{decimals}f}"

def fmt_amount_yi(amount):
    """成交额转亿元"""
    if amount is None:
        return "--"
    return f"{amount/1e8:,.1f}亿"

# ═══════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════
def make_logger(log_file):
    def log(msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    return log

# ═══════════════════════════════════════════════════════════════
# 美股/港股代码表（常用 ETF/标的）
# ═══════════════════════════════════════════════════════════════
US_SECTOR_ETFS = [
    ("QQQ", "纳指100ETF"),
    ("SPY", "标普500ETF"),
    ("SMH", "半导体ETF"),
    ("XLK", "科技行业"),
    ("XLE", "能源行业"),
    ("XLF", "金融行业"),
    ("XLV", "医疗保健"),
    ("XLY", "可选消费"),
    ("XLP", "必选消费"),
    ("XLI", "工业"),
    ("XLB", "原材料"),
    ("XLU", "公用事业"),
    ("DIA", "道指ETF"),
    ("IWM", "罗素2000"),
    ("ARKK", "ARK创新"),
    ("GLD", "黄金ETF"),
    ("USO", "原油ETF"),
    ("TLT", "20年国债"),
    ("HYG", "高收益债"),
]

US_TECH_LEADERS = [
    ("AAPL", "苹果"), ("MSFT", "微软"), ("GOOGL", "谷歌"),
    ("META", "Meta"), ("AMZN", "亚马逊"), ("NVDA", "英伟达"),
    ("TSLA", "特斯拉"), ("AMD", "超微"), ("MU", "美光"),
    ("AVGO", "博通"), ("ORCL", "甲骨文"), ("CRM", "Salesforce"),
    ("INTC", "英特尔"), ("CSCO", "思科"),
]

HK_TECH_LEADERS = [
    ("00700", "腾讯控股"), ("09988", "阿里巴巴"),
    ("03690", "美团"), ("01024", "快手"),
    ("09618", "京东集团"), ("09999", "网易"),
    ("01810", "小米"), ("02318", "中国平安"),
    ("00939", "建设银行"), ("01398", "工商银行"),
]

A_SHARE_INDICES = [
    ("sh000001", "上证指数"), ("sz399001", "深证成指"),
    ("sz399006", "创业板指"), ("sh000688", "科创50"),
    ("sh000300", "沪深300"), ("sh000016", "上证50"),
    ("sz399905", "中证500"),
]

A_SHARE_SECTOR_ETFS = [
    # 行业 ETF
    ("512010", "医药ETF"), ("512170", "医疗ETF"),
    ("512760", "芯片ETF"), ("512480", "半导体ETF"),
    ("512660", "军工ETF"), ("512290", "生物医药ETF"),
    ("512200", "房地产ETF"), ("512800", "银行ETF"),
    ("512000", "券商ETF"), ("512880", "证券ETF"),
    ("512980", "传媒ETF"), ("512580", "环保ETF"),
    ("512690", "酒ETF"), ("512670", "国防ETF"),
    ("512400", "有色ETF"), ("512930", "人工智能ETF"),
    ("515030", "新能源车ETF"), ("515700", "智能汽车ETF"),
    ("515980", "人工智能ETF"), ("515790", "光伏ETF"),
    ("515050", "5GETF"), ("515080", "中证红利"),
    ("159915", "创业板ETF"), ("510050", "上证50ETF"),
    ("510300", "沪深300ETF"), ("510500", "中证500ETF"),
    ("510880", "红利ETF"), ("518880", "黄金ETF"),
    ("159949", "创业板50ETF"),
]

A_SHARE_BLUE_CHIPS = [
    # 大盘蓝筹/权重股（涨跌停榜代理池）
    ("600519", "贵州茅台"), ("601318", "中国平安"),
    ("600036", "招商银行"), ("600276", "恒瑞医药"),
    ("600900", "长江电力"), ("601398", "工商银行"),
    ("600030", "中信证券"), ("601988", "中国银行"),
    ("600000", "浦发银行"), ("600028", "中国石化"),
    ("601857", "中国石油"), ("600887", "伊利股份"),
    ("600585", "海螺水泥"), ("601628", "中国人寿"),
    ("600016", "民生银行"), ("600104", "上汽集团"),
    ("600196", "复星医药"), ("600703", "三安光电"),
    ("600436", "片仔癀"), ("600690", "海尔智家"),
    ("601888", "中国中免"),
    ("000001", "平安银行"), ("000002", "万科A"),
    ("000063", "中兴通讯"), ("000333", "美的集团"),
    ("000651", "格力电器"), ("000858", "五粮液"),
    ("000725", "京东方A"), ("000768", "中航西飞"),
    ("000776", "广发证券"), ("000938", "紫光股份"),
    ("002230", "科大讯飞"), ("002415", "海康威视"),
    ("002475", "立讯精密"), ("002594", "比亚迪"),
    ("300750", "宁德时代"), ("300059", "东方财富"),
    ("300015", "爱尔眼科"), ("300122", "智飞生物"),
    ("300760", "迈瑞医疗"), ("300124", "汇川技术"),
    ("300142", "沃森生物"), ("300498", "温氏股份"),
    ("300601", "康泰生物"), ("300999", "金龙鱼"),
]


def _index_quotes():
    """A 股大盘指数 · 直接用 qt 代码，不走 _qt_code_for（避免 sh000001 被映射到 sz000001）"""
    qt_codes = [c for c, _ in A_SHARE_INDICES]
    # 直接拉取 qt 数据，不经过 _qt_code_for 转换
    url = "https://qt.gtimg.cn/q=" + ",".join(qt_codes)
    text = _http_get_text(url, timeout=15, retries=2, encoding="gbk")
    if not text:
        return []
    results = []
    for line in text.splitlines():
        r = _parse_qt_line(line)
        if r:
            results.append(r)
    by_code = {q["code"]: q for q in results}
    out = []
    for qt_code, name in A_SHARE_INDICES:
        pure_code = qt_code[2:]  # sh000001 → 000001
        q = by_code.get(pure_code)
        if q and q.get("price") is not None:
            out.append({"name": name, "code": pure_code, "price": q["price"], "chg_pct": q.get("change_pct")})
    return out


# ═══════════════════════════════════════════════════════════════
# 上证指数技术分析（斐波那契 + MA13/MA55 + 量能）
# ═══════════════════════════════════════════════════════════════
_FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
_FIB_NAMES = {
    0.0: "波段起点", 0.236: "0.236", 0.382: "0.382",
    0.5: "半分位", 0.618: "黄金分割", 0.786: "0.786", 1.0: "波段终点",
}


# ── K线数据拉取（多源 fallback：腾讯 → baostock → 新浪HTTP）──
def _fetch_index_kline(code="sh000001", count=120):
    """拉取指数日K线数据，多源 fallback。
    返回: [(date, open, close, high, low, volume), ...] 或 None
    参考: UZI-Skill fetch_kline.py 7层fallback链
    """
    # 源1: 腾讯 ifzq（主，不封IP）
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{count},qfq"
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("data", {}).get(code, {}).get("day", [])
        if rows and len(rows) >= 55:
            result = []
            for row in rows[-count:]:
                if len(row) >= 6:
                    result.append((row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]) if row[5] else 0))
            return result
    except Exception:
        pass

    # 源2: 新浪 HTTP（免费，无需 key）
    try:
        symbol = code.replace("sh", "sh").replace("sz", "sz")
        url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        if data and len(data) >= 55:
            result = []
            for row in data[-count:]:
                result.append((row.get("day", ""), float(row.get("open", 0)), float(row.get("close", 0)),
                               float(row.get("high", 0)), float(row.get("low", 0)), float(row.get("volume", 0))))
            return result
    except Exception:
        pass

    # 源3: baostock（独立协议，无需 key）
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == "0":
            bs_code = f"{code.replace('sh', 'sh.').replace('sz', 'sz.')}"
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y-%m-%d")
            rs = bs.query_history_k_data_plus(bs_code,
                "date,open,close,high,low,volume",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="1")
            rows = []
            while (rs.error_code == "0") & rs.next():
                rows.append(rs.get_row_data())
            bs.logout()
            if len(rows) >= 55:
                result = []
                for row in rows[-count:]:
                    result.append((row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
                return result
    except Exception:
        pass

    return None


def _index_tech_analysis():
    """
    上证指数技术分析摘要 — 斐波那契回撤 + MA13/MA55 + 量能
    多源 fallback：腾讯 → 新浪HTTP → baostock
    海外网络不可用时返回 None（不阻断主流程）。
    """
    rows = _fetch_index_kline("sh000001", 120)
    if not rows:
        return None

    closes = []
    highs = []
    lows = []
    vols = []
    for row in rows:
        closes.append(row[2])
        highs.append(row[3])
        lows.append(row[4])
        vols.append(row[5])

    if len(closes) < 55:
        return None

    close = closes[-1]

    # MA13 / MA55
    def _ma(arr, period):
        if len(arr) < period:
            return None
        return round(sum(arr[-period:]) / period, 2)

    ma13 = _ma(closes, 13)
    ma55 = _ma(closes, 55)

    # 波段高低点（最近60根）
    lookback = min(60, len(closes))
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    high_idx = max(range(len(recent_highs)), key=lambda i: recent_highs[i])
    low_idx = min(range(len(recent_lows)), key=lambda i: recent_lows[i])
    swing_high = recent_highs[high_idx]
    swing_low = recent_lows[low_idx]
    direction = "up" if low_idx < high_idx else "down"

    # 斐波那契
    if direction == "up":
        start_price, end_price = swing_low, swing_high
    else:
        start_price, end_price = swing_high, swing_low
    diff = end_price - start_price
    fib_levels = {r: round(start_price + diff * r, 2) for r in _FIB_RATIOS}

    # 最近支撑/压力
    nearest_support = None
    nearest_resist = None
    for ratio in sorted(_FIB_RATIOS):
        lvl = fib_levels[ratio]
        if lvl < close:
            nearest_support = (ratio, lvl)
        if lvl > close and nearest_resist is None:
            nearest_resist = (ratio, lvl)

    # 量能分析
    if len(vols) >= 25:
        avg_vol_5 = sum(vols[-5:]) / 5
        avg_vol_20 = sum(vols[-25:-5]) / 20
        vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1.0
        vwap_num = sum(closes[i] * vols[i] for i in range(-20, 0))
        vwap_den = sum(vols[-20:])
        vwap = vwap_num / vwap_den if vwap_den > 0 else 0

        prev_close = closes[-6] if len(closes) >= 6 else closes[0]
        price_chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0

        if vol_ratio > 1.5 and price_chg > 0:
            vol_signal = "放量上涨"
        elif vol_ratio > 1.5 and price_chg < 0:
            vol_signal = "放量下跌"
        elif vol_ratio < 0.6 and price_chg > 0:
            vol_signal = "缩量上涨"
        elif vol_ratio < 0.6 and price_chg < 0:
            vol_signal = "缩量下跌"
        elif vol_ratio > 1.2:
            vol_signal = "温和放量"
        elif vol_ratio < 0.8:
            vol_signal = "缩量整理"
        else:
            vol_signal = "量能平稳"
    else:
        avg_vol_5 = avg_vol_20 = vol_ratio = vwap = 0
        vol_signal = "N/A"

    # MA13/MA55 位置判断
    if ma13 and ma55:
        if ma13 > ma55:
            ma_status = "多头排列"
            ma_gap = (ma13 - ma55) / ma55 * 100
        else:
            ma_status = "空头排列"
            ma_gap = (ma55 - ma13) / ma55 * 100
    else:
        ma_status = "N/A"
        ma_gap = 0

    return {
        "close": close,
        "ma13": ma13,
        "ma55": ma55,
        "ma_status": ma_status,
        "ma_gap": round(ma_gap, 2),
        "swing_high": swing_high,
        "swing_low": swing_low,
        "direction": direction,
        "fib_levels": fib_levels,
        "nearest_support": nearest_support,
        "nearest_resist": nearest_resist,
        "vol_ratio": round(vol_ratio, 2),
        "vol_signal": vol_signal,
        "vwap": round(vwap, 2) if vwap else None,
    }


def _index_tech_card(tech):
    """
    将 _index_tech_analysis() 的结果格式化为飞书卡片 Markdown 片段。
    返回 3-4 行紧凑的 Markdown 文本。
    """
    if not tech:
        return ""

    lines = ["\n**📈 上证技术面**\n"]

    # MA 状态
    if tech.get("ma13") and tech.get("ma55"):
        ma_color = "red" if tech["ma_status"] == "多头排列" else "green"
        lines.append(
            f"MA13: <font color='{ma_color}'>{tech['ma13']:.0f}</font> | "
            f"MA55: {tech['ma55']:.0f} | "
            f"<font color='{ma_color}'>{tech['ma_status']}</font>（间距{tech['ma_gap']:.2f}%）"
        )

    # 支撑/压力
    sup = tech.get("nearest_support")
    res = tech.get("nearest_resist")
    parts = []
    if sup:
        parts.append(f"支撑: {sup[1]:.0f}（{_FIB_NAMES.get(sup[0], str(sup[0]))}）")
    if res:
        parts.append(f"压力: {res[1]:.0f}（{_FIB_NAMES.get(res[0], str(res[0]))}）")
    if parts:
        lines.append(" | ".join(parts))

    # 量能
    if tech.get("vol_signal") and tech["vol_signal"] != "N/A":
        vcolor = "grey"
        if "放量" in tech["vol_signal"]:
            vcolor = "red" if "上涨" in tech["vol_signal"] else "green"
        lines.append(
            f"量能: <font color='{vcolor}'>{tech['vol_signal']}</font>"
            f"（量比{tech['vol_ratio']:.2f} | VWAP{tech['vwap']:.0f}）" if tech.get("vwap") else
            f"量能: <font color='{vcolor}'>{tech['vol_signal']}</font>（量比{tech['vol_ratio']:.2f}）"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 市场情绪数据（涨跌家数 + 两市量能 + 涨停进阶 + 破板率）
# 数据源：财联社情绪API（单次调用全覆盖）
# ═══════════════════════════════════════════════════════════════
_MARKET_EMOTION_URL = (
    "https://x-quote.cls.cn/v2/quote/a/stock/emotion"
    "?app=CailianpressWeb&os=web&sv=8.4.6&sign=9f8797a1f4de66c2370f7a03990d2737"
)


def _market_breadth():
    """
    市场情绪数据 — 涨跌家数、两市成交额、涨停进阶、破板率。
    返回 dict 或 None（网络异常时）。
    """
    try:
        resp = _http_get_text(_MARKET_EMOTION_URL, timeout=10, retries=1)
        if not resp:
            return None
        data = json.loads(resp)

        d = data.get("data", {})
        if not d:
            return None

        # 涨跌家数
        ud = d.get("up_down_dis", {})
        rise_num = ud.get("rise_num", 0)      # 上涨家数
        fall_num = ud.get("fall_num", 0)      # 下跌家数
        flat_num = ud.get("flat_num", 0)      # 平盘家数
        up_num = ud.get("up_num", 0)          # 涨停家数
        down_num = ud.get("down_num", 0)      # 跌停家数

        # 两市成交额
        balance = d.get("shsz_balance", "")                      # 如 "2.7万亿"
        balance_chg = d.get("shsz_balance_change_px", "")        # 如 "-2986亿"

        # 涨停板进阶统计
        lb = d.get("limit_up_board", {})
        board_stats = []
        if lb:
            row1 = lb.get("row1", [])  # 板名
            row2 = lb.get("row2", [])  # 数量
            row3 = lb.get("row3", [])  # 连板率
            for i in range(min(len(row1), len(row2))):
                rate_str = str(row3[i]) if i < len(row3) else ""
                # 跳过表头行（rate 列值为 "连板率" 等非数字文本）
                if rate_str in ("连板率", "板名", "数量"):
                    continue
                board_stats.append({
                    "name": row1[i],
                    "count": int(row2[i]) if str(row2[i]).isdigit() else row2[i],
                    "rate": rate_str,
                })

        # 破板率
        break_rate = d.get("break_through_rate", "")
        break_rate_label = d.get("break_through_rate_label", "")

        # 昨日涨停表现
        zt_perf = d.get("zt_performance", "")
        zt_perf_label = d.get("zt_performance_label", "")

        # 大幅回撤
        big_pullback = d.get("big_pullback_num", 0)

        total = rise_num + fall_num + flat_num
        rise_pct = round(rise_num / total * 100, 1) if total > 0 else 0

        return {
            "rise_num": rise_num,
            "fall_num": fall_num,
            "flat_num": flat_num,
            "up_num": up_num,           # 涨停
            "down_num": down_num,       # 跌停
            "total": total,
            "rise_pct": rise_pct,
            "balance": balance,         # 成交额
            "balance_chg": balance_chg,
            "board_stats": board_stats, # 涨停进阶
            "break_rate": break_rate,
            "break_rate_label": break_rate_label,
            "zt_perf": zt_perf,         # 昨日涨停今表现
            "zt_perf_label": zt_perf_label,
            "big_pullback": big_pullback,
        }
    except Exception:
        return None


def _market_breadth_card(breadth):
    """
    将 _market_breadth() 结果格式化为飞书卡片 Markdown 片段。
    返回 4-6 行紧凑的 Markdown 文本。
    """
    if not breadth:
        return ""

    lines = ["\n**📊 市场情绪**\n"]

    # 涨跌家数
    lines.append(
        f"上涨: <font color='red'>{breadth['rise_num']}</font> | "
        f"下跌: <font color='green'>{breadth['fall_num']}</font> | "
        f"平盘: {breadth['flat_num']} | "
        f"涨停: <font color='red'>{breadth['up_num']}</font> | "
        f"跌停: <font color='green'>{breadth['down_num']}</font>"
    )

    # 两市成交额
    if breadth.get("balance"):
        chg_str = f"（{breadth['balance_chg']}）" if breadth.get("balance_chg") else ""
        lines.append(f"两市成交: {breadth['balance']}{chg_str}")

    # 涨停板进阶
    if breadth.get("board_stats"):
        parts = []
        for bs in breadth["board_stats"]:
            rate_str = f" {bs['rate']}" if bs.get("rate") else ""
            parts.append(f"{bs['name']}:{bs['count']}{rate_str}")
        lines.append("涨停进阶: " + " → ".join(parts))

    # 破板率 + 昨日涨停表现
    extras = []
    if breadth.get("break_rate"):
        extras.append(f"破板率: {breadth['break_rate']}（{breadth.get('break_rate_label', '')}）")
    if breadth.get("zt_perf"):
        extras.append(f"昨涨停表现: {breadth['zt_perf']}（{breadth.get('zt_perf_label', '')}）")
    if breadth.get("big_pullback", 0) > 0:
        extras.append(f"大幅回撤: {breadth['big_pullback']}只")
    if extras:
        lines.append(" | ".join(extras))

    return "\n".join(lines)


def _sector_etf_quotes():
    """A 股行业 ETF 行情"""
    pairs = [(c, "A") for c, _ in A_SHARE_SECTOR_ETFS]
    qs = fetch_qt_quotes(pairs)
    by_code = {q["code"]: q for q in qs}
    out = []
    for c, name in A_SHARE_SECTOR_ETFS:
        q = by_code.get(c)
        if q and q.get("price") is not None and q.get("change_pct") is not None:
            out.append({"name": name, "code": c, "chg_pct": q["change_pct"]})
    out.sort(key=lambda x: -(x["chg_pct"] or 0))
    return out


# ═══════════════════════════════════════════════════════════════
# 东财涨停板池（打板层 V3.3.0）— 涨停/炸板/跌停/昨日涨停 + 连板天梯
# 参考 a-stock-data SKILL §8.1 — 直连 HTTP，无需 akshare 依赖
# ═══════════════════════════════════════════════════════════════
import random
import requests as _requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 东财全局节流（a-stock-data 防封规则）
EM_SESSION = _requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]

def em_get(url: str, params: dict = None, headers: dict = None, timeout: int = 15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA。
    所有 eastmoney.com 接口都应通过它请求，避免高频被封 IP。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def eastmoney_datacenter(report_name: str, columns: str = "ALL",
                          filter_str: str = "", page_size: int = 50,
                          sort_columns: str = "", sort_types: str = "-1") -> list:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 共用（已内置限流）"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []

_ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"

def _fmt_zt_time(t) -> str:
    """涨停板时间整数 → HH:MM:SS（92500 → 09:25:00）。"""
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"

def _em_zt_api(endpoint: str, sort: str, date: str) -> list[dict]:
    """东财涨停板行情中心通用请求（push2ex，走 em_get 限流）。
    endpoint: getTopicZTPool / getTopicZBPool / getTopicDTPool / getYesterdayZTPool
    返回 data.pool 原始列表（data 为 null = 非交易日 / 参数错）。"""
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {"ut": _ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": date}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        return (r.json().get("data") or {}).get("pool") or []
    except Exception as e:
        print(f"[WARN] 涨停板池 {endpoint} 请求失败: {e}", file=sys.stderr)
        return []

def em_zt_pool(date: str) -> list[dict]:
    """涨停池（a-stock-data 直连，无需 akshare）。date=YYYYMMDD（交易日）。
    返回每只: code/name/price/pct/amount/float_cap/turnover/limit_days(连板数)/
    first_seal/last_seal(封板时间)/seal_fund(封板资金,元)/break_times(炸板次数)/
    industry/zt_stat(N天M板)"""
    out = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "amount": p["amount"], "float_cap": p["ltsz"],
            "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
            "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
            "seal_fund": p["fund"], "break_times": p["zbc"], "industry": p.get("hybk", ""),
            "zt_stat": f'{(p.get("zttj") or {}).get("days","?")}天{(p.get("zttj") or {}).get("ct","?")}板'})
    return out

def em_zb_pool(date: str) -> list[dict]:
    """炸板池（涨停后开板）。返回 code/name/price/limit_price(涨停价)/pct/turnover/
    first_seal/break_times/amplitude(振幅)/speed(涨速)/industry/zt_stat"""
    out = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
            "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
            "break_times": p["zbc"], "amplitude": round(p["zf"], 2),
            "speed": round(p["zs"], 2), "industry": p.get("hybk", ""),
            "zt_stat": f'{(p.get("zttj") or {}).get("days","?")}天{(p.get("zttj") or {}).get("ct","?")}板'})
    return out

def em_dt_pool(date: str) -> list[dict]:
    """跌停池。返回 code/name/price/pct/turnover/pe/seal_fund(封单资金)/last_seal/
    board_amount(板上成交额)/dt_days(连续跌停)/open_times(开板次数)/industry"""
    out = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2), "pe": p.get("pe"),
            "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
            "board_amount": p.get("fba"), "dt_days": p.get("days"),
            "open_times": p.get("oc"), "industry": p.get("hybk", "")})
    return out

def em_yzt_pool(date: str) -> list[dict]:
    """昨日涨停池（昨涨停今表现，算晋级率/赚钱效应）。返回 code/name/price/
    pct(今日涨幅)/turnover/amplitude/speed/y_first_seal(昨封板时间)/
    y_limit_days(昨连板)/industry/zt_stat"""
    out = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
            "amplitude": round(p["zf"], 2), "speed": round(p["zs"], 2),
            "y_first_seal": _fmt_zt_time(p["yfbt"]), "y_limit_days": p["ylbc"],
            "industry": p.get("hybk", ""),
            "zt_stat": f'{(p.get("zttj") or {}).get("days","?")}天{(p.get("zttj") or {}).get("ct","?")}板'})
    return out

def limit_up_sentiment(date: str) -> dict:
    """打板情绪温度计：连板梯队 + 炸板率 + 涨跌停对比。
    返回: {ladder: {板数:家数}, max_height: 最高连板, zt_count: 涨停数, break_rate: 炸板率%}
    """
    zt = em_zt_pool(date)
    zb = em_zb_pool(date)
    dt = em_dt_pool(date)
    ladder = {}
    for s in zt:
        ld = s.get("limit_days", 1)
        ladder[ld] = ladder.get(ld, 0) + 1
    zt_n, zb_n = len(zt), len(zb)
    return {"date": date, "zt_count": zt_n, "zb_count": zb_n, "dt_count": len(dt),
        "break_rate": round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0,
        "max_height": max((s["limit_days"] for s in zt), default=0),
        "ladder": dict(sorted(ladder.items()))}

def _convert_em_zt_to_old_format(zt_list):
    """将东财涨停池转换为旧接口格式（兼容现有代码）"""
    out = []
    for p in zt_list:
        out.append({
            "code": p["code"],
            "name": p["name"],
            "chg_pct": p["pct"],
            "price": p["price"],
            "amount": p["amount"],
            "mcap": p["float_cap"],
            "turnover": p["turnover"],
            "seal_amount": p["seal_fund"],
            "seal_time": p["last_seal"],
            "break_count": p["break_times"],
            "consecutive": p["limit_days"],
            "industry": p["industry"],
        })
    return out

def _convert_em_dt_to_old_format(dt_list):
    """将东财跌停池转换为旧接口格式（兼容现有代码）"""
    out = []
    for p in dt_list:
        out.append({
            "code": p["code"],
            "name": p["name"],
            "chg_pct": p["pct"],
            "price": p["price"],
            "amount": p["board_amount"],
            "mcap": 0,  # 不提供流通市值，旧代码不用
            "turnover": p["turnover"],
            "seal_amount": p["seal_fund"],
            "consecutive": p["dt_days"],
            "industry": p["industry"],
        })
    return out

def _fetch_limit_pool_safe(date_str=None):
    """涨停池 · 东财直连（a-stock-data V3.3），无需 akshare
    返回: (list[dict], source_name)
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        em_result = em_zt_pool(date_str)
        if em_result is not None and len(em_result) > 0:
            return _convert_em_zt_to_old_format(em_result), "东财直连"
        raise ValueError("涨停池为空")
    except Exception as e:
        print(f"[LIMIT] 东财涨停池不可用: {e}", file=sys.stderr)
        # fallback: 活跃股代理
        stocks = _a_share_active_stocks()
        proxy = sorted([s for s in stocks if s.get("change_pct", 0) >= 9.5],
                       key=lambda x: -(x.get("change_pct") or 0))[:15]
        return proxy, "proxy"

def _fetch_dt_pool_safe(date_str=None):
    """跌停池 · 东财直连（a-stock-data V3.3），无需 akshare
    返回: (list[dict], source_name)
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        em_result = em_dt_pool(date_str)
        if em_result is not None and len(em_result) > 0:
            return _convert_em_dt_to_old_format(em_result), "东财直连"
        raise ValueError("跌停池为空")
    except Exception as e:
        print(f"[DT] 东财跌停池不可用: {e}", file=sys.stderr)
        stocks = _a_share_active_stocks()
        proxy = sorted([s for s in stocks if s.get("change_pct", 0) <= -9.5],
                       key=lambda x: (x.get("change_pct") or 0))[:15]
        return proxy, "proxy"

def _fetch_strong_pool_em(date_str=None):
    """连板股 · 东财直连，从涨停池提取（无需 akshare 强势股）
    返回: 连板股列表（limit_days >= 2）
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        all_zt = em_zt_pool(date_str)
        # 连板股 = 连板数 >= 2 的涨停股
        strong = [p for p in all_zt if p.get("limit_days", 1) >= 2]
        # 转换为旧格式
        out = []
        for p in strong:
            out.append({
                "code": p["code"],
                "name": p["name"],
                "chg_pct": p["pct"],
                "price": p["price"],
                "amount": p["amount"],
                "mcap": p["float_cap"],
                "turnover": p["turnover"],
                "zt_stat": p["zt_stat"],
                "reason": "",
                "industry": p["industry"],
            })
        return out
    except Exception as e:
        print(f"[STRONG] 东财连板池不可用: {e}", file=sys.stderr)
        return None


def _a_share_active_stocks():
    """A 股活跃股权重股（涨跌停榜代理池）"""
    pairs = [(c, "A") for c, _ in A_SHARE_BLUE_CHIPS]
    qs = fetch_qt_quotes(pairs)
    valid = [q for q in qs if q.get("change_pct") is not None]
    return valid


def _us_quote(code):
    """单只美股"""
    qs = fetch_qt_quotes([(code, "U")])
    return qs[0] if qs else None


def _us_quotes_batch(codes):
    """批量美股"""
    pairs = [(c, "U") for c in codes]
    return fetch_qt_quotes(pairs)


def _us_sector_etf_quotes():
    """美股板块 ETF 行情"""
    pairs = [(c, "U") for c, _ in US_SECTOR_ETFS]
    qs = fetch_qt_quotes(pairs)
    by_code = {q["code"]: q for q in qs}
    out = []
    for c, name in US_SECTOR_ETFS:
        q = by_code.get(c)
        if q and q.get("price") is not None and q.get("change_pct") is not None:
            out.append({"name": name, "code": c, "chg_pct": q["change_pct"]})
    out.sort(key=lambda x: -(x["chg_pct"] or 0))
    return out


# ═══════════════════════════════════════════════════════════════
# 新浪行业板块 · 49 个真实行业涨跌（替代 ETF 代理）
# ═══════════════════════════════════════════════════════════════
def _sector_spot_sina():
    """新浪行业板块 · 返回 49 个行业真实涨跌数据
    字段: name, chg_pct, count(公司家数), lead_stock, lead_code, lead_chg, amount
    """
    try:
        import akshare as ak
        df = ak.stock_sector_spot(indicator="新浪行业")
        if df is None or len(df) == 0:
            raise ValueError("新浪行业板块为空")
        sectors = []
        for _, row in df.iterrows():
            sectors.append({
                "name": str(row.get("板块", "")),
                "chg_pct": float(row.get("涨跌幅", 0)),
                "count": int(row.get("公司家数", 0)),
                "lead_stock": str(row.get("股票名称", "")),
                "lead_code": str(row.get("股票代码", "")).replace("sh", "").replace("sz", ""),
                "lead_chg": float(row.get("个股-涨跌幅", 0)),
                "amount": float(row.get("总成交额", 0)),
            })
        sectors.sort(key=lambda x: -(x["chg_pct"] or 0))
        return sectors
    except Exception as e:
        print(f"[SINA SECTOR] 不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 东财全球资讯（7x24）· 替代已下线的财联社快讯
# 参考 a-stock-data SKILL §5.3
# ═══════════════════════════════════════════════════════════════
def _eastmoney_global_news(page_size=30):
    """东方财富全球财经资讯（7x24 滚动）。
    返回: [{title, summary, time}]
    """
    import uuid
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web", "biz": "web_724",
        "fastColumn": "102", "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    try:
        text = _http_get_text(f"{url}?{'&'.join(f'{k}={v}' for k,v in params.items())}",
                              timeout=10, retries=2,
                              extra_headers={"Referer": "https://kuaixun.eastmoney.com/"})
        if not text:
            return None
        data = json.loads(text)
        rows = []
        for item in data.get("data", {}).get("fastNewsList", []):
            rows.append({
                "title": item.get("title", ""),
                "summary": (item.get("summary", "") or "")[:200],
                "time": item.get("showTime", ""),
            })
        return rows
    except Exception as e:
        print(f"[EM NEWS] 东财全球资讯不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 金十数据快讯 · 参考 a-stock-data SKILL §5 新闻层
# ═══════════════════════════════════════════════════════════════
def _jin10_flash(page_size=20):
    """金十数据快讯（实时财经快讯）。
    返回: [{title, snippet, time}] 或 None
    """
    url = "https://www.jin10.com/flash_newest.js"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.jin10.com/"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'var newest = (\[.*?\]);', raw, re.DOTALL)
        if not match:
            return None
        items = json.loads(match.group(1))
        rows = []
        for item in items[:page_size]:
            data = item.get("data", {})
            title = data.get("content", "") or data.get("title", "") or ""
            rows.append({
                "title": title,
                "snippet": data.get("content", "") or title,
                "time": item.get("time", ""),
            })
        return rows
    except Exception as e:
        print(f"[JIN10] 金十数据不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 东财快讯 · 参考 a-stock-data SKILL §5 新闻层
# ═══════════════════════════════════════════════════════════════
def _eastmoney_kuaixun(page_size=15):
    """东财快讯（7x24 滚动快讯）。
    返回: [{title, snippet, time}] 或 None
    """
    url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_101_ajaxResult_50_1_.html"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://kuaixun.eastmoney.com/"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8", errors="replace")
        idx = raw.find("var ajaxResult=")
        if idx < 0:
            return None
        json_str = raw[idx + len("var ajaxResult="):]
        last_brace = json_str.rfind("}")
        if last_brace >= 0:
            json_str = json_str[:last_brace + 1]
        data = json.loads(json_str)
        rows = []
        for item in data.get("LivesList", [])[:page_size]:
            rows.append({
                "title": item.get("title", ""),
                "snippet": item.get("digest", "") or item.get("title", ""),
                "time": item.get("showtime", ""),
            })
        return rows
    except Exception as e:
        print(f"[EM KX] 东财快讯不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 新浪财经新闻 · 通用（支持不同分类频道）
# 参考 a-stock-data SKILL §5 新闻层
# ═══════════════════════════════════════════════════════════════
def _sina_news(lid="2516", page_size=15):
    """新浪财经新闻（通用，支持不同分类）。
    lid 分类: 2509=财经要闻, 2512=A股聚焦, 2515=产经动态, 2516=全球宏观
    返回: [{title, snippet, url}] 或 None
    """
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&k=&num={page_size}&page=1"
    try:
        text = _http_get_text(url, timeout=15, retries=2,
                              extra_headers={"User-Agent": "Mozilla/5.0"})
        if not text:
            return None
        data = json.loads(text)
        items = data.get("result", {}).get("data", [])
        rows = []
        for item in items:
            rows.append({
                "title": item.get("title", ""),
                "snippet": item.get("intro", "") or item.get("title", ""),
                "url": item.get("url", ""),
            })
        return rows
    except Exception as e:
        print(f"[SINA NEWS] 新浪财经不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 东财个股新闻 · 参考 a-stock-data SKILL §5.1
# ═══════════════════════════════════════════════════════════════
def _eastmoney_stock_news(code, page_size=20):
    """东财个股新闻（JSONP 接口）。
    code: 6位股票代码
    返回: [{title, content, time, source, url}] 或 None
    """
    cb = "jQuery_news"
    inner_params = json.dumps({
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                  "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }, separators=(',', ':'))
    try:
        import urllib.parse
        full_url = f"https://search-api-web.eastmoney.com/search/jsonp?cb={cb}&param={urllib.parse.quote(inner_params)}"
        text = _http_get_text(full_url, timeout=15, retries=2,
                              extra_headers={"Referer": "https://so.eastmoney.com/"})
        if not text:
            return None
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        d = json.loads(json_str)
        articles = d.get("result", {}).get("cmsArticleWebOld", []) or []
        rows = []
        for a in articles:
            rows.append({
                "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
                "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
                "time": a.get("date", ""),
                "source": a.get("mediaName", ""),
                "url": a.get("url", ""),
            })
        return rows
    except Exception as e:
        print(f"[EM STOCK NEWS] 东财个股新闻不可用: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 统一新闻收集 · 多源聚合 + 自动去重
# ═══════════════════════════════════════════════════════════════
def _collect_all_news(sources=None, max_per_source=None):
    """统一新闻收集：多源聚合 + 自动去重。
    sources: 要启用的数据源列表，默认全部。
             可选: "jin10", "em_kuaixun", "em_global", "sina_global", "sina_yaowen",
                   "sina_agu", "sina_chanjing"
    max_per_source: 每源最大条数 dict，如 {"jin10": 20, "em_kuaixun": 15}
    返回: [{title, snippet, source, time, url}]
    """
    if sources is None:
        sources = ["jin10", "em_kuaixun", "em_global", "sina_global"]
    if max_per_source is None:
        max_per_source = {}

    all_items = []
    seen = set()

    def _add(rows, source_label):
        if not rows:
            return
        for r in rows:
            key = r.get("title", "")[:30]
            if key not in seen:
                seen.add(key)
                all_items.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", "") or r.get("summary", "") or r.get("title", ""),
                    "source": source_label,
                    "time": r.get("time", ""),
                    "url": r.get("url", ""),
                })

    # 金十数据
    if "jin10" in sources:
        try:
            rows = _jin10_flash(max_per_source.get("jin10", 20))
            _add(rows, "金十数据")
        except Exception:
            pass

    # 东财快讯
    if "em_kuaixun" in sources:
        try:
            rows = _eastmoney_kuaixun(max_per_source.get("em_kuaixun", 15))
            _add(rows, "东财快讯")
        except Exception:
            pass

    # 东财全球资讯
    if "em_global" in sources:
        try:
            rows = _eastmoney_global_news(max_per_source.get("em_global", 15))
            _add(rows, "东财全球资讯")
        except Exception:
            pass

    # 新浪全球宏观
    if "sina_global" in sources:
        try:
            rows = _sina_news("2516", max_per_source.get("sina_global", 10))
            _add(rows, "新浪全球宏观")
        except Exception:
            pass

    # 新浪财经要闻
    if "sina_yaowen" in sources:
        try:
            rows = _sina_news("2509", max_per_source.get("sina_yaowen", 15))
            _add(rows, "新浪财经要闻")
        except Exception:
            pass

    # 新浪A股聚焦
    if "sina_agu" in sources:
        try:
            rows = _sina_news("2512", max_per_source.get("sina_agu", 15))
            _add(rows, "新浪A股聚焦")
        except Exception:
            pass

    # 新浪产经动态
    if "sina_chanjing" in sources:
        try:
            rows = _sina_news("2515", max_per_source.get("sina_chanjing", 15))
            _add(rows, "新浪产经动态")
        except Exception:
            pass

    return all_items


# ═══════════════════════════════════════════════════════════════
# 共享卡片构建函数（从各脚本提取，消除重复代码）
# ═══════════════════════════════════════════════════════════════

def _fmt_seal(amt):
    """封单金额格式化: 1.23亿 / 8210万"""
    if amt is None or amt == 0:
        return "--"
    if amt >= 1e8:
        return f"{amt/1e8:.2f}亿"
    return f"{amt/1e4:.0f}万"

def _get_concept_tags(s, ths_map):
    """获取一只股票的题材标签列表（从同花顺涨停原因拆分）"""
    c = s.get("code", "")
    t = ths_map.get(c, {})
    reason = t.get("reason", "") or s.get("industry", "")
    if not reason:
        return []
    return [r.strip() for r in reason.split("+") if r.strip()]

def send_card(webhook, title, content, template="blue", max_bytes=18000, log_func=None):
    """通用飞书卡片发送（从各脚本提取，消除重复）"""
    return send_feishu_card(webhook, title, content, template, max_bytes, log=log_func)


# ═══════════════════════════════════════════════════════════════
# 同花顺涨停揭秘 · 涨停原因题材 + 封板成功率 + 板型
# 参考 a-stock-data SKILL §8.2
# ═══════════════════════════════════════════════════════════════
def _ths_limit_up_pool(date_str=None):
    """同花顺涨停揭秘（涨停原因 + 封板质量增强源）。date_str=YYYYMMDD。
    返回每只: code/name/price/pct/reason(涨停原因题材)/board_type(换手板/一字板/T字板)/
    seal_rate(封板成功率,0~1)/break_times(炸板次数)/seal_amount(封单额,元)/
    high_days(几天几板)/first_time(首次涨停时间)/is_again(是否回封 0/1)
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    url = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    params = {
        "page": 1, "limit": 200,
        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
        "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": date_str,
    }
    try:
        data = _http_get_json(f"{url}?{'&'.join(f'{k}={v}' for k,v in params.items())}", timeout=10, retries=2)
        if not data:
            return None
        info = (data.get("data") or {}).get("info", [])
    except Exception as e:
        print(f"[THS LIMIT] 涨停揭秘不可用: {e}", file=sys.stderr)
        return None
    out = []
    for it in info:
        ft = it.get("first_limit_up_time")
        try:
            first_time = datetime.fromtimestamp(int(ft)).strftime("%H:%M:%S") if ft else ""
        except (ValueError, TypeError, OSError):
            first_time = ""
        out.append({
            "code": str(it.get("code", "")),
            "name": str(it.get("name", "")),
            "price": it.get("latest"),
            "pct": it.get("change_rate"),
            "reason": str(it.get("reason_type", "") or ""),
            "board_type": str(it.get("limit_up_type", "") or ""),
            "seal_rate": it.get("limit_up_suc_rate"),
            "break_times": it.get("open_num") or 0,
            "seal_amount": it.get("order_amount"),
            "high_days": str(it.get("high_days", "") or ""),
            "first_time": first_time,
            "is_again": it.get("is_again_limit"),
        })
    return out


# ═══════════════════════════════════════════════════════════════
# 题材热度分析 · 涨停池/跌停池/连板池 行业聚合
# ═══════════════════════════════════════════════════════════════
def _aggregate_pool_by_industry(pool):
    """涨停池/跌停池按行业聚合 → [(行业, 数量, [股票列表]), ...] 按数量降序"""
    if not pool:
        return []
    buckets = {}
    for s in pool:
        ind = s.get("industry", "其他")
        if ind not in buckets:
            buckets[ind] = []
        buckets[ind].append(s)
    ranked = sorted(buckets.items(), key=lambda x: -len(x[1]))
    return [(ind, len(stocks), stocks) for ind, stocks in ranked]


# ═══════════════════════════════════════════════════════════════
# 同花顺热点 · 概念题材热度（v4 核心数据源）
# 参考 a-stock-data SKILL §3.1 · 同花顺热点接口
# 零鉴权、73ms 响应、返回当日强势股 + 人工运营题材标签
# ═══════════════════════════════════════════════════════════════
def _ths_hot_reason(date_str=None):
    """同花顺当日强势股归因。
    返回 list[dict]: [{name, code, reason, pct, turnover, amount, ...}]
    reason 字段是核心：人工运营题材标签，如 '算力租赁+Token工厂+AI政务'
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    elif len(date_str) == 8:  # YYYYMMDD → YYYY-MM-DD
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    try:
        url = (
            f"http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{date_str}/orderby/date/orderway/desc/charset/GBK/"
        )
        text = _http_get_text(url, timeout=10, retries=2, encoding="gbk")
        if not text:
            return None
        data = json.loads(text)
        if data.get("errocode", 0) != 0:
            print(f"[THS HOT] 接口错误: {data.get('errormsg', '')}", file=sys.stderr)
            return None
        rows = data.get("data") or []
        out = []
        for r in rows:
            out.append({
                "name": str(r.get("name", "")),
                "code": str(r.get("code", "")),
                "reason": str(r.get("reason", "")),
                "pct": float(r.get("zhangfu", 0)),
                "price": float(r.get("close", 0)) if r.get("close") else None,
                "turnover": float(r.get("huanshou", 0)),
                "amount": float(r.get("chengjiaoe", 0)),
                "dde": float(r.get("ddejingliang", 0)),
            })
        return out
    except Exception as e:
        print(f"[THS HOT] 不可用: {e}", file=sys.stderr)
        return None


def _ths_concept_heat(date_str=None):
    """从同花顺热点提取题材热度排名。
    返回: {
        "concepts": [(题材标签, 数量, 股票列表), ...],  # 按热度降序
        "stocks": [{name, code, reason, pct, ...}],     # 原始强势股列表
        "total": int,  # 强势股总数
        "date": str,
    }
    """
    stocks = _ths_hot_reason(date_str)
    if stocks is None:
        return None
    # 提取所有题材标签
    tag_buckets = {}
    for s in stocks:
        reason = s.get("reason", "")
        tags = [t.strip() for t in reason.split("+") if t.strip()]
        for t in tags:
            if t not in tag_buckets:
                tag_buckets[t] = []
            tag_buckets[t].append(s)
    ranked = sorted(tag_buckets.items(), key=lambda x: -len(x[1]))
    return {
        "concepts": [(tag, len(stocks_list), stocks_list) for tag, stocks_list in ranked],
        "stocks": stocks,
        "total": len(stocks),
        "date": date_str or datetime.now().strftime("%Y-%m-%d"),
    }


def _ths_hot_rank():
    """同花顺人气榜 TOP 50（含概念标签）。
    返回 list[dict]: [{rank, name, code, heat(人气值), pct, concepts, tag}]
    """
    try:
        url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
        params = "stock_type=a&type=day&list_type=normal"
        text = _http_get_text(f"{url}?{params}", timeout=10, retries=2)
        if not text:
            return None
        data = json.loads(text)
        rows = data.get("data", {}).get("stock_list", [])
        out = []
        for it in rows:
            tag = it.get("tag") or {}
            out.append({
                "rank": it.get("order"),
                "name": it.get("name", ""),
                "code": it.get("code", ""),
                "heat": it.get("rate", 0),
                "pct": it.get("rise_and_fall", 0),
                "concepts": tag.get("concept_tag") or [],
                "pop_tag": tag.get("popularity_tag", ""),
            })
        return out
    except Exception as e:
        print(f"[THS RANK] 不可用: {e}", file=sys.stderr)
        return None


def _theme_heat_from_pools(limit_up, limit_down, strong_pool):
    """综合题材热度分析（保留涨停池行业聚合作为辅助）
    返回: {
        "up_industries": [(行业, 数量, 股票列表), ...],
        "down_industries": [(行业, 数量, 股票列表), ...],
        "strong_themes": {行业: {"count": N, "stocks": [...]}},
    }
    """
    result = {
        "up_industries": _aggregate_pool_by_industry(limit_up) if limit_up else [],
        "down_industries": _aggregate_pool_by_industry(limit_down) if limit_down else [],
        "strong_themes": {},
    }
    if strong_pool:
        for s in strong_pool:
            ind = s.get("industry", "其他")
            if ind not in result["strong_themes"]:
                result["strong_themes"][ind] = {"count": 0, "stocks": []}
            result["strong_themes"][ind]["count"] += 1
            result["strong_themes"][ind]["stocks"].append(s)
    return result


def _theme_heat_safe(date_str=None):
    """安全获取题材热度 · 同花顺概念题材(主) + 涨停池(辅) + 连板池 + 人气榜 + 涨停揭秘"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    date_dash = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    up, up_src = _fetch_limit_pool_safe(date_str)
    down, down_src = _fetch_dt_pool_safe(date_str)
    strong = _fetch_strong_pool_em(date_str)
    heat = _theme_heat_from_pools(up, down, strong)
    heat["up_src"] = up_src
    heat["down_src"] = down_src
    heat["up_count"] = len(up)
    heat["down_count"] = len(down)
    heat["strong_count"] = len(strong) if strong else 0
    # 主数据源：同花顺概念题材热度
    concept = _ths_concept_heat(date_dash)
    if concept:
        heat["concepts"] = concept["concepts"]
        heat["ths_stocks"] = concept["stocks"]
        heat["ths_total"] = concept["total"]
    else:
        heat["concepts"] = None
        heat["ths_stocks"] = None
        heat["ths_total"] = 0
    # 同花顺涨停揭秘（封板率+板型+涨停原因）
    ths_limit = _ths_limit_up_pool(date_str)
    if ths_limit:
        heat["ths_limit"] = ths_limit
        # 按涨停原因聚合题材热度（辅助 concepts）
        reason_buckets = {}
        for s in ths_limit:
            reason = s.get("reason", "")
            tags = [t.strip() for t in reason.split("+") if t.strip()]
            for t in tags:
                if t not in reason_buckets:
                    reason_buckets[t] = []
                reason_buckets[t].append(s)
        heat["reason_concepts"] = sorted(reason_buckets.items(), key=lambda x: -len(x[1]))
    else:
        heat["ths_limit"] = None
        heat["reason_concepts"] = None
    # 人气榜
    hot_rank = _ths_hot_rank()
    if hot_rank:
        heat["hot_rank"] = hot_rank[:20]
    else:
        heat["hot_rank"] = None
    return heat


# ═══════════════════════════════════════════════════════════════
# 信号层 - 北向资金 · 沪深港通每日净流入 + 5日均值 + 连续流向
# 数据源：东财 push2his fflow（已修复 kamt.kline 数据缺口问题）
# ═══════════════════════════════════════════════════════════════

# ── 北向资金本地缓存 ──
_NB_CACHE_DIR = Path.home() / ".tradingagents" / "cache"
_NB_CACHE_FILE = _NB_CACHE_DIR / "northbound_daily.csv"

def _nb_cache_read():
    """读取本地CSV缓存，返回 [{date, hgt, sgt}] 按日期升序"""
    if not _NB_CACHE_FILE.exists():
        return []
    rows = []
    try:
        for line in _NB_CACHE_FILE.read_text().strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                rows.append({
                    "date": parts[0],
                    "hgt": float(parts[1]),
                    "sgt": float(parts[2]),
                })
    except Exception:
        return []
    return rows

def _nb_cache_write(date: str, hgt: float, sgt: float):
    """写入/更新当天北向收盘数据到CSV"""
    _NB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows = {}
    if _NB_CACHE_FILE.exists():
        try:
            for line in _NB_CACHE_FILE.read_text().strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) == 3:
                    rows[parts[0]] = line
        except Exception:
            pass
    rows[date] = f"{date},{hgt},{sgt}"
    with open(_NB_CACHE_FILE, "w") as f:
        f.write("date,hgt,sgt\n")
        for d in sorted(rows.keys()):
            f.write(rows[d] + "\n")


def _ths_north_bound():
    """北向资金 · 沪深港通日级净流入 + 5日均值 + 连续流向统计
    数据源：同花顺 hsgtApi（data.hexin.cn）— 零鉴权、稳定
    参考 a-stock-data SKILL.md §3.2
    返回: {"sh": 沪净流入(亿), "sz": 深净流入(亿), "total": 合计(亿),
           "ma5": 5日均值(亿), "consecutive": 连续N日, "direction": "流入"/"流出"}
    或 None（数据源不可用时）
    """
    try:
        # 1. 拉取同花顺当日实时北向数据
        hsgt_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36",
            "Host": "data.hexin.cn",
            "Referer": "https://data.hexin.cn/",
        }
        url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
        req = urllib.request.Request(url, headers=hsgt_headers)
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        data = json.loads(raw)

        hgt_list = data.get("hgt", [])
        sgt_list = data.get("sgt", [])
        time_list = data.get("time", [])

        if not hgt_list or not sgt_list:
            return None

        # 取最后一个有效值（收盘累计）
        hgt_close = None
        sgt_close = None
        for i in range(len(hgt_list) - 1, -1, -1):
            if hgt_close is None and hgt_list[i] is not None:
                hgt_close = float(hgt_list[i])
            if sgt_close is None and sgt_list[i] is not None:
                sgt_close = float(sgt_list[i])
            if hgt_close is not None and sgt_close is not None:
                break

        if hgt_close is None or sgt_close is None:
            return None

        sh_net = round(hgt_close, 2)
        sz_net = round(sgt_close, 2)
        total = round(sh_net + sz_net, 2)

        # 2. 写入本地缓存（当日收盘数据）
        today_str = datetime.now().strftime("%Y-%m-%d")
        _nb_cache_write(today_str, sh_net, sz_net)

        # 3. 读取历史缓存，计算5日均值和连续天数
        history = _nb_cache_read()
        if len(history) >= 5:
            recent_5 = history[-5:]
            ma5 = round(sum(r["hgt"] + r["sgt"] for r in recent_5) / 5, 2)
        elif len(history) > 0:
            ma5 = round(sum(r["hgt"] + r["sgt"] for r in history) / len(history), 2)
        else:
            ma5 = total

        # 连续流入/流出天数
        consecutive = 0
        direction = "流入" if total > 0 else "流出" if total < 0 else "持平"
        for r in reversed(history):
            day_total = r["hgt"] + r["sgt"]
            if (day_total > 0 and total > 0) or (day_total < 0 and total < 0):
                consecutive += 1
            else:
                break

        return {
            "sh": sh_net,
            "sz": sz_net,
            "total": total,
            "ma5": ma5,
            "consecutive": consecutive,
            "direction": direction,
        }
    except Exception as e:
        print(f"[NORTH] 同花顺 hsgtApi 北向资金获取失败: {e}", file=sys.stderr)

    # Fallback: 东财 push2 fflow（仅当同花顺不可用时）
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        params = "secid=1.000001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54&klt=101&lmt=30&ut=7eea3edcaed734bea9cbfc24409ed989"
        headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
        req = urllib.request.Request(f"{url}?{params}", headers=headers)
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        data = json.loads(raw)
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return None
        latest = klines[-1]
        parts = latest.split(",")
        sh_net = round(float(parts[1]) / 1e8, 2)
        sz_net = round(float(parts[2]) / 1e8, 2)
        total = round(sh_net + sz_net, 2)
        return {"sh": sh_net, "sz": sz_net, "total": total, "ma5": total, "consecutive": 0, "direction": "流入" if total > 0 else "流出"}
    except Exception as e2:
        print(f"[NORTH] 东财 fflow fallback 也失败: {e2}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 公告层 - 巨潮公告检索 · 周末消息面 + 个股公告
# 参考 a-stock-data SKILL §7.1
# ═══════════════════════════════════════════════════════════════

# 巨潮 orgId 映射表（动态获取 + 硬编码 fallback）
_CNINFO_ORG_CACHE = None

def _cninfo_org_map():
    """获取巨潮公告 orgId 映射表，自动缓存24h"""
    global _CNINFO_ORG_CACHE
    if _CNINFO_ORG_CACHE is not None:
        return _CNINFO_ORG_CACHE
    try:
        text = _http_get_text("http://www.cninfo.com.cn/new/data/szse_stock.json", timeout=15, retries=2)
        if text:
            data = json.loads(text)
            org_map = {}
            for item in data.get("stockList", []):
                code = str(item.get("code", ""))
                org_id = str(item.get("orgId", ""))
                if code and org_id:
                    org_map[code] = org_id
            if org_map:
                _CNINFO_ORG_CACHE = org_map
                return org_map
    except Exception:
        pass
    # 硬编码 fallback
    return {}

def _cninfo_org_id(code):
    """获取个股巨潮 orgId"""
    org_map = _cninfo_org_map()
    if code in org_map:
        return org_map[code]
    # fallback: 硬编码规则
    if code.startswith("6"):
        return f"gssh0{code}"
    elif code.startswith(("0", "3")):
        return f"gssz0{code}"
    elif code.startswith(("4", "8")):
        return f"gsbj0{code}"
    return None

def _cninfo_announcements(code, page_size=10, keyword=""):
    """巨潮公告检索 · 获取个股最新公告
    code: 6位股票代码
    返回: [{title, type, date, url}] 或 None
    参考: a-stock-data SKILL §7.1
    """
    org_id = _cninfo_org_id(code)
    if not org_id:
        return None
    try:
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.cninfo.com.cn/",
        }
        body = f"stock={code},{org_id}&tabName=fulltext&pageSize={page_size}&pageNum=1&isHLtitle=true"
        if keyword:
            body += f"&seDate={keyword}"
        data = _http_get_bytes(url, timeout=15, retries=2, extra_headers=headers)
        if not data:
            return None
        # 巨潮返回的是网页表单提交，需要特殊处理
        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode("utf-8"))
        announcements = result.get("announcements", [])
        if not announcements:
            return None
        out = []
        for item in announcements:
            anno_id = item.get("announcementId", "")
            out.append({
                "title": item.get("announcementTitle", ""),
                "type": item.get("announcementTypeName", ""),
                "date": datetime.fromtimestamp(item.get("announcementTime", 0) / 1000).strftime("%Y-%m-%d") if item.get("announcementTime") else "",
                "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={anno_id}&stockCode={code}" if anno_id else "",
            })
        return out
    except Exception as e:
        print(f"[CNINFO] 巨潮公告获取失败 {code}: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 舆情互动层 - 互动易批量问答 · 热门标的投资者问答
# 参考 a-stock-data SKILL §10.1
# ═══════════════════════════════════════════════════════════════

def _irm_batch_qa(codes, limit=3):
    """批量获取互动易问答（用于热门标的）
    codes: 股票代码列表
    返回: {code: [{question, answer, date}]}
    """
    result = {}
    for code in codes[:10]:  # 最多10只，避免太慢
        qa = _irm_qa(code, limit)
        if qa:
            result[code] = qa
        time.sleep(0.3)  # 防止请求过快
    return result


# ═══════════════════════════════════════════════════════════════
# 东财人气榜升级 · 使用 a-stock-data §10.2b 新端点
# ═══════════════════════════════════════════════════════════════

def _em_hot_rank_v2(top_n=50):
    """东财人气榜 V2 · 使用 emappdata 新端点（含 rank_change）
    返回: [{rank, name, code, price, chg_pct, rank_change}]
    参考: a-stock-data SKILL §10.2b
    """
    try:
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Referer": "https://emappdata.eastmoney.com/",
        }
        body = json.dumps({
            "appId": "appId01",
            "globalId": "786e4c21-70dc-435a-93bb-38",
            "marketType": "",
            "pageNo": 1,
            "pageSize": top_n,
        })
        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data", [])
        if not items:
            return None

        # 提取代码列表，批量补名称/价格
        codes = []
        for item in items:
            sc = item.get("sc", "")
            if sc.startswith("SZ"):
                codes.append(f"0.{sc[2:]}")
            elif sc.startswith("SH"):
                codes.append(f"1.{sc[2:]}")
        if not codes:
            return None

        # 批量拉取名称/价格
        secids = ",".join(codes)
        ulist_url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        ulist_params = {
            "ut": "f057cbcbce2a86e2866ab8877db1d059",
            "fltt": "2", "invt": "2",
            "fields": "f14,f3,f12,f2",
            "secids": secids,
        }
        ulist_req = urllib.request.Request(
            f"{ulist_url}?{urllib.parse.urlencode(ulist_params)}",
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
        )
        ulist_data = json.loads(urllib.request.urlopen(ulist_req, timeout=10).read().decode("utf-8"))
        ulist_map = {}
        if ulist_data.get("data") and ulist_data["data"].get("diff"):
            for d in ulist_data["data"]["diff"]:
                ulist_map[str(d.get("f12", ""))] = {
                    "name": d.get("f14", ""),
                    "price": float(d.get("f2", 0)),
                    "chg_pct": float(d.get("f3", 0)),
                }

        out = []
        for idx, item in enumerate(items, 1):
            code = item.get("sc", "").replace("SZ", "").replace("SH", "")
            info = ulist_map.get(code, {})
            out.append({
                "rank": idx,
                "name": info.get("name", item.get("name", "")),
                "code": code,
                "price": info.get("price"),
                "chg_pct": info.get("chg_pct"),
                "rank_change": item.get("rank_change", 0),
            })
        return out
    except Exception as e:
        print(f"[EM HOT V2] 东财人气榜V2获取失败: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 打板情绪增强 · 晋级率 + 赚钱效应 + 连板梯队
# 参考 a-stock-data SKILL §8.3
# ═══════════════════════════════════════════════════════════════

def _limit_up_sentiment_v2(date_str=None):
    """打板情绪 V2 · 完整情绪指标
    返回: {
        zt_count, zb_count, dt_count, break_rate, max_height,
        ladder: {板数: 家数},
        advance_rate: 晋级率%,
        profit_effect: 昨日涨停今日表现（平均涨幅%+红盘比例%）
    }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    zt = em_zt_pool(date_str)
    zb = em_zb_pool(date_str)
    dt = em_dt_pool(date_str)
    yzt = em_yzt_pool(date_str)

    zt_n = len(zt)
    zb_n = len(zb)
    dt_n = len(dt)
    br = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0
    max_h = max((s.get("limit_days", 1) for s in zt), default=0)

    # 连板梯队
    ladder = {}
    for s in zt:
        ld = s.get("limit_days", 1)
        ladder[ld] = ladder.get(ld, 0) + 1

    # 晋级率
    yzt_total = len(yzt)
    yzt_continue = sum(1 for s in yzt if s.get("pct", 0) >= 9.8)
    advance_rate = round(yzt_continue / yzt_total * 100, 1) if yzt_total > 0 else 0

    # 赚钱效应：昨日涨停今日平均涨幅 + 红盘比例
    profit_effect = None
    if yzt_total > 0:
        avg_pct = sum(s.get("pct", 0) for s in yzt) / yzt_total
        red_count = sum(1 for s in yzt if s.get("pct", 0) > 0)
        profit_effect = {
            "avg_pct": round(avg_pct, 2),
            "red_ratio": round(red_count / yzt_total * 100, 1),
        }

    return {
        "zt_count": zt_n,
        "zb_count": zb_n,
        "dt_count": dt_n,
        "break_rate": br,
        "max_height": max_h,
        "ladder": ladder,
        "advance_rate": advance_rate,
        "profit_effect": profit_effect,
    }

def _em_sector_rank():
    """东财行业板块排名 · 返回: [{name, code, chg_pct, up_count, down_count, lead_stock, lead_chg}]
    按涨跌幅降序排序。优先级：东财(独有) > 新浪(akshare)
    """
    url = "https://quote.eastmoney.com/center/api/hyboard/gethyfl"
    params = {"sort": "zdf", "order": "desc", "pagesize": "100"}
    try:
        data = em_get(url, params=params, timeout=10).json()
        if not data or "data" not in data:
            return None
        out = []
        for item in data["data"]:
            out.append({
                "name": item.get("name", ""),
                "code": item.get("code", ""),
                "chg_pct": float(item.get("zdf", 0)),
                "up_count": int(item.get("sz", 0)),
                "down_count": int(item.get("xd", 0)),
                "lead_stock": item.get("leadname", ""),
                "lead_code": str(item.get("leadcode", "")).replace("sh", "").replace("sz", ""),
                "lead_chg": float(item.get("leadzdf", 0)),
            })
        return out
    except Exception as e:
        print(f"[EM SECTOR] 东财行业排名获取失败: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 舆情互动层 - 东财人气榜 · 实时热门股票排名
# 参考 a-stock-data SKILL §10.3 · 同花顺人气榜备用
# ═══════════════════════════════════════════════════════════════

def _em_hot_rank(top_n=50):
    """东财人气榜 · 返回 [{rank, name, code, price, chg_pct, change}]
    change: 排名变化（正数=上升，负数=下降）
    """
    url = "https://emdata.eastmoney.com/apprank/getrank"
    params = {"type": "1", "p": "1", "ps": str(top_n)}
    try:
        data = em_get(url, params=params, timeout=10).json()
        if not data or "data" not in data:
            return None
        out = []
        for idx, item in enumerate(data["data"], 1):
            out.append({
                "rank": idx,
                "name": item.get("name", ""),
                "code": str(item.get("code", "")),
                "price": float(item.get("price", 0)),
                "chg_pct": float(item.get("change", 0)),
                "rank_change": int(item.get("diff", 0)),
            })
        return out
    except Exception as e:
        print(f"[EM HOT] 东财人气榜获取失败: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 信号层 - 全市场龙虎榜 · 每日上榜股票 + 净买额排名
# 参考 a-stock-data SKILL §3.5
# ═══════════════════════════════════════════════════════════════

def _em_lhb_daily(date_str=None):
    """东财每日全市场龙虎榜 · date_str=YYYYMMDD
    返回: [{code, name, net_buy, turnover, reason, institution}]
    net_buy: 净买入额(万元)，按净买额降序
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    elif len(date_str) == 8:
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    columns = "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLANATION,TURNOVER,AMOUNT,NET_BUY,RANK"
    filter_str = f"TRADE_DATE='{date_str}'"
    data = eastmoney_datacenter("ZGGGLRB", columns, filter_str=filter_str, page_size=100)
    if not data:
        return []
    out = []
    for item in data:
        net_buy = float(item.get("NET_BUY", 0)) / 10000  # 元 → 万元
        turnover = float(item.get("TURNOVER", 0))
        out.append({
            "code": str(item.get("SECURITY_CODE", "")),
            "name": item.get("SECURITY_NAME_ABBR", ""),
            "net_buy_wan": round(net_buy, 2),
            "turnover_yi": round(turnover / 1e8, 2),
            "reason": item.get("EXPLANATION", ""),
        })
    # 按净买入降序
    out.sort(key=lambda x: -x["net_buy_wan"])
    return out


# ═══════════════════════════════════════════════════════════════
# 信号层 - 限售解禁日历 · 未来90天待解禁
# 参考 a-stock-data SKILL §3.7
# ═══════════════════════════════════════════════════════════════

def _em_lockup_calendar(days_ahead=90):
    """未来N天内的限售解禁日历
    返回: [{code, name, date, volume(万股), price, market_cap(亿)}]
    """
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    columns = "CODE,NAME,DATE,COUNTTOTAL,PRICE,LTSZ"
    filter_str = f"DATE >= '{today}' AND DATE <= '{end_date}'"
    data = eastmoney_datacenter("GXJF", columns, filter_str=filter_str,
                               page_size=min(days_ahead * 2, 100), sort_columns="DATE", sort_types="1")
    if not data:
        return []
    out = []
    for item in data:
        volume = float(item.get("COUNTTOTAL", 0)) / 10000  # 股 → 万股
        price = float(item.get("PRICE", 0)) if item.get("PRICE") else None
        cap = volume * price / 10000 if price else None  # 万股 * 元/股 = 万元 → 亿 = /10000
        out.append({
            "code": str(item.get("CODE", "")),
            "name": item.get("NAME", ""),
            "date": item.get("DATE", ""),
            "volume_wan": round(volume, 2),
            "price": price,
            "cap_yi": round(cap, 2) if cap else None,
        })
    return out


# ═══════════════════════════════════════════════════════════════
# 舆情互动层 - 互动易问答 · 最近投资者问答
# 参考 a-stock-data SKILL §10.1
# ═══════════════════════════════════════════════════════════════

def _irm_qa(code, limit=5):
    """互动易问答 · 获取个股最近问答（投资者提问 + 公司回复）
    code: 6位股票代码
    返回: [{question, answer, date}] 或 None
    """
    url = "https://irm.cninfo.com.cn/api/irm/question/list"
    params = {"stockCode": code, "pageNum": 1, "pageSize": limit}
    headers = {
        "User-Agent": UA,
        "Referer": "https://irm.cninfo.com.cn/",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        if not data or "questions" not in data.get("data", {}):
            return None
        out = []
        for q in data["data"]["questions"]:
            out.append({
                "question": q.get("content", ""),
                "answer": q.get("answer", {}).get("content", "") if q.get("answer") else "",
                "date": q.get("publishDate", ""),
            })
        return out
    except Exception as e:
        print(f"[IRM QA] 互动易问答获取失败 {code}: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 行情层 - mootdx 优先级接入（TCP 优先，腾讯 fallback）
# 参考 a-stock-data SKILL §1.1 · 实现优先级备选
# ═══════════════════════════════════════════════════════════════

# mootdx 客户端（延迟创建，规避 0.11.x BESTIP 空串 bug）
_tdx_client = None

def tdx_client(market='std'):
    """创建 mootdx 客户端，规避 0.11.x BESTIP 空串 bug。
    顺序兜底: TCP探测 → bestip测速 → 裸factory → 抛异常
    """
    global _tdx_client
    if _tdx_client is not None:
        return _tdx_client
    _TDX_SERVERS = [
        ('119.97.185.59', 7709), ('124.70.133.119', 7709), ('116.205.183.150', 7709),
        ('123.60.73.44', 7709),  ('116.205.163.254', 7709), ('121.36.225.169', 7709),
        ('123.60.70.228', 7709), ('124.71.9.153', 7709),    ('110.41.147.114', 7709),
        ('124.71.187.122', 7709),
    ]
    from mootdx.quotes import Quotes
    for ip, port in _TDX_SERVERS:
        try:
            with socket.create_connection((ip, port), timeout=2.0):
                _tdx_client = Quotes.factory(market=market, server=(ip, port))
                return _tdx_client
        except Exception:
            continue
    try:
        _tdx_client = Quotes.factory(market=market, bestip=True)
        return _tdx_client
    except Exception:
        pass
    try:
        _tdx_client = Quotes.factory(market=market)
        return _tdx_client
    except Exception as e:
        _tdx_client = False
        raise RuntimeError(f"所有 mootdx 服务器均不可达: {e}")

def _mootdx_quotes(codes):
    """mootdx 批量实时报价 · 优先级: mootdx(TCP) → None (腾讯兜底)
    codes: ["688017", "000001", ...] 纯6位
    返回: {code: {"price", "open", "high", "low", "prev_close"}} 或 {}
    """
    try:
        client = tdx_client()
        # mootdx 需要带市场前缀
        prefixed = []
        for c in codes:
            if c.startswith(("6", "9")):
                prefixed.append(f"sh{c}")
            else:
                prefixed.append(f"sz{c}")
        quotes = client.quotes(symbol=prefixed)
        if quotes is None or len(quotes) == 0:
            return {}
        result = {}
        for c, q in zip(codes, quotes):
            if q is not None:
                result[c] = {
                    "price": q.get("price"),
                    "open": q.get("open"),
                    "high": q.get("high"),
                    "low": q.get("low"),
                    "prev_close": q.get("last_close"),
                }
        return result
    except Exception as e:
        print(f"[MOOTDX] 通达信行情失败（将走腾讯 fallback）: {e}", file=sys.stderr)
        return {}


# ═══════════════════════════════════════════════════════════════
# 行业排名优先级备选 · 东财直连(主) → 新浪(akshare fallback)
# ═══════════════════════════════════════════════════════════════

def _sector_rank_safe():
    """安全获取行业排名 · 优先级：东财直连(主，无需akshare) → 新浪(akshare fallback)
    返回: 同 _em_sector_rank 格式，或 None
    """
    # 优先东财（无需akshare，防封已内置）
    em_result = _em_sector_rank()
    if em_result is not None and len(em_result) > 0:
        return em_result
    # fallback to 新浪（需要akshare）
    print("[SECTOR] 东财行业排名失败，尝试新浪 fallback", file=sys.stderr)
    return _sector_spot_sina()


# ═══════════════════════════════════════════════════════════════
# 人气榜优先级备选 · 同花顺(主) → 东财(fallback)
# ═══════════════════════════════════════════════════════════════

def _hot_rank_safe(top_n=50):
    """安全获取人气榜 · 优先级：同花顺(主) → 东财(fallback)
    返回: 同 _ths_hot_rank 格式，或 None
    """
    ths_result = _ths_hot_rank()
    if ths_result is not None and len(ths_result) > 0:
        return ths_result[:top_n]
    # fallback to 东财
    print("[HOT RANK] 同花顺人气榜失败，尝试东财 fallback", file=sys.stderr)
    return _em_hot_rank(top_n)


# ═══════════════════════════════════════════════════════════════
# 共享连板天梯卡片构建（midday/close 共用，消除 ~200 行重复代码）
# ═══════════════════════════════════════════════════════════════

def build_ladder_card(date_str=None, webhook=None, log_func=None):
    """构建连板天梯卡片并发送到飞书。
    date_str: YYYYMMDD 格式
    webhook: 飞书 webhook URL
    log_func: 日志函数
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    if webhook is None:
        return  # 无 webhook 不发送

    try:
        all_zt = em_zt_pool(date_str)
        yzt = em_yzt_pool(date_str)
        ths = _ths_limit_up_pool(date_str)
    except Exception as e:
        if log_func:
            log_func(f"连板天梯数据获取失败: {e}")
        send_feishu_card(webhook, "📊 连板天梯", "数据获取失败，请稍后重试", "blue", log=log_func)
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

    # ── 构建概念题材纵深 ──
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
    # 题材纵深
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

    # 分卡发送
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > 16000:
        split_idx = text.find("**━━━ 未涨停的高标")
        if split_idx > 0:
            main_text = text[:split_idx].strip()
            fail_text = text[split_idx:].strip()
            send_feishu_card(webhook, "📊 连板天梯", main_text, "turquoise", log=log_func)
            time.sleep(0.3)
            send_feishu_card(webhook, "📊 连板天梯（续）", fail_text, "turquoise", log=log_func)
            return
    send_feishu_card(webhook, "📊 连板天梯", text, "turquoise", log=log_func)


# ═══════════════════════════════════════════════════════════════
# a-stock-data SKILL.md 自动拉取（每次启动时拉取最新版本）
# ═══════════════════════════════════════════════════════════════
_SKILL_CACHE = None
_SKILL_CACHE_PATH = Path("/tmp/uzi_a_stock_skill.md")

def fetch_latest_skill():
    """从 GitHub 拉取最新 a-stock-data SKILL.md，缓存到本地。
    每次自动化启动时调用，确保使用最新版本的数据端点。
    返回: SKILL.md 文本内容（str），失败返回 None
    """
    global _SKILL_CACHE
    SKILL_URL = "https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/SKILL.md"
    # 优先用缓存（24小时内有效）
    if _SKILL_CACHE_PATH.exists():
        age = time.time() - _SKILL_CACHE_PATH.stat().st_mtime
        if age < 86400:  # 24小时
            try:
                _SKILL_CACHE = _SKILL_CACHE_PATH.read_text(encoding="utf-8")
                return _SKILL_CACHE
            except Exception:
                pass
    try:
        text = _http_get_text(SKILL_URL, timeout=15, retries=2, encoding="utf-8")
        if text and len(text) > 10000:
            _SKILL_CACHE = text
            _SKILL_CACHE_PATH.write_text(text, encoding="utf-8")
            print(f"[SKILL] a-stock-data SKILL.md 已更新 ({len(text)} 字节)", file=sys.stderr)
            return text
    except Exception as e:
        print(f"[SKILL] 拉取 SKILL.md 失败: {e}，使用缓存", file=sys.stderr)
        if _SKILL_CACHE:
            return _SKILL_CACHE
        if _SKILL_CACHE_PATH.exists():
            try:
                _SKILL_CACHE = _SKILL_CACHE_PATH.read_text(encoding="utf-8")
                return _SKILL_CACHE
            except Exception:
                pass
    return None
