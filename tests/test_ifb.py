import datetime as dt

import httpx
import pytest
from conftest import fixture

from ifb_mcp import http
from ifb_mcp.ifb import from_ms, jdate, num, to_jalali

pytestmark = pytest.mark.anyio

ASMX = "/HistoricalInstrumentData.asmx/"
TOOLS = {
    "ifb_search",
    "ifb_yields",
    "ifb_instrument",
    "ifb_quote",
    "ifb_history",
    "ifb_ipos",
    "ifb_underwritings",
    "ifb_announcements",
    "ifb_article",
    "ifb_market_summary",
    "ifb_yield_curve",
    "ifb_sukuk_flows",
    "ifb_market_watch",
    "ifb_index_history",
    "ifb_corporate_actions",
    "ifb_halted",
}


def test_number_and_date_helpers():
    assert num("1,000,000") == 1_000_000
    assert num("58,602/74") == 58602.74
    assert num("4/04B") == 4_040_000_000
    assert num("2,550B") == 2_550_000_000_000
    assert num("42/53%") == 42.53
    assert num("سررسید شده") is None
    assert jdate("17:45:00 1405/08/09") == "1405/08/09" and jdate("1405-8-9") == "1405/08/09"
    assert to_jalali(dt.date(2026, 10, 31)) == "1405/08/09"
    assert to_jalali(dt.date(2025, 3, 21)) == "1404/01/01"
    assert to_jalali(dt.date(2026, 3, 20)) == "1404/12/29"
    assert to_jalali(dt.date(2025, 3, 20)) == "1403/12/30"  # leap year: Esfand has 30 days
    assert to_jalali(dt.date(2013, 3, 21)) == "1392/01/01"
    assert from_ms(1793133000000) == "1405/08/06"  # Tehran midnight of 2026-10-28


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name
        assert "title" not in str(t.input_schema.get("properties", {}).values()), t.name


async def test_search_splits_symbol_name_and_market(client, site):
    site[ASMX + "GetAllSymbolsForSearch"] = fixture("search.json")
    r = (await client.call_tool("ifb_search", {"query": "اخزا301"})).structured_content
    first = r["results"][0]
    assert r["count"] == 3 and first["id"] == 37012 and first["symbol"] == "اخزا3011"
    assert first["market"] == "ابزارنوین مالی" and first["isin"] == "IRB3TR260661"
    assert site.calls[0].method == "POST" and b'"q":"' in site.calls[0].content


async def test_yields_filters_and_ranks(client, site):
    site["/YTM.aspx"] = fixture("ytm.html")
    r = (await client.call_tool("ifb_yields", {"kind": "treasury", "limit": 50})).structured_content
    papers = r["papers"]
    assert papers and all(p["kind"] == "treasury" for p in papers)
    assert all(p["ytm_pct"] is not None and "matured" not in p for p in papers)  # matured papers are hidden
    ytms = [p["ytm_pct"] for p in papers]
    assert ytms == sorted(ytms, reverse=True)
    p202 = next(p for p in papers if p["symbol"] == "اخزا202")
    assert p202["id"] == 32272 and p202["price"] == 958180 and p202["maturity"] == "1405/09/23"
    assert p202["ytm_pct"] == 42.53 and p202["simple_yield_pct"] == 36.21 and p202["last_trade"] == "1405/08/09"

    near = (
        await client.call_tool(
            "ifb_yields",
            {"kind": "treasury", "matures_before": "1405-09-30", "sort_by": "maturity", "descending": False},
        )
    ).structured_content["papers"]
    assert near and all(p["maturity"] <= "1405/09/30" for p in near)
    assert [p["maturity"] for p in near] == sorted(p["maturity"] for p in near)

    matured = (
        await client.call_tool("ifb_yields", {"kind": "treasury", "include_matured": True, "limit": 100})
    ).structured_content
    assert any(p.get("matured") for p in matured["papers"]) and matured["matching"] > r["matching"]


async def test_yields_bonds_skip_never_traded(client, site):
    site["/YTM.aspx"] = fixture("ytm.html")
    r = (
        await client.call_tool("ifb_yields", {"kind": "bonds", "sort_by": "maturity", "descending": False})
    ).structured_content
    assert r["papers"] and all(p["kind"] == "bonds" and p["ytm_pct"] is not None for p in r["papers"])
    assert not any(p["symbol"].startswith("آرگون07") for p in r["papers"])  # no YTM in the table
    only = (await client.call_tool("ifb_yields", {"kind": "bonds", "symbol_contains": "کارون"})).structured_content
    assert [p["symbol"] for p in only["papers"]] == ["کارون073"]


async def test_instrument_maps_fields_and_pages_coupons(client, site):
    def instrument(request: httpx.Request) -> httpx.Response:
        page = fixture("instrument_pb2.html") if request.method == "POST" else fixture("instrument.html")
        return httpx.Response(200, text=page)

    site["/InstrumentsMFI.aspx"] = instrument
    r = (await client.call_tool("ifb_instrument", {"instrument": "34291"})).structured_content
    assert r["symbol"] == "کارون073" and r["isin"] == "IRB3KA040751" and r["coupon_rate_pct"] == 23
    assert r["maturity"] == "1407/05/21" and r["guarantor"] == "بانک شهر" and r["term_months"] == 48
    sched = r["coupon_schedule"]
    assert sched[0] == {"date": "1403/08/21", "amount": 57814.21} and sched[-1] == {
        "date": "1407/05/21",
        "amount": 1000000,
    }
    assert len(sched) == 17 and sched == sorted(sched, key=lambda c: (c["date"], c["amount"]))
    assert r["next_coupon"]["date"] >= to_jalali(dt.date.today())
    post = next(c for c in site.calls if c.method == "POST")
    form = site.form(post)
    assert (
        form["__EVENTARGUMENT"] == "Page$2"
        and form["__EVENTTARGET"].endswith("grdPBs")
        and form["__VIEWSTATE"] == "VS1"
    )


async def test_instrument_unknown_id_is_an_error(client, site):
    site["/InstrumentsMFI.aspx"] = "<html><body><form></form></body></html>"
    r = await client.call_tool("ifb_instrument", {"instrument": "1"})
    assert r.is_error


async def test_symbol_must_name_one_instrument(client, site):
    site[ASMX + "GetAllSymbolsForSearch"] = fixture("search.json")
    r = await client.call_tool("ifb_quote", {"instrument": "اخزا301"})
    assert r.is_error and "ifb_search" in r.content[0].text


async def test_quote_drops_empty_order_levels(client, site):
    site[ASMX + "InstrumentData"] = fixture("quote.json")
    site[ASMX + "MFIInstrumentData"] = {"d": None}
    r = (await client.call_tool("ifb_quote", {"instrument": "37012"})).structured_content
    assert r["last_price"] == 746580 and r["closing_price"] == 747600 and r["previous_close"] == 731650
    assert r["closing_change_pct"] == 2.18 and r["ytm_last_pct"] == 41.07 and r["volume"] == 95323
    assert len(r["order_book"]) == 5 and r["order_book"][0]["bid_price"] == 746580
    assert "ask_ytm_pct" not in r["order_book"][0]


async def test_quote_adds_bond_yields_to_the_book(client, site):
    site[ASMX + "InstrumentData"] = fixture("quote_bond.json")
    site[ASMX + "MFIInstrumentData"] = fixture("quote_bond_mfi.json")
    r = (await client.call_tool("ifb_quote", {"instrument": "32272"})).structured_content
    top = r["order_book"][0]
    assert top["bid_price"] == 958500 and top["bid_ytm_pct"] == 42.14 and top["ask_ytm_pct"] == 37.95
    assert len(r["order_book"]) == 5 and "bid_ytm_pct" not in r["order_book"][4]  # the bond service has 3 levels


async def test_quote_unknown_id(client, site):
    site[ASMX + "InstrumentData"] = {"d": None}
    site[ASMX + "MFIInstrumentData"] = {"d": None}
    assert (await client.call_tool("ifb_quote", {"instrument": "999"})).is_error


async def test_history_joins_yield_by_day(client, site):
    site[ASMX + "getHistorical"] = fixture("history.json")
    site[ASMX + "getYTMHistory"] = fixture("ytm_history.json")
    r = (await client.call_tool("ifb_history", {"instrument": "37012", "days": 3})).structured_content
    h = r["history"]
    assert r["days"] == 3 and h[0]["date"] == "1405/08/06" and h[0]["close"] == 731650 and h[0]["volume"] == 48629
    assert h[0]["ytm_pct"] == 43.96 and h[0]["date"] > h[1]["date"] > h[2]["date"]


async def test_ipos_completed_with_filters(client, site):
    site["/datareporter/IPO.aspx"] = fixture("ipo.html")
    r = (await client.call_tool("ifb_ipos", {})).structured_content
    first = r["ipos"][0]
    assert first["symbol"].strip() == "سیمکات1" and first["id"] == 35864 and first["price_end"] == 45840
    assert first["value"] == 4_464_160_000_000 and first["start"] == "1405/07/29" and r["page"] == 1
    site.calls.clear()
    await client.call_tool("ifb_ipos", {"market": "second", "offering_type": "combined", "from_date": "1405/01/01"})
    form = site.form(next(c for c in site.calls if c.method == "POST"))
    assert (
        form["ctl00$ContentPlaceHolder1$Marketcombo"] == "14" and form["ctl00$ContentPlaceHolder1$ArzeTypeCombo"] == "4"
    )
    assert form["ctl00$ContentPlaceHolder1$FromDT"] == "1405/01/01" and "BtnSearch" in str(form)


async def test_ipos_pipeline(client, site):
    site["/datareporter/IPOStatus.aspx"] = fixture("ipostatus.html")
    r = (await client.call_tool("ifb_ipos", {"stage": "pipeline"})).structured_content
    assert r["introduced"][0]["symbol"] == "بیمیسکو" and r["introduced"][0]["valuer"] == "تامین سرمایه امید"
    assert r["offered"][0]["symbol"] == "سیمکات" and r["offered"][0]["offering_date"] == "1405/07/29"
    assert r["offered"][0]["announcement"].endswith("file=25819")


async def test_underwritings(client, site):
    site["/ThirdMarket/AllUnderWrited.aspx"] = fixture("underwritings.html")
    r = (await client.call_tool("ifb_underwritings", {})).structured_content
    assert r["underwritings"][0]["id"] == 40330 and r["underwritings"][0]["amount"] == 2_500_000_000_000
    assert r["underwritings"][0]["start"] == "1404/10/07"


async def test_announcements_search_and_paging(client, site):
    site["/anouncments.aspx"] = fixture("announcements.html")
    r = (await client.call_tool("ifb_announcements", {"query": "اخزا", "page": 2})).structured_content
    first = r["items"][0]
    assert first["symbol"] == "فزر1" and first["number"] == "05/الف/11853" and first["date"] == "1405/08/09"
    assert first["time"] == "18:11" and first["url"].endswith("GetAnnouncmentFile.aspx?file=25885")
    posts = [site.form(c) for c in site.calls if c.method == "POST"]
    assert posts[0]["ctl00$ContentPlaceHolder1$txtKey"] == "اخزا" and "ctl00$ContentPlaceHolder1$BtnSearch" in posts[0]
    assert posts[1]["__EVENTARGUMENT"] == "Page$2" and "ctl00$ContentPlaceHolder1$BtnSearch" not in posts[1]
    assert posts[1]["ctl00$ContentPlaceHolder1$txtKey"] == "اخزا"  # the filter survives the pager click


async def test_news_and_market_messages(client, site):
    site["/AllNews.aspx"] = fixture("news.html")
    site["/AllMarketMassages.aspx"] = fixture("messages.html")
    n = (await client.call_tool("ifb_announcements", {"source": "news"})).structured_content
    assert (
        n["items"][0]["id"] == 20152
        and n["items"][0]["time"] == "12:17"
        and not [c for c in site.calls if c.method == "POST"]
    )
    m = (await client.call_tool("ifb_announcements", {"source": "market_messages"})).structured_content
    assert (
        m["items"][0]["date"] == "1405/08/09"
        and m["items"][0]["time"] == "11:41:00"
        and "فرویح1" in m["items"][0]["title"]
    )


async def test_article_text(client, site):
    site["/News.aspx"] = fixture("article.html")
    r = (await client.call_tool("ifb_article", {"id": 20152})).structured_content
    assert r["published"] == "1405/08/09 12:17" and "ثبت بیش از" in r["text"] and "news-container" not in r["text"]
    assert site.calls[0].url.params["id"] == "20152"


async def test_market_summary(client, site):
    site["/SukukIndex.aspx"] = fixture("summary.html")
    site[ASMX + "getFaraBourseIndex"] = fixture("index.json")
    site[ASMX + "GetArchiveOfToday"] = fixture("archive.json")
    site[ASMX + "GetLastSukukIndexValue"] = fixture("sukuk_index.json")
    r = (await client.call_tool("ifb_market_summary", {"top": 2})).structured_content
    assert r["today"] == {
        "volume": 26658704069,
        "value": 491038756916076,
        "trades": 1216307,
        "market_cap": 70545265983960984,
    }
    assert r["index"]["value"] == 66127.6128 and r["index"]["change"] < 0
    assert r["bond_indices"]["sukuk"]["weighted"] == 1361.6158104432
    second = r["markets"]["بازار دوم"]
    assert second["symbols"] > 10 and len(second["gainers"]) <= 2
    assert all(
        m["symbol"] != "JUNK" for m in second["gainers"] + second["losers"]
    )  # -100% placeholder rows are skipped


async def test_yield_curve(client, site):
    site[ASMX + "GetGovermentNelsonIndex"] = fixture("nelson_gov.json")
    site[ASMX + "GetNonGovermentNelsonIndex"] = fixture("nelson_non.json")
    site[ASMX + "GetITBtNelsonIndex"] = fixture("nelson_tb.json")
    r = (await client.call_tool("ifb_yield_curve", {})).structured_content
    g = r["government"]
    assert g["days"][:3] == [30, 60, 90] and g["now"][0] == 43.07 and len(g["now"]) == len(g["days"])
    assert set(r) == {"government", "non_government", "treasury_bills"} and "month_ago" in g


async def test_sukuk_flows(client, site):
    site["/datareporter/DailySukukTrades.aspx"] = fixture("flows.html")
    a = (await client.call_tool("ifb_sukuk_flows", {})).structured_content
    d = a["days"][0]
    assert (
        d["date"] == "1405/08/06"
        and d["bought"]["central_bank"] == 30_418_840_000_000
        and d["sold"]["banks"] == 60_809_630_000_000
    )
    b = (await client.call_tool("ifb_sukuk_flows", {"by": "type"})).structured_content
    assert b["days"][0]["open_market_operations"] == 30_418_840_000_000


async def test_only_ifb_host_is_called(site):
    site["/YTM.aspx"] = fixture("ytm.html")
    await http.get_page("YTM.aspx")
    assert {c.url.host for c in site.calls} == {"www.ifb.ir"}


async def test_http_errors_are_actionable(site):
    site["/blocked.aspx"] = lambda r: httpx.Response(403, text="no")
    site["/slow.aspx"] = lambda r: httpx.Response(503, text="no")
    with pytest.raises(http.ApiError, match="IFB_MCP_PROXY"):
        await http.get_page("blocked.aspx")
    with pytest.raises(http.ApiError, match="server error"):
        await http.get_page("slow.aspx")
    site["/odd.aspx"] = "just text"
    with pytest.raises(http.ApiError, match="unexpected response"):
        await http.get_page("odd.aspx")


def _signalr(site):
    site["/signalr/negotiate"] = {"ConnectionToken": "tok", "ConnectionId": "cid"}
    site["/signalr/connect"] = fixture("signalr_connect.json")
    site["/signalr/start"] = {"Response": "started"}
    site["/signalr/send"] = {"I": "0"}
    site["/signalr/poll"] = fixture("signalr_poll.json")
    site["/signalr/abort"] = {}


async def test_market_watch_reads_the_hub_once_and_ranks(client, site):
    _signalr(site)
    r = (await client.call_tool("ifb_market_watch", {"board": "second", "sort_by": "change_pct"})).structured_content
    assert r["as_of"].startswith("یکشنبه") and r["matching"] >= 1 and r["breadth"]["up"] + r["breadth"]["down"] >= 1
    chg = [s["close_change_pct"] for s in r["symbols"]]
    assert chg == sorted(chg, reverse=True)
    assert all(isinstance(s["close"], int) for s in r["symbols"])  # 41550.0 becomes 41550
    send = next(c for c in site.calls if c.url.path == "/signalr/send")
    assert b"letsStart" in send.content and b"cid" in send.content
    assert site.calls[-1].url.path == "/signalr/abort"
    await client.call_tool("ifb_market_watch", {})
    assert sum(c.url.path == "/signalr/negotiate" for c in site.calls) == 1  # second call is cached


async def test_market_watch_filters(client, site):
    _signalr(site)
    halted = (await client.call_tool("ifb_market_watch", {"status": "halted", "traded_only": False})).structured_content
    assert halted["symbols"] and all(s["status"].startswith("ممنوع") for s in halted["symbols"])
    idle = (await client.call_tool("ifb_market_watch", {"board": "first", "traded_only": False})).structured_content
    assert any(s["trades"] == 0 for s in idle["symbols"])
    named = (await client.call_tool("ifb_market_watch", {"symbol_contains": "اخزا"})).structured_content
    assert named["symbols"] and all("اخزا" in s["symbol"] for s in named["symbols"])


async def test_market_watch_without_table_is_an_error(client, site):
    _signalr(site)
    site["/signalr/poll"] = {"C": "d-X,3", "M": []}
    res = await client.call_tool("ifb_market_watch", {})
    assert res.is_error and "did not push" in res.content[0].text


async def test_index_history(client, site):
    site[ASMX + "getIndexHistory"] = fixture("index_history.json")
    r = (await client.call_tool("ifb_index_history", {"days": 30, "step": "weekly"})).structured_content
    assert r["latest"]["value"] == 66127.6128 and r["latest"]["date"] == "1405/08/09"
    assert r["changes_pct"]["1d"] == -0.39 and r["high"]["value"] >= r["latest"]["value"] >= r["low"]["value"]
    assert len(r["points"]) == 6 and r["points"][0]["date"] == "1405/08/09"
    site[ASMX + "GetWeightedIndexHistory"] = fixture("sukuk_history.json")
    s = (await client.call_tool("ifb_index_history", {"index": "sukuk", "days": 3})).structured_content
    assert s["latest"]["value"] == 1361.6158104432 and len(s["points"]) == 3


async def test_corporate_actions(client, site):
    site["/Issuers.aspx"] = lambda r: httpx.Response(
        200,
        text=fixture(
            {
                "2": "capital_increases.html",
                "3": "moved_to_tehran.html",
                "5": "market_transfers.html",
                "6": "new_listings.html",
            }[r.url.params["id"]]
        ),
    )
    cap = (await client.call_tool("ifb_corporate_actions", {})).structured_content["items"]
    assert [c["symbol"] for c in cap] == ["اتکای", "ارفع", "آرمان"] and cap[0]["id"] == 21282
    assert (
        cap[0]["increase"] == 5_000_000 and cap[0]["from_retained_earnings"] == 5_000_000 and "from_cash" not in cap[0]
    )
    assert cap[1]["rights_window"] == ["05/06/19 05/07/17"] and cap[1]["from_cash"] == 10_000_000
    assert cap[2]["rights_window"] == ["05/04/07 05/05/05", "05/05/06 05/05/20"]
    mv = (await client.call_tool("ifb_corporate_actions", {"kind": "market_transfers"})).structured_content["items"]
    assert mv[0] == {
        "symbol": "اتکاسا",
        "from": "تابلو پایه زرد",
        "to": "بازار دوم",
        "date": "1404/11/25",
        "reason": "به دلیل پذیرش در بازار دوم فرابورس ایران",
    }
    nl = (await client.call_tool("ifb_corporate_actions", {"kind": "new_listings"})).structured_content["items"]
    assert nl[0]["symbol"] == "فاسمین" and nl[0]["first_trade"] == "1405/06/29" and nl[0]["capital"] == 24 * 10**12
    out = await client.call_tool("ifb_corporate_actions", {"kind": "moved_to_tehran_bourse", "symbol_contains": "شراز"})
    assert out.structured_content["items"][0]["last_market_value"] == 194_150_000_000_000


async def test_halted_and_issuer_status(client, site):
    site["/DataReporter/SuspendedSymbols.aspx"] = fixture("halted.html")
    h = (await client.call_tool("ifb_halted", {})).structured_content
    assert h["items"][0]["symbol"] == "فتوسا" and h["items"][0]["halted_on"] == "1405/08/10" and not h["has_more"]
    site["/IFBSymbolState.aspx"] = fixture("issuer_status.html")
    s = (await client.call_tool("ifb_halted", {"kind": "issuer_status"})).structured_content
    assert s["items"][0]["status"] == "معامله تحت احتیاط" and s["items"][1]["since"] == "1405/08/09"


async def test_instrument_live_adds_quote_and_share_extras(client, site):
    site["/InstrumentsMFI.aspx"] = fixture("share_page.html")
    site["/Instruments.aspx"] = fixture("share_page.html")
    site[ASMX + "InstrumentData"] = fixture("quote.json")
    site[ASMX + "MFIInstrumentData"] = {"d": None}
    r = (await client.call_tool("ifb_instrument", {"instrument": "40326", "live": True})).structured_content
    assert r["price_band"] == {"upper": 42357, "lower": 38323} and r["quote"]["id"] == 40326
    peers = r["industry_peers"]
    assert peers[0]["id"] == 22091 and peers[0]["change_pct"] == 1.46 and peers[2]["change_pct"] == -1.72
    plain = (await client.call_tool("ifb_instrument", {"instrument": "40326"})).structured_content
    assert "quote" not in plain and "industry_peers" not in plain


async def test_requests_match_the_site_frontend_no_referer(site):
    site["/p.aspx"] = "<html><form></form></html>"
    site["/HistoricalInstrumentData.asmx/X"] = {}
    _signalr(site)
    await http.get_page("p.aspx")
    await http.post_page("p.aspx", {"a": "b"})
    await http.call("X", {})
    await http.market_watch()
    get, post, asmx, neg = (c.headers for c in site.calls[:4])
    send = next(c.headers for c in site.calls if c.url.path == "/signalr/send")
    assert all("referer" not in c.headers for c in site.calls)  # the site sends Referrer-Policy: no-referrer
    assert get["sec-fetch-mode"] == "navigate" and "origin" not in get
    assert post["origin"] == "null" and post["sec-fetch-site"] == "same-origin"
    assert (
        asmx["priority"] == "u=1, i"
        and asmx["content-type"] == "application/json; charset=UTF-8"
        and asmx["x-requested-with"] == "XMLHttpRequest"
    )
    assert asmx["origin"] == "https://www.ifb.ir" and asmx["accept"] == "application/json, text/javascript, */*; q=0.01"
    assert neg["content-type"] == "application/json; charset=UTF-8" and "origin" not in neg
    assert send["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
