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
