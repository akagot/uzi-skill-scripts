#!/usr/bin/env python3
"""
收盘复盘+尾盘抢筹自动化 (15:01)
- 交易日 15:01 执行
- 拉取收盘行情：大盘/板块/个股/资金/北向
- 全天盘面复盘
- 尾盘抢筹机会
- 明日策略研判
- 发送飞书分卡片
"""

import os
import sys
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 配置 ──
LOG_FILE = Path("/tmp/uzi_close_analysis.log")
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/96d30f0a-639b-40c8-8ed5-1028ea80bef9"
MAX_BYTES = 18000

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
                urllib.request.urlopen(req, timeout=30)
            return
        req = urllib.request.Request(FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
        log(f"飞书推送成功: {title}")
    except Exception as e:
        log(f"飞书推送失败: {e}")

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
    if chg is None:
        return "<font color='grey'>--</font>"
    color = "green" if chg >= 0 else "red"
    arrow = "📈" if chg >= 0 else "📉"
    return f"{arrow} <font color='{color}'>{chg:+.2f}%</font>"

# ── 1. 大盘指数收盘 ──
def get_index_close():
    log("拉取大盘指数收盘行情...")
    indices = []
    try:
        index_codes = {
            "1.000001": "上证指数",
            "1.000300": "沪深300",
            "1.000688": "科创50",
            "1.000905": "中证500",
            "0.399001": "深证成指",
            "0.399006": "创业板指",
        }
        fs = ",".join(index_codes.keys())
        url = f"https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2&fs={fs}&fields=f2,f3,f4,f5,f6,f12,f14"
        data = _fetch_json(url)
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"]:
                code = item.get("f12", "")
                if code in index_codes:
                    indices.append({
                        "name": index_codes[code],
                        "price": item.get("f2", 0),
                        "chg_pct": item.get("f3", 0),
                        "volume": item.get("f5", 0),
                        "turnover": item.get("f6", 0),
                    })
            log(f"大盘指数: {len(indices)} 个")
    except Exception as e:
        log(f"大盘指数获取失败: {e}")
    return indices

# ── 2. 行业板块 ──
def get_sector_close():
    log("拉取行业板块涨跌幅...")
    sectors = []
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=40&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f2,f3,f4,f6,f12,f14"
        data = _fetch_json(url)
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"][:40]:
                sectors.append({
                    "name": item.get("f14", ""),
                    "chg_pct": item.get("f3", 0),
                    "inflow": item.get("f6", 0) / 1e8,
                })
            log(f"行业板块: {len(sectors)} 个")
    except Exception as e:
        log(f"行业板块获取失败: {e}")
    return sectors

# ── 3. 涨停/跌停 ──
def get_limit_stocks():
    log("拉取涨停/跌停股...")
    up_stocks = []
    down_stocks = []
    try:
        # 涨停
        url1 = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=80&pn=1&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f5,f6,f12,f14&fipt=0&force=1"
        data1 = _fetch_json(url1)
        if data1 and data1.get("data", {}).get("diff"):
            for item in data1["data"]["diff"]:
                if item.get("f3", 0) >= 9.5:
                    up_stocks.append({
                        "name": item.get("f14", ""),
                        "code": item.get("f12", ""),
                        "chg_pct": item.get("f3", 0),
                        "turnover": item.get("f6", 0) / 1e8,
                    })
                    if len(up_stocks) >= 20:
                        break

        # 跌停
        url2 = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=100&pn=1&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f5,f6,f12,f14&fipt=0&force=1"
        data2 = _fetch_json(url2)
        if data2 and data2.get("data", {}).get("diff"):
            for item in sorted(data2["data"]["diff"], key=lambda x: x.get("f3", 0)):
                if item.get("f3", 0) <= -9.5:
                    down_stocks.append({
                        "name": item.get("f14", ""),
                        "code": item.get("f12", ""),
                        "chg_pct": item.get("f3", 0),
                        "turnover": item.get("f6", 0) / 1e8,
                    })
                    if len(down_stocks) >= 10:
                        break
        log(f"涨停{len(up_stocks)}只 / 跌停{len(down_stocks)}只")
    except Exception as e:
        log(f"涨跌停股获取失败: {e}")
    return up_stocks, down_stocks

# ── 4. 尾盘抢筹股（14:30后放量拉升） ──
def get_late_session_stocks():
    log("拉取尾盘抢筹股...")
    try:
        # 涨幅榜 + 成交活跃
        url = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=30&pn=1&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f5,f6,f12,f14&fipt=0&force=1"
        data = _fetch_json(url)
        stocks = []
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"][:20]:
                chg = item.get("f3", 0)
                turnover = item.get("f6", 0) / 1e8
                # 涨幅3%-9%之间 + 成交活跃 = 尾盘抢筹候选
                if 3.0 <= chg < 9.5 and turnover > 1:
                    stocks.append({
                        "name": item.get("f14", ""),
                        "code": item.get("f12", ""),
                        "chg_pct": chg,
                        "turnover": turnover,
                    })
        return sorted(stocks, key=lambda x: x["turnover"], reverse=True)[:8]
    except Exception as e:
        log(f"尾盘抢筹股获取失败: {e}")
        return []

# ── 5. 主力资金流向 ──
def get_capital_flow():
    log("拉取主力资金流向...")
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=30&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f2,f3,f6,f12,f14"
        data = _fetch_json(url)
        flows = []
        if data and data.get("data", {}).get("diff"):
            for item in data["data"]["diff"][:30]:
                flows.append({
                    "name": item.get("f14", ""),
                    "inflow": item.get("f6", 0) / 1e8,
                })
        return sorted(flows, key=lambda x: x["inflow"], reverse=True)
    except Exception as e:
        log(f"资金流向获取失败: {e}")
        return []

# ── 6. 主逻辑 ──
def main():
    log("=" * 60)
    log("收盘复盘+尾盘抢筹启动")

    today = datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[today.weekday()]
    date_display = today.strftime("%Y年%m月%d日") + f" {wd}"

    # 1. 大盘指数
    indices = get_index_close()
    time.sleep(0.3)

    # 2. 行业板块
    sectors = get_sector_close()
    time.sleep(0.3)

    # 3. 涨停/跌停
    up_stocks, down_stocks = get_limit_stocks()
    time.sleep(0.3)

    # 4. 尾盘抢筹
    late_stocks = get_late_session_stocks()
    time.sleep(0.3)

    # 5. 资金流向
    flows = get_capital_flow()

    # ─── 卡片1: 收盘概览 ───
    overview = [f"**{date_display} 收盘复盘（15:00）**", ""]
    if indices:
        overview.append("**主要指数收盘**")
        for idx in indices:
            overview.append(f"  • **{idx['name']}**: {idx['price']:.2f}　{_color_chg(idx['chg_pct'])}")
    else:
        overview.append("⚠️ 大盘指数数据获取失败")

    overview.append("")
    if up_stocks is not None and down_stocks is not None:
        overview.append(f"**涨停 {len(up_stocks)} 只 / 跌停 {len(down_stocks)} 只**")

    overview.append("")
    overview.append("数据来源：东方财富 | 自动化生成")
    send_card("📊 收盘概览", "\n".join(overview), "blue")
    time.sleep(0.5)

    # ─── 卡片2: 行业板块 ───
    sector_lines = [f"**{date_display} 行业板块涨跌幅**", ""]
    if sectors:
        sector_lines.append("**领涨板块 TOP8**")
        for s in sorted(sectors, key=lambda x: x["chg_pct"], reverse=True)[:8]:
            sector_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**")
        sector_lines.append("")
        sector_lines.append("**领跌板块 TOP8**")
        for s in sorted(sectors, key=lambda x: x["chg_pct"])[:8]:
            sector_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**")
    else:
        sector_lines.append("⚠️ 行业板块数据获取失败")
    send_card("🏭 行业板块", "\n".join(sector_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片3: 涨停/跌停 ───
    limit_lines = [f"**{date_display} 涨停跌停**", ""]
    limit_lines.append(f"**涨停 {len(up_stocks)} 只**")
    for s in up_stocks[:10]:
        limit_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**({s['code']})  成交{s['turnover']:.2f}亿")
    limit_lines.append("")
    limit_lines.append(f"**跌停 {len(down_stocks)} 只**")
    for s in down_stocks[:5]:
        limit_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**({s['code']})")
    send_card("⚡ 涨停跌停", "\n".join(limit_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片4: 主力资金 ───
    flow_lines = [f"**{date_display} 主力资金净流入**", ""]
    if flows:
        flow_lines.append("**资金净流入 TOP8**")
        for f in flows[:8]:
            color = "green" if f["inflow"] >= 0 else "red"
            flow_lines.append(f"  <font color='{color}'>{f['inflow']:+.2f}亿</font> **{f['name']}**")
        flow_lines.append("")
        flow_lines.append("**资金净流出 TOP5**")
        for f in flows[-5:]:
            color = "green" if f["inflow"] >= 0 else "red"
            flow_lines.append(f"  <font color='{color}'>{f['inflow']:+.2f}亿</font> **{f['name']}**")
    else:
        flow_lines.append("⚠️ 资金流向数据获取失败")
    send_card("💰 主力资金", "\n".join(flow_lines), "blue")
    time.sleep(0.5)

    # ─── 卡片5: 尾盘抢筹 ───
    late_lines = [f"**{date_display} 尾盘抢筹候选**", ""]
    if late_stocks:
        late_lines.append("成交活跃 + 涨幅居前")
        for s in late_stocks:
            late_lines.append(f"  {_color_chg(s['chg_pct'])} **{s['name']}**({s['code']})  成交{s['turnover']:.2f}亿")
    else:
        late_lines.append("今日无明显尾盘抢筹标的")
    send_card("🏃 尾盘抢筹", "\n".join(late_lines), "green")
    time.sleep(0.5)

    # ─── 卡片6: 明日策略 ───
    # 当日盘面总结
    if indices:
        avg_chg = sum(idx["chg_pct"] for idx in indices) / len(indices)
        if avg_chg > 1.0:
            day_summary = "强势上涨，多头主导"
            color = "green"
        elif avg_chg < -1.0:
            day_summary = "弱势下跌，空头主导"
            color = "red"
        else:
            day_summary = "震荡整理，方向不明"
            color = "orange"
    else:
        day_summary = "数据获取失败"
        color = "orange"

    judgment = f"""**明日策略研判**

今日盘面：<font color='{color}'>{day_summary}</font>

**复盘要点：**
• 涨停股数量：{len(up_stocks)}只（市场情绪）
• 跌停股数量：{len(down_stocks)}只（风险偏好）
• 成交量：关注较昨日变化
• 板块轮动：领涨板块的持续性

**明日关注：**
• 美股/欧股隔夜表现
• 政策面：国常会、央行动态
• 财报季：业绩超预期标的
• 板块轮动：跟随资金走向

**操作建议：**
▸ 强势行情：持股待涨，关注龙头
▸ 弱势行情：减仓观望，避开弱势股
▸ 震荡行情：高低切换，控制仓位

---
数据来源：东方财富
声明：以上内容仅供市场信息参考，不构成任何投资建议"""
    send_card("🧠 明日策略", judgment, "purple")

    log("收盘复盘+尾盘抢筹完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"执行异常: {traceback.format_exc()}")
        sys.exit(1)
