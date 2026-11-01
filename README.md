<!-- mcp-name: io.github.sepehr071/ifb-mcp -->

<div align="center">

# ifb-mcp: Iran Fara Bourse (فرابورس ایران) bonds, sukuk and IPO MCP server for AI agents

**ifb-mcp is an unofficial, read-only MCP server that gives AI agents the public data of Iran Fara Bourse (ifb.ir), Iran's over-the-counter exchange.**<br>
Its 16 tools rank bonds, sukuk and treasury bills (اخزا) by yield to maturity, explain a paper's coupons and issuer,<br>
quote prices and order books, list IPOs and new offerings, search exchange notices and news, and summarise the market.<br>
No API key or account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[![PyPI](https://img.shields.io/pypi/v/ifb-mcp?color=2563eb)](https://pypi.org/project/ifb-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/ifb-mcp)](https://pypi.org/project/ifb-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/ifb-mcp/blob/main/LICENSE)

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for ifb.ir: yield to maturity of every treasury bill, sukuk and bond, instrument details with coupon schedules, live quotes and order books, price and yield history, IPOs (finished and upcoming), underwritings, exchange announcements, news and market messages, market summary, the live market watch with top movers and most traded, index history, halted symbols, capital increases and board transfers, yield curves and who trades Islamic securities.
- **Install:** `uvx ifb-mcp` or `claude mcp add ifb -- uvx ifb-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `IFB_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only. It cannot trade, log in or change anything, so no confirmation is needed before calling any tool.
- **Tools:** 16, all prefixed `ifb_`. Typical sequences: `ifb_yields` -> `ifb_instrument` / `ifb_quote`; `ifb_search` -> `ifb_history`; `ifb_ipos(stage="pipeline")`; `ifb_announcements` -> `ifb_article`.
- **Use when:** the user asks which bond, sukuk or treasury bill yields most or matures when, what a paper pays and when, a Fara Bourse price or order book, what IPOs are coming or happened, why a symbol is halted, or how the market did.
- **Do not use when:** the question is about the Tehran Stock Exchange (tse.ir), cryptocurrencies or gold; or the user wants to place an order.
- **Output:** compact JSON. Prices in rial, yields in percent a year, dates Jalali (`1405/08/09`). Texts are the exchange's own (Persian). The site is reachable from Iranian networks only.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/ifb-mcp/blob/main/server.json).

## Why

On ifb.ir, "which treasury bill maturing before next spring pays the best yield?" means opening the yield page,
scrolling 1,000 rows and sorting by hand. With `ifb-mcp` an agent answers it in one call:

> **You:** Best yield among treasury bills maturing before 1405/12/01?
>
> **Agent:** *calls* `ifb_yields(kind="treasury", matures_before="1405/12/01", limit=3)`

<sub>Data is the exchange's, as shown on its website; yields are computed by Fara Bourse from the last trade. This is information, not investment advice.</sub>

## What it can do

- **Rank fixed income by yield**: treasury bills, sukuk, participation papers and other bonds with last price, maturity and YTM, filtered by yield, maturity and symbol
- **Explain a paper**: coupon rate, maturity, issuer, guarantor, underwriter, market maker, full coupon schedule and the next payment
- **Quote and chart**: live price, change, volume, order book with the yield of each bid and offer, and daily history with YTM
- **Follow offerings**: finished IPOs with price and volume (filter by market and pricing method), companies being introduced, underwritings
- **Read the exchange**: search official notices, news and market messages (halts, openings) by keyword and date, and read an article
- **Watch the live market**: every symbol's price, change and best bid/ask ranked by change or value, per board; halted symbols with reasons; index history since 2009; capital increases and board transfers
- **See the market**: index, turnover, bond indices, each board's value and top movers, the fitted yield curves and who buys and sells Islamic securities
- **Read-only by design**: no login, no orders

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account. ifb.ir answers Iranian
networks; from abroad set `IFB_MCP_PROXY` to an Iranian proxy.

```bash
claude mcp add ifb -- uvx ifb-mcp
```

Claude Desktop, Cursor and others:

```json
{
  "mcpServers": {
    "ifb": { "command": "uvx", "args": ["ifb-mcp"] }
  }
}
```

## Tools

| Tool | Use it to |
|---|---|
| `ifb_search` | turn a symbol or name into an instrument id |
| `ifb_yields` | rank bonds, sukuk and treasury bills by YTM with maturity and price filters |
| `ifb_instrument` | read coupon rate, maturity, issuer, guarantor and the coupon schedule |
| `ifb_quote` | get the live price, volume, order book and yields |
| `ifb_history` | get daily prices and YTM |
| `ifb_ipos` | list finished IPOs or those upcoming and under way |
| `ifb_underwritings` | list securities offered by underwriting |
| `ifb_announcements` | search notices, news and market messages |
| `ifb_article` | read one news article |
| `ifb_market_summary` | index, turnover, bond indices, boards and top movers |
| `ifb_yield_curve` | fitted yield curves for government, non-government and treasury bills |
| `ifb_sukuk_flows` | daily value of Islamic securities bought and sold by investor type |
| `ifb_market_watch` | live market watch: top gainers, losers and most traded per board, halted symbols |
| `ifb_index_history` | daily history of the total, equal-weight, price and sukuk indices with 1d/5d/21d/250d changes |
| `ifb_corporate_actions` | capital increases with rights windows, board transfers, new listings, moves to Tehran Stock Exchange |
| `ifb_halted` | symbols halted now with date and reason, and issuers flagged for weak disclosure |

## FAQ

**Is it official?** No. It reads the public pages and web service of ifb.ir and is not affiliated with Iran Fara Bourse.

**Why do symbols end in a digit?** The exchange lists each paper on several boards (`کارون0731`, `0732`). Take the `id` from `ifb_yields` or `ifb_search`.

**Is the market watch included?** Not the live ranking grid, which the site fills through a push connection. Quotes come from the site's JSON service.

## فارسی

<div dir="rtl">

**ifb-mcp** یک سرور MCP غیررسمی و فقط‌خواندنی برای فرابورس ایران (ifb.ir) است. با آن می‌توان اوراق بدهی، صکوک و اسناد خزانه (اخزا) را بر پایهٔ بازده تا سررسید (YTM) مرتب کرد، جزئیات و برنامهٔ پرداخت سود هر ورقه را دید، قیمت و صف خرید و فروش را گرفت، عرضه‌های اولیه و پذیره‌نویسی‌ها را دید، اطلاعیه‌ها و اخبار را جستجو کرد و خلاصهٔ بازار را خواند. کلید API یا حساب لازم نیست. سایت فقط از شبکه‌های ایران پاسخ می‌دهد.

نصب: `claude mcp add ifb -- uvx ifb-mcp`

</div>

## License

MIT
