from __future__ import annotations

from fund_estimator.data_sources.sina import parse_sina_quotes, to_sina_symbol


def test_parse_sina_quotes():
    text = (
        'var hq_str_sz300502="新易盛,620.130,606.770,659.010,660.060,595.010,'
        '659.010,659.030,47689344,30125178942.270,1578,659.010,82900,659.000,'
        '100,658.990,100,658.900,1500,658.880,6100,659.030,100,659.040,600,'
        '659.060,200,659.070,300,659.100,2026-05-25,15:35:30,00,D|7800|5140278.000";'
    )

    quotes = parse_sina_quotes(text)

    quote = quotes["300502"]
    assert quote.stock_name == "新易盛"
    assert quote.latest_price == 659.01
    assert quote.previous_close == 606.77
    assert round(quote.change_pct, 2) == 8.61
    assert to_sina_symbol("600519") == "sh600519"
    assert to_sina_symbol("300502") == "sz300502"
