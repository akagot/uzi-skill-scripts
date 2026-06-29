#!/usr/bin/env python3
"""
早盘竞价+大盘分析自动化 (9:26)
- 交易日 9:26 执行
- 拉取竞价行情：上证/深证/创业板/科创板 + 行业板块涨跌幅
- 集合竞价异动个股（涨跌幅榜、量比异动）
- 发送飞书分卡片
- 全部只依赖 workspace 自身，不需 git clone
"""

import os
import sys
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 配置 ──
WORKSPACE = Path("/workspace")
LOG_FILE = Path("/tmp/uzi_morning_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000

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

# ── 飞书卡片 ──
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
                urllib.request.urlopen(req, timeout=30)
            return
        req = urllib.request.Request(FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
        log(f"飞书推送成功: {title}")
    except Exception as e:
        log(f"飞书推送失败: {e}")

# ── 工具函数 ──
def _fetch_json(url, timeout=15, headers=None):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log(f"HTTP请求失败 {url[:80]}: {e}")
        return None

def _color_chg(chg):
    """根据涨跌幅返回带颜色标签"""
    if chg is None:
        return "<font color='grey'>--</font>"
    color = "green" if chg >= 0 else "red"
    arrow = "📈" if chg >= 0 else "📉"
    return f"{arrow} <font color='{color}'>{chg:+.2f}%</font>"

# ── 1. 拉取大盘指数竞价行情 ──
def get_index_auction():
    """东财接口获取主要指数实时行情"""
    log("拉取大盘指数竞价行情...")
    indices = []
    try:
        # 主要指数代码
        index_codes = {
            "1.000001": "上证指数",
            "1.000300": "沪深300",
            "1.000688": "科创50",
            "1.000905": "中证500",
            "1.000852": "中证1000",
            "0.399001": "深证成指",
            "0.399006": "创业板指",
            "1.000016": "上证50",
        }
        fs = ",".join(index_codes.keys())
        url = f"https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2&fs={fs}&fields=f2,f3,f4,f12,f14"
        data = _fetch_json(url)
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"]:
                code = item.get("f12", "")
                if code in index_codes:
                    indices.append({
                        "name": index_codes[code],
                        "code": code,
                        "price": item.get("f2", 0),
                        "chg_pct": item.get("f3", 0),
                    })
            log(f"大盘指数: {len(indices)} 个")
    except Exception as e:
        log(f"大盘指数获取失败: {e}")
    return indices

# ── 2. 拉取行业板块涨跌幅 ──
def get_sector_auction():
    """东财行业板块涨跌幅排行"""
    log("拉取行业板块涨跌幅...")
    sectors = []
    try:
        # 申万行业 / 东财行业
        url = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=30&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f2,f3,f4,f12,f14"
        data = _fetch_json(url)
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"][:20]:
                sectors.append({
                    "name": item.get("f14", ""),
                    "chg_pct": item.get("f3", 0),
                    "code": item.get("f12", "")
                })
            log(f"行业板块: {len(sectors)} 个")
    except Exception as e:
        log(f"行业板块获取失败: {e}")
    return sectors

# ── 3. 拉取涨停/异动股（竞价阶段） ──
def get_auction_stocks():
    """竞价阶段涨跌幅/量比异动股"""
    log("拉取竞价异动股...")
    stocks = []
    try:
        # 涨幅榜
        url1 = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f5,f6,f12,f14&fipt=0&force=1"
        data1 = _fetch_json(url1)
        if data1 and data1.get("data", {}).get("diff"):
            for item in data1["data"]["diff"][:10]:
                stocks.append({
                    "name": item.get("f14", ""),
                    "code": item.get("f12", ""),
                    "price": item.get("f2", 0),
                    "chg_pct": item.get("f3", 0),
                    "volume": item.get("f5", 0),
                    "turnover": item.get("f6", 0),
                    "type": "涨停"
                })

        # 跌幅榜
        url2 = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f5,f6,f12,f14&fipt=0&force=1"
        data2 = _fetch_json(url2)
        if data2 and data2.get("data", {}).get("diff"):
            for item in sorted(data2["data"]["diff"], key=lambda x: x.get("f3", 0))[:5]:
                stocks.append({
                    "name": item.get("f14", ""),
                    "code": item.get("f12", ""),
                    "price": item.get("f2", 0),
                    "chg_pct": item.get("f3", 0),
                    "volume": item.get("f5", 0),
                    "turnover": item.get("f6", 0),
                    "type": "跌停"
                })
        log(f"异动股: {len(stocks)} 只")
    except Exception as e:
        log(f"异动股获取失败: {e}")
    return stocks

# ── 4. 拉取北向资金 ──
def get_north_flow():
    """北向资金净流入"""
    log("拉取北向资金...")
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2&fs=m:1+t:1&fields=f2,f3,f4,f12,f14"
        data = _fetch_json(url)
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"]:
                name = item.get("f14", "")
                if "北向" in name or "沪股通" in name or "深股通" in name:
                    return {
                        "name": name,
                        "chg_pct": item.get("f3", 0),
                        "amount": item.get("f4", 0) / 1e8  # 转亿
                    }
    except Exception as e:
        log(f"北向资金获取失败: {e}")
    return None

# ── 5. 主逻辑 ──
def main():
    log("=" * 60)
    log("早盘竞价+大盘分析启动")

    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[today.weekday()]
    date_display = today.strftime("%Y年%m月%d日") + f" {wd}"

    # 1. 大盘指数
    indices = get_index_auction()
    time.sleep(0.3)

    # 2. 行业板块
    sectors = get_sector_auction()
    time.sleep(0.3)

    # 3. 异动股
    stocks = get_auction_stocks()
    time.sleep(0.3)

    # 4. 北向资金
    north = get_north_flow()

    # ─── 卡片1: 大盘概览 ───
    overview_lines = [f"**{date_display} 早盘竞价概览**", ""]
    if indices:
        overview_lines.append("**主要指数**")
        for idx in indices:
            overview_lines.append(f"  • **{idx['name']}**: {idx['price']:.2f}　{_color_chg(idx['chg_pct'])}")
    else:
        overview_lines.append("⚠️ 大盘指数数据获取失败")

    if north:
        overview_lines.append("")
        overview_lines.append(f"**北向资金**: {north['name']} 净流入 {north['amount']:+.2f} 亿")

    overview_lines.append("")
    overview_lines.append("数据来源：东方财富 | 自动化生成")

    send_card("📊 早盘概览", "\n".join(overview_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片2: 行业板块 ───
    sector_lines = [f"**{date_display} 行业板块涨跌幅**", ""]
    if sectors:
        # 涨幅前5
        sector_lines.append("**领涨板块**")
        for s in sorted(sectors, key=lambda x: x["chg_pct"], reverse=True)[:5]:
            sector_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**")
        sector_lines.append("")
        sector_lines.append("**领跌板块**")
        for s in sorted(sectors, key=lambda x: x["chg_pct"])[:5]:
            sector_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**")
    else:
        sector_lines.append("⚠️ 行业板块数据获取失败")

    send_card("🏭 行业板块", "\n".join(sector_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片3: 异动股 ───
    stock_lines = [f"**{date_display} 竞价异动股**", ""]
    if stocks:
        up_stocks = [s for s in stocks if s["type"] == "涨停"]
        down_stocks = [s for s in stocks if s["type"] == "跌停"]

        if up_stocks:
            stock_lines.append("**涨停/强势股**")
            for s in up_stocks[:8]:
                stock_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**({s['code']})")
            stock_lines.append("")

        if down_stocks:
            stock_lines.append("**跌停/弱势股**")
            for s in down_stocks[:5]:
                stock_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**({s['code']})")
    else:
        stock_lines.append("⚠️ 异动股数据获取失败")

    send_card("⚡ 竞价异动", "\n".join(stock_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片4: 开盘研判 ───
    # 大盘情绪判断
    if indices:
        avg_chg = sum(idx["chg_pct"] for idx in indices) / len(indices)
        if avg_chg > 0.3:
            sentiment = "竞价整体偏暖，指数小幅高开"
            color = "green"
        elif avg_chg < -0.3:
            sentiment = "竞价整体偏冷，指数小幅低开"
            color = "red"
        else:
            sentiment = "竞价表现平淡，指数接近平开"
            color = "orange"
    else:
        sentiment = "数据获取失败，关注开盘后表现"
        color = "orange"

    judgment = f"""**早盘研判**

竞价情绪：<font color='{color}'>{sentiment}</font>

**关注要点：**
• 开盘后30分钟量能是否放大
• 板块联动效应是否清晰
• 异动股持续性（是否打开涨停/跌停）
• 北向资金动向

---
数据来源：东方财富
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_card("🧠 竞价研判", judgment, "purple")

    log("早盘竞价+大盘分析完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)
