"""Browser test for the dealer portal: checks the dashboard against the database and
drives the order flow (draft, confirm, short stock, dispatch, deliver, cancel).

Not part of `manage.py test`: it needs `pip install playwright` and a Chromium, a running
server on :8000 and a FRESHLY SEEDED database (it edits stock and creates orders).

    DATABASE_URL=postgres://... python tests_ui/e2e_portal.py [screenshot_dir]
"""
import asyncio, csv, math, os, re, statistics, subprocess, sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ["DATABASE_URL"]
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
DATA = ROOT / "data"
ML = ROOT / "ml" / "forecast.csv"
CHROMIUM = os.environ.get("CHROMIUM_PATH")  # default: Playwright's own browser
fails, passes = [], 0


def sql(q):
    return subprocess.run(["psql", DB, "-t", "-A", "-F", "|", "-c", q], capture_output=True, text=True, check=True).stdout.strip()


def check(name, actual, expected):
    global passes
    if str(actual) == str(expected):
        passes += 1
        print(f"  PASS  {name}: {actual}")
    else:
        fails.append(name)
        print(f"  FAIL  {name}: screen={actual!r} truth={expected!r}")


def money(d):  # formatted the way Intl en-IN INR shows it
    d = Decimal(d)
    whole, frac = f"{d:,.2f}".split(".")
    whole = whole.replace(",", "")
    out = whole[-3:]
    whole = whole[:-3]
    while whole:
        out = whole[-2:] + "," + out
        whole = whole[:-2]
    return f"₹{out}.{frac}"


def reorder_alerts():
    """Independent of the app: contract formula from the CSV files + live stock in the DB."""
    stock = {r.split("|")[0]: int(r.split("|")[1]) for r in sql("select sku,stock from core_product").splitlines()}
    lead = {r["sku"]: int(r["lead_time_weeks"]) for r in csv.DictReader(open(f"{DATA}/lead_times.csv"))}
    fc = {}
    for r in csv.DictReader(open(ML)):
        fc.setdefault(r["sku"], []).append((r["week_start"], float(r["forecast_units"])))
    sales = {}
    for r in csv.DictReader(open(f"{DATA}/sales_history.csv")):
        sales.setdefault(r["sku"], []).append((r["week_start"], int(r["units_sold"])))
    n = 0
    for sku, L in lead.items():
        demand = sum(u for _, u in sorted(fc[sku])[:L])
        vals = [u for _, u in sorted(sales[sku])[-12:]]
        sigma = statistics.stdev(vals) if len(vals) > 1 else 0
        rp = demand + math.ceil(1.65 * sigma * math.sqrt(L))
        if max(0, math.ceil(rp - stock[sku])) > 0:
            n += 1
    return n


def truth():
    t = {}
    t["products"] = int(sql("select count(*) from core_product"))
    t["dealers"] = int(sql("select count(*) from core_dealer"))
    t["drafts"] = int(sql("select count(*) from core_order where status='DRAFT'"))
    t["out"] = int(sql("select count(*) from core_product where stock=0"))
    t["low"] = int(sql("select count(*) from core_product where stock>0 and stock<10"))
    t["healthy"] = t["products"] - t["out"] - t["low"]
    t["reorders"] = reorder_alerts()
    rows = sql("select order_number,status,grand_total from core_order order by created_at desc, id desc limit 5").splitlines()
    t["recent"] = [(r.split("|")[0], r.split("|")[1], money(r.split("|")[2])) for r in rows if r]
    return t


async def read_dashboard(pg):
    await pg.click('.nav-link[data-view="orders"]')
    await pg.wait_for_timeout(300)
    await pg.click('.nav-link[data-view="dashboard"]')
    await pg.wait_for_timeout(2500)
    g = lambda i: pg.inner_text(f"#{i}")
    d = {"products": (await g("metric-products")).replace(",", ""), "dealers": await g("metric-dealers"),
         "drafts": await g("metric-drafts"), "reorders": await g("metric-reorders")}
    health = await pg.eval_on_selector_all("#inventory-health .health-row strong", "e=>e.map(x=>x.firstChild.textContent.trim())")
    d["healthy"], d["low"], d["out"] = health
    rows = await pg.eval_on_selector_all("#dashboard-orders tbody tr", "e=>e.map(r=>[...r.querySelectorAll('td')].map(c=>c.innerText.split('\\n')[0].trim()))")
    d["recent"] = [(r[0], r[1], r[2]) for r in rows if len(r) >= 3]  # empty state is a single cell
    return d


def compare(label, d, t):
    print(f"[{label}]")
    for k in ("products", "dealers", "drafts", "reorders", "healthy", "low", "out"):
        check(f"dashboard {k}", d[k], t[k])
    check("dashboard recent orders", d["recent"], t["recent"])


async def actions(pg):
    return await pg.eval_on_selector_all("#drawer-body [data-action]", "e=>e.map(x=>x.dataset.action)")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "404" not in m.text and "409" not in m.text else None)
        await pg.goto("http://localhost:8000/")
        await pg.wait_for_timeout(3000)

        # 1. fresh dashboard vs database
        d = await read_dashboard(pg)
        compare("fresh database", d, truth())
        await pg.screenshot(path=f"{OUT}/a_dashboard_fresh.png", full_page=True)

        sku, price = sql("select sku,price from core_product where stock between 20 and 40 order by sku limit 1").split("|")
        sql(f"update core_product set stock=12 where sku='{sku}'")
        price = Decimal(price)
        await pg.reload()
        await pg.wait_for_timeout(2500)

        # 2. create a draft through the form; the drawer opens
        await pg.click('.nav-link[data-view="orders"]')
        await pg.wait_for_timeout(800)
        await pg.select_option("#order-dealer", "3")
        await pg.select_option('#order-lines [name="sku"]', sku)
        await pg.fill('#order-lines [name="quantity"]', "5")
        await pg.click('#order-form button[type="submit"]')
        await pg.wait_for_timeout(1500)
        number = (await pg.inner_text("#drawer-title")).strip()
        check("drawer opened with an order number", bool(re.fullmatch(r"ORD-\d{8}-\d{4}", number)), True)
        sub = (price * 5).quantize(Decimal("0.01"))
        tax = (sub * Decimal("0.18")).quantize(Decimal("0.01"), ROUND_HALF_UP)
        check("detail subtotal", await pg.inner_text("#detail-subtotal"), money(sub))
        check("detail GST", await pg.inner_text("#detail-tax"), money(tax))
        check("detail grand total", await pg.inner_text("#detail-grand"), money(sub + tax))
        check("draft offers confirm + cancel", await actions(pg), ["confirm", "cancel"])
        check("draft leaves stock untouched", sql(f"select stock from core_product where sku='{sku}'"), "12")
        await pg.screenshot(path=f"{OUT}/b_drawer_draft.png")

        # 3. confirm: stock 12 -> 7, which moves the product into the LOW bucket
        await pg.click('#drawer-body [data-action="confirm"]')
        await pg.wait_for_timeout(1500)
        check("status after confirm (db)", sql(f"select status from core_order where order_number='{number}'"), "CONFIRMED")
        check("stock after confirm (db)", sql(f"select stock from core_product where sku='{sku}'"), "7")
        check("confirmed offers dispatch + cancel", await actions(pg), ["dispatch", "cancel"])
        await pg.screenshot(path=f"{OUT}/c_drawer_confirmed.png")
        await pg.keyboard.press("Escape")
        d = await read_dashboard(pg)
        compare("after confirm (stock buckets must have moved)", d, truth())
        await pg.screenshot(path=f"{OUT}/d_dashboard_after_confirm.png", full_page=True)

        # 4. not enough stock: nothing changes, shortage is listed
        await pg.click('.nav-link[data-view="orders"]')
        await pg.wait_for_timeout(500)
        await pg.select_option("#order-dealer", "4")
        await pg.select_option('#order-lines [name="sku"]', sku)
        await pg.fill('#order-lines [name="quantity"]', "9")
        await pg.click('#order-form button[type="submit"]')
        await pg.wait_for_timeout(1500)
        short_no = (await pg.inner_text("#drawer-title")).strip()
        await pg.click('#drawer-body [data-action="confirm"]')
        await pg.wait_for_timeout(1200)
        err = await pg.inner_text("#drawer-error")
        check("shortage lists sku, requested and available", all(s in err for s in (sku, "requested 9", "only 7 available")), True)
        check("shortage: order still DRAFT", sql(f"select status from core_order where order_number='{short_no}'"), "DRAFT")
        check("shortage: stock unchanged", sql(f"select stock from core_product where sku='{sku}'"), "7")
        check("shortage: buttons usable again", await pg.eval_on_selector_all("#drawer-body [data-action]", "e=>e.every(x=>!x.disabled)"), True)
        await pg.screenshot(path=f"{OUT}/e_drawer_short_stock.png")
        await pg.click('#drawer-body [data-action="cancel"]')
        await pg.click('#drawer-body [data-action="cancel"]')
        await pg.wait_for_timeout(1200)
        check("cancel draft (needs two clicks)", sql(f"select status from core_order where order_number='{short_no}'"), "CANCELLED")
        check("cancel draft leaves stock", sql(f"select stock from core_product where sku='{sku}'"), "7")
        await pg.keyboard.press("Escape")

        # 5. dispatch -> deliver, opening the order from the list
        await pg.click(f'#orders-table tr[data-order="{number}"]')
        await pg.wait_for_timeout(1000)
        await pg.click('#drawer-body [data-action="dispatch"]')
        await pg.wait_for_timeout(1200)
        check("status after dispatch", sql(f"select status from core_order where order_number='{number}'"), "DISPATCHED")
        check("dispatched offers only deliver", await actions(pg), ["deliver"])
        await pg.click('#drawer-body [data-action="deliver"]')
        await pg.wait_for_timeout(1200)
        check("status after deliver", sql(f"select status from core_order where order_number='{number}'"), "DELIVERED")
        check("delivered offers no actions", len(await actions(pg)), 0)
        await pg.screenshot(path=f"{OUT}/f_drawer_delivered.png")
        await pg.keyboard.press("Escape")

        # 6. cancel a CONFIRMED order: stock comes back exactly once
        await pg.select_option("#order-dealer", "5")
        await pg.select_option('#order-lines [name="sku"]', sku)
        await pg.fill('#order-lines [name="quantity"]', "2")
        await pg.click('#order-form button[type="submit"]')
        await pg.wait_for_timeout(1500)
        c_no = (await pg.inner_text("#drawer-title")).strip()
        await pg.click('#drawer-body [data-action="confirm"]')
        await pg.wait_for_timeout(1200)
        check("stock after second confirm", sql(f"select stock from core_product where sku='{sku}'"), "5")
        await pg.click('#drawer-body [data-action="cancel"]')
        check("first cancel click does nothing yet", sql(f"select status from core_order where order_number='{c_no}'"), "CONFIRMED")
        await pg.click('#drawer-body [data-action="cancel"]')
        await pg.wait_for_timeout(1200)
        check("status after cancel", sql(f"select status from core_order where order_number='{c_no}'"), "CANCELLED")
        check("stock restored exactly once", sql(f"select stock from core_product where sku='{sku}'"), "7")
        await pg.screenshot(path=f"{OUT}/g_drawer_cancelled.png")
        await pg.keyboard.press("Escape")

        # 7. dashboard after the whole flow
        d = await read_dashboard(pg)
        compare("after the full flow", d, truth())
        await pg.screenshot(path=f"{OUT}/h_dashboard_final.png", full_page=True)
        await pg.click('.nav-link[data-view="orders"]')
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=f"{OUT}/i_orders_list.png", full_page=True)

        # 8. a change made outside the UI shows up after a refresh
        sql("update core_product set stock=0 where sku in (select sku from core_product order by sku limit 3)")
        await pg.reload()
        await pg.wait_for_timeout(2500)
        d = await read_dashboard(pg)
        compare("stock changed outside the UI", d, truth())

        check("browser console / page errors", errs, [])
        await b.close()
    print(f"\n{passes} checks passed, {len(fails)} failed" + (": " + ", ".join(fails) if fails else ""))
    sys.exit(1 if fails else 0)


asyncio.run(main())
