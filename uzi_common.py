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
import socket
import re
import sys
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
    color = "green" if chg >= 0 else "red"
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
    ("000001", "上证指数"), ("399001", "深证成指"),
    ("399006", "创业板指"), ("000688", "科创50"),
    ("000300", "沪深300"), ("000016", "上证50"),
    ("399905", "中证500"),
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
    """A 股大盘指数"""
    pairs = [(c, "A") for c, _ in A_SHARE_INDICES]
    qs = fetch_qt_quotes(pairs)
    by_code = {q["code"]: q for q in qs}
    out = []
    for c, name in A_SHARE_INDICES:
        q = by_code.get(c)
        if q and q.get("price") is not None:
            out.append({"name": name, "code": c, "price": q["price"], "chg_pct": q.get("change_pct")})
    return out


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
# akshare 涨停池 / 跌停池 / 连板池（真实数据，非代理）
# ═══════════════════════════════════════════════════════════════
def _fetch_limit_pool_ak(date_str=None):
    """涨停池 · akshare.stock_zt_pool_em
    返回 list[dict] · 失败时回退到活跃股代理
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or len(df) == 0:
            raise ValueError("涨停池为空")
        out = []
        for _, row in df.iterrows():
            out.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "chg_pct": float(row.get("涨跌幅", 0)),
                "price": float(row.get("最新价", 0)),
                "amount": float(row.get("成交额", 0)),
                "mcap": float(row.get("流通市值", 0)),
                "turnover": float(row.get("换手率", 0)),
                "seal_amount": float(row.get("封板资金", 0)),
                "seal_time": str(row.get("最后封板时间", "")),
                "break_count": int(row.get("炸板次数", 0)),
                "consecutive": int(row.get("连板数", 0)),
                "industry": str(row.get("所属行业", "")),
            })
        return out
    except Exception as e:
        print(f"[AK LIMIT] 涨停池不可用: {e}", file=sys.stderr)
        return None


def _fetch_dt_pool_ak(date_str=None):
    """跌停池 · akshare.stock_zt_pool_dtgc_em"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        import akshare as ak
        df = ak.stock_zt_pool_dtgc_em(date=date_str)
        if df is None or len(df) == 0:
            raise ValueError("跌停池为空")
        out = []
        for _, row in df.iterrows():
            out.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "chg_pct": float(row.get("涨跌幅", 0)),
                "price": float(row.get("最新价", 0)),
                "amount": float(row.get("成交额", 0)),
                "mcap": float(row.get("流通市值", 0)),
                "turnover": float(row.get("换手率", 0)),
                "seal_amount": float(row.get("封单资金", 0)),
                "consecutive": int(row.get("连续跌停", 0)),
                "industry": str(row.get("所属行业", "")),
            })
        return out
    except Exception as e:
        print(f"[AK LIMIT] 跌停池不可用: {e}", file=sys.stderr)
        return None


def _fetch_strong_pool_ak(date_str=None):
    """连板股 · akshare.stock_zt_pool_strong_em · 连板+涨停统计"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    try:
        import akshare as ak
        df = ak.stock_zt_pool_strong_em(date=date_str)
        if df is None or len(df) == 0:
            return []
        out = []
        for _, row in df.iterrows():
            out.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "chg_pct": float(row.get("涨跌幅", 0)),
                "price": float(row.get("最新价", 0)),
                "limit_price": float(row.get("涨停价", 0)),
                "amount": float(row.get("成交额", 0)),
                "mcap": float(row.get("流通市值", 0)),
                "turnover": float(row.get("换手率", 0)),
                "zt_stat": str(row.get("涨停统计", "")),
                "reason": str(row.get("入选理由", "")),
                "industry": str(row.get("所属行业", "")),
            })
        return out
    except Exception as e:
        print(f"[AK STRONG] 连板池不可用: {e}", file=sys.stderr)
        return None


def _fetch_limit_pool_safe(date_str=None):
    """涨停池 · akshare 优先，失败回退到活跃股代理"""
    ak_result = _fetch_limit_pool_ak(date_str)
    if ak_result is not None:
        return ak_result, "akshare"
    # fallback: 活跃股代理
    stocks = _a_share_active_stocks()
    proxy = sorted([s for s in stocks if s.get("change_pct", 0) >= 9.5],
                   key=lambda x: -(x.get("change_pct") or 0))[:15]
    return proxy, "proxy"


def _fetch_dt_pool_safe(date_str=None):
    """跌停池 · akshare 优先，失败回退到活跃股代理"""
    ak_result = _fetch_dt_pool_ak(date_str)
    if ak_result is not None:
        return ak_result, "akshare"
    stocks = _a_share_active_stocks()
    proxy = sorted([s for s in stocks if s.get("change_pct", 0) <= -9.5],
                   key=lambda x: (x.get("change_pct") or 0))[:15]
    return proxy, "proxy"


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


def _theme_heat_from_pools(limit_up, limit_down, strong_pool):
    """综合题材热度分析
    返回: {
        "up_industries": [(行业, 数量, 股票列表), ...],
        "down_industries": [(行业, 数量, 股票列表), ...],
        "strong_themes": {行业: {"count": N, "stocks": [...]}},  # 连板题材
        "hot_sectors": [...],  # 新浪行业领涨 TOP5
        "cold_sectors": [...],  # 新浪行业领跌 TOP5
    }
    """
    result = {
        "up_industries": _aggregate_pool_by_industry(limit_up) if limit_up else [],
        "down_industries": _aggregate_pool_by_industry(limit_down) if limit_down else [],
        "strong_themes": {},
        "hot_sectors": [],
        "cold_sectors": [],
    }
    # 连板题材
    if strong_pool:
        for s in strong_pool:
            ind = s.get("industry", "其他")
            if ind not in result["strong_themes"]:
                result["strong_themes"][ind] = {"count": 0, "stocks": []}
            result["strong_themes"][ind]["count"] += 1
            result["strong_themes"][ind]["stocks"].append(s)
    # 新浪行业
    sectors = _sector_spot_sina()
    if sectors:
        result["hot_sectors"] = [s for s in sectors if (s.get("chg_pct") or 0) > 0][:5]
        result["cold_sectors"] = [s for s in sectors if (s.get("chg_pct") or 0) < 0][-5:]
        result["cold_sectors"].reverse()  # 跌幅从大到小
    return result


def _theme_heat_safe(date_str=None):
    """安全获取题材热度 · 涨停池+跌停池+连板池+新浪行业"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    up, up_src = _fetch_limit_pool_safe(date_str)
    down, down_src = _fetch_dt_pool_safe(date_str)
    strong = _fetch_strong_pool_ak(date_str)
    heat = _theme_heat_from_pools(up, down, strong)
    heat["up_src"] = up_src
    heat["down_src"] = down_src
    heat["up_count"] = len(up)
    heat["down_count"] = len(down)
    heat["strong_count"] = len(strong) if strong else 0
    return heat
