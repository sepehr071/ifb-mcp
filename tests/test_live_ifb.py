import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

TB = 37012  # اخزا301, a treasury bill that trades most days
BOND = 34291  # کارون073, a quarterly sukuk maturing 1407/05/21


async def test_live_search(client):
    r = (await client.call_tool("ifb_search", {"query": "اخزا301"})).structured_content
    assert any(x["id"] == TB for x in r["results"]), r


async def test_live_yields_treasury_and_bonds(client):
    t = (await client.call_tool("ifb_yields", {"kind": "treasury", "limit": 10})).structured_content
    assert t["matching"] > 10 and all(20 < p["ytm_pct"] < 80 and p["id"] and p["maturity"] for p in t["papers"]), t
    b = (
        await client.call_tool(
            "ifb_yields", {"kind": "bonds", "min_ytm": 30, "sort_by": "maturity", "descending": False, "limit": 10}
        )
    ).structured_content
    assert b["papers"] and all(p["ytm_pct"] >= 30 and p["kind"] == "bonds" for p in b["papers"]), b


async def test_live_instrument_coupons(client):
    r = (await client.call_tool("ifb_instrument", {"instrument": str(BOND)})).structured_content
    assert r["symbol"] == "کارون073" and r["coupon_rate_pct"] == 23 and r["maturity"] == "1407/05/21"
    assert len(r["coupon_schedule"]) == 17 and r["next_coupon"]["amount"] > 50000


async def test_live_instrument_by_symbol(client):
    r = (await client.call_tool("ifb_instrument", {"instrument": "24235", "coupons": False})).structured_content
    assert r["symbol"] == "فرابورس1" and r["isin"] == "IRO3FRBZ0001"
    # a bond stem names several board variants (کارون0731, 0732...): the tool asks for an id instead of guessing
    amb = await client.call_tool("ifb_quote", {"instrument": "کارون073"})
    assert amb.is_error and "ifb_search" in amb.content[0].text


async def test_live_quote_and_history(client):
    q = (await client.call_tool("ifb_quote", {"instrument": str(TB)})).structured_content
    assert q["last_price"] > 100000 and q["ytm_closing_pct"] and q["units_issued"] == 250_000_000
    h = (await client.call_tool("ifb_history", {"instrument": str(TB), "days": 10})).structured_content
    assert h["days"] == 10 and h["history"][0]["date"] > h["history"][-1]["date"] and h["history"][0]["ytm_pct"]


async def test_live_ipos(client):
    done = (await client.call_tool("ifb_ipos", {})).structured_content
    assert len(done["ipos"]) == 10 and done["ipos"][0]["start"] >= done["ipos"][1]["start"]
    flt = (await client.call_tool("ifb_ipos", {"market": "second", "offering_type": "opening"})).structured_content
    assert flt["ipos"] and all(x["type"] == "گشایش نماد" and x["market"] == "دوم" for x in flt["ipos"]), flt
    pipe = (await client.call_tool("ifb_ipos", {"stage": "pipeline"})).structured_content
    assert pipe["offered"] and pipe["offered"][0]["symbol"]


async def test_live_underwritings(client):
    r = (await client.call_tool("ifb_underwritings", {})).structured_content
    assert r["underwritings"] and r["underwritings"][0]["amount"] > 0


async def test_live_announcements(client):
    a = (await client.call_tool("ifb_announcements", {"query": "اخزا"})).structured_content
    assert a["items"] and all("اخزا" in i["title"] or "اخزا" in (i.get("symbol") or "") for i in a["items"][:5]), a
    n = (await client.call_tool("ifb_announcements", {"source": "news"})).structured_content
    art = (await client.call_tool("ifb_article", {"id": n["items"][0]["id"]})).structured_content
    assert len(art["text"]) > 200
    m = (
        await client.call_tool("ifb_announcements", {"source": "market_messages", "query": "بازگشایی"})
    ).structured_content
    assert m["items"] and all("بازگشایی" in i["title"] + i["text"] for i in m["items"])


async def test_live_market_summary_and_curves(client):
    s = (await client.call_tool("ifb_market_summary", {"top": 2})).structured_content
    assert s["index"]["value"] > 10000 and s["today"]["market_cap"] > 10**15 and s["markets"] and s["bond_indices"]
    c = (await client.call_tool("ifb_yield_curve", {})).structured_content
    assert c["government"]["days"][0] == 30 and len(c["treasury_bills"]["now"]) >= 10


async def test_live_sukuk_flows(client):
    r = (await client.call_tool("ifb_sukuk_flows", {})).structured_content
    assert len(r["days"]) == 10 and r["days"][0]["bought"]["banks"] >= 0
    page2 = (await client.call_tool("ifb_sukuk_flows", {"page": 2})).structured_content
    assert page2["days"][0]["date"] < r["days"][-1]["date"]


async def test_live_market_watch(client):
    r = (
        await client.call_tool("ifb_market_watch", {"board": "second", "sort_by": "value", "limit": 5})
    ).structured_content
    assert r["matching"] > 50 and r["as_of"] and len(r["symbols"]) == 5
    values = [s["value"] for s in r["symbols"]]
    assert values == sorted(values, reverse=True) and r["symbols"][0]["id"]


async def test_live_index_history(client):
    r = (await client.call_tool("ifb_index_history", {"days": 10})).structured_content
    assert r["latest"]["value"] > 10000 and len(r["points"]) == 10 and r["changes_pct"]["250d"] is not None
    s = (await client.call_tool("ifb_index_history", {"index": "sukuk", "days": 5})).structured_content
    assert 500 < s["latest"]["value"] < 5000


async def test_live_corporate_actions_and_halts(client):
    for kind in ("capital_increases", "market_transfers", "new_listings", "moved_to_tehran_bourse"):
        r = (await client.call_tool("ifb_corporate_actions", {"kind": kind})).structured_content
        assert r["items"] and r["items"][0]["symbol"], kind
    h = (await client.call_tool("ifb_halted", {})).structured_content
    assert h["items"] and h["items"][0]["halted_on"]
    s = (await client.call_tool("ifb_halted", {"kind": "issuer_status"})).structured_content
    assert s["items"] and s["items"][0]["status"]
