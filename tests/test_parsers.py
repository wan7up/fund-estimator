from __future__ import annotations

import json
from datetime import datetime

from fund_estimator.data_sources.eastmoney import (
    _is_notfound_redirect,
    infer_market,
    parse_fund_code_search,
    parse_holdings_response,
    parse_pingzhong_profile,
    parse_quote_response,
    parse_single_quote_response,
    to_eastmoney_secid,
)
from fund_estimator.data_sources.sina import parse_sina_quotes


def test_parse_fund_code_search():
    text = 'var r = [["001438","YFDRXHHE","易方达瑞享混合E","混合型"],["000001","HXCZHH","华夏成长混合","混合型"]];'

    results = parse_fund_code_search(text)

    assert results[0].code == "001438"
    assert results[0].name == "易方达瑞享混合E"
    assert results[0].fund_type == "混合型"


def test_notfound_redirect_is_detected():
    import httpx

    response = httpx.Response(
        301,
        headers={"location": "https://fund.eastmoney.com/notfound.html"},
        request=httpx.Request("GET", "https://fund.eastmoney.com/pingzhongdata/000157.js"),
    )

    assert _is_notfound_redirect(response) is True


def test_parse_pingzhong_profile():
    ts = int(datetime(2026, 5, 25).timestamp() * 1000)
    text = f'''
    var fS_name = "易方达瑞享混合E";
    var Data_netWorthTrend = [
      {{"x": {ts - 86400000}, "y": 9.7000, "equityReturn": 0.1, "unitMoney": ""}},
      {{"x": {ts}, "y": 9.8255, "equityReturn": 1.29, "unitMoney": ""}}
    ];
    var Data_ACWorthTrend = [[{ts}, 9.8255]];
    var fund_sourceRate = "1.50";
    var fund_Rate = "0.15";
    var fund_minsg = "10";
    var syl_1n = "86.5";
    var syl_6y = "42.2";
    var syl_3y = "31.4";
    var syl_1y = "18.6";
    var Data_fluctuationScale = {{"categories":["2026-03-31"],"series":[{{"y":12.34,"mom":"1.00%"}}]}};
    var Data_assetAllocation = {{"series":[
      {{"name":"股票占净比","data":[86.2]}},
      {{"name":"债券占净比","data":[0.0]}},
      {{"name":"现金占净比","data":[8.1]}},
      {{"name":"净资产","data":[12.34]}}
    ],"categories":["2026-03-31"]}};
    var Data_currentFundManager = [{{"name":"示例经理","star":4,"workTime":"5年又120天","fundSize":"120.00亿(8只基金)"}}];
    var Data_rateInSimilarType = [{{"x": {ts}, "y": 12, "sc": "3280"}}];
    var Data_rateInSimilarPersent = [[{ts}, 99.63]];
    '''

    profile = parse_pingzhong_profile("001438", text, fund_type="混合型")

    assert profile.code == "001438"
    assert profile.name == "易方达瑞享混合E"
    assert profile.last_nav == 9.8255
    assert profile.previous_nav == 9.7
    assert profile.actual_change_pct == 1.29
    assert profile.nav_date.isoformat() == "2026-05-25"
    assert profile.details.stage_returns.one_month_pct == 18.6
    assert profile.details.stage_returns.one_year_pct == 86.5
    assert profile.details.asset_allocation.stock_pct == 86.2
    assert profile.details.asset_allocation.net_asset_billion == 12.34
    assert profile.details.trading.current_rate_pct == 0.15
    assert profile.details.trading.min_purchase_amount == 10
    assert profile.details.managers[0].name == "示例经理"
    assert profile.details.similar_rank.rank == 12
    assert profile.details.similar_rank.percentile_pct == 99.63


def test_parse_holdings_response_from_html():
    html = """
    <div>截止日期：2026-03-31</div>
    <table>
      <tr><td>1</td><td>600519</td><td>贵州茅台</td><td>1600.00</td><td>1.20%</td><td>资讯</td><td>9.80%</td></tr>
      <tr><td>2</td><td>300750</td><td>宁德时代</td><td>200.00</td><td>-0.30%</td><td>资讯</td><td>8.60%</td></tr>
    </table>
    """

    holdings = parse_holdings_response("001438", html)

    assert holdings.holdings_date.isoformat() == "2026-03-31"
    assert holdings.items[0].stock_code == "600519"
    assert holdings.items[0].stock_name == "贵州茅台"
    assert holdings.items[0].market == "SH"
    assert holdings.top10_weight_sum == 18.4


def test_parse_quote_response():
    payload = {
        "data": {
            "diff": [
                {"f12": "600519", "f14": "贵州茅台", "f2": 1702.3, "f3": 1.33, "f18": 1680.0}
            ]
        }
    }

    quotes = parse_quote_response(payload)

    assert quotes["600519"].latest_price == 1702.3
    assert quotes["600519"].change_pct == 1.33
    assert infer_market("000001") == "SZ"
    assert infer_market("161128") == "SZ"
    assert infer_market("510300") == "SH"
    assert to_eastmoney_secid("600519") == "1.600519"
    assert to_eastmoney_secid("161128") == "0.161128"
    assert json.dumps(payload)


def test_parse_single_quote_response():
    payload = {
        "data": {
            "f43": 65901,
            "f57": "300502",
            "f58": "新易盛",
            "f60": 60677,
            "f170": 861,
        }
    }

    quote = parse_single_quote_response(payload)

    assert quote is not None
    assert quote.stock_code == "300502"
    assert quote.stock_name == "新易盛"
    assert quote.latest_price == 659.01
    assert quote.previous_close == 606.77
    assert quote.change_pct == 8.61


def test_parse_sina_quotes_uses_previous_close_before_open():
    text = (
        'var hq_str_sz002318="久立特材,0.000,26.160,0.000,0.000,0.000,0.000,0.000,'
        '0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,'
        '0,0.000,0,0.000,0,0.000,2026-05-26,09:08:36,00";'
    )

    quotes = parse_sina_quotes(text)

    assert quotes["002318"].stock_name == "久立特材"
    assert quotes["002318"].latest_price == 26.16
    assert quotes["002318"].previous_close == 26.16
    assert quotes["002318"].change_pct == 0.0
