"""test_ui_order.py -- OFFLINE ONLY (Playwright + Chromium; never on the server).
Usage: python3 test_ui_order.py path/to/app.py

Real-browser check of the v3.19.0 monthly order: the card lists what to
order, Send on WhatsApp opens wa.me with the order text AND saves the order,
Order received adds it to stock, the Pack form writes pack size, type and
keep-on-hand, the Now banner appears when the plan says the order is due and
opens Stock when tapped, nothing scrolls sideways at 360px, and the page
throws no JS error in light or dark. Synthetic medicines only.

service_workers="block" is load-bearing -- see test_ui_now.py.
"""
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)
import importlib.util, json, os, sqlite3, sys, tempfile, threading, time, datetime as dt
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

app_path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "app.py")
work = tempfile.mkdtemp()
SHOTS = os.environ.get("GUTLOG_UI_SHOTS") or os.path.join(tempfile.gettempdir(), "gutlog_ui_shots")
os.makedirs(SHOTS, exist_ok=True)
os.environ.update(GUTLOG_DB=os.path.join(work, "t.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                  GUTLOG_INSECURE="1", GUTLOG_SECRET="ui-test-not-real",
                  GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"),
                  GUTLOG_ICONS=os.path.dirname(app_path))
sys.path.insert(0, os.path.dirname(app_path))
spec = importlib.util.spec_from_file_location("g", app_path); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
srv = make_server("127.0.0.1", 8797, m.app); threading.Thread(target=srv.serve_forever, daemon=True).start()
B = "http://127.0.0.1:8797"
DB = os.environ["GUTLOG_DB"]
ok = True
def res(cond, msg):
    global ok; ok = ok and bool(cond); print(("[PASS] " if cond else "[FAIL] ") + msg)
def q(sql, a=()):
    c = sqlite3.connect(DB)
    try:
        r = c.execute(sql, a).fetchall(); c.commit(); return r
    finally:
        c.close()

with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 360, "height": 800}, service_workers="block")
    pg = ctx.new_page()
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/setup"); pg.fill("input[name=pw]", "testpassword1"); pg.fill("input[name=pw2]", "testpassword1")
    pg.locator("input[name=pw2]").press("Enter"); pg.wait_for_load_state("networkidle")
    meds = pg.request.get(B + "/api/prnmeds/full").json()
    A, X = meds[0], meds[1]
    Y3 = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    Y1 = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    pg.request.post(B + "/api/schedule", data={"med_id": A["id"], "slot": "MORNING", "dose_text": "1 tab", "valid_from": Y3})
    for mid, n in ((A["id"], 10), (X["id"], 3)):
        pg.request.post(B + "/api/stock/count", data={"med_id": mid, "qty": n})
    q("UPDATE stock_events SET at=?", (Y1 + " 00:00",))
    pg.request.post(B + "/api/stock/pack", data={"med_id": A["id"], "pack_size": 10, "pack_type": "strip", "keep": 0})

    # --- the Now banner: stub the plan as due, see it render and route ---
    hits = []
    def due(route):
        hits.append(1)
        real = pg.request.get(B + "/api/order").json(); real["due"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(real))
    pg.route("**/api/order", due)
    pg.goto(B + "/"); pg.wait_for_load_state("networkidle"); time.sleep(0.6)
    ban = pg.locator("#nowOrder .stockalert")
    res(hits and ban.count() == 1 and "order is due" in ban.inner_text(), "due order shows the Order banner on Now")
    if ban.count():
        pg.screenshot(path=os.path.join(SHOTS, "order_banner.png"))
        ban.click(); time.sleep(0.8)
    res(pg.locator("#meds-stock").is_visible(), "tapping the banner opens Stock")
    time.sleep(0.8); pg.unroute_all(behavior="ignoreErrors")

    # --- the card ---
    pg.goto(B + "/"); pg.wait_for_load_state("networkidle")
    pg.click('#nav button[data-t="meds"]'); time.sleep(0.3)
    pg.click('.seg[data-seg="meds"] button[data-s="stock"]'); time.sleep(1.0)
    lines = pg.locator("#ordLines .ordl")
    res(lines.count() == 1 and A["name"] in lines.first.inner_text() and "strips of 10" in lines.first.inner_text(),
        "card lists the scheduled medicine in whole strips")
    res(pg.locator("#ordSend").is_visible() and not pg.locator("#ordRecv").is_visible(),
        "before sending: Send shown, Order received hidden")
    pg.screenshot(path=os.path.join(SHOTS, "order_card.png"), full_page=True)

    ctx.route("https://wa.me/**", lambda r: r.fulfill(status=200, body="ok"))
    url = ""
    try:
        with ctx.expect_page(timeout=5000) as pi:
            pg.click("#ordSend")
        np_ = pi.value
        np_.wait_for_url("https://wa.me/**", timeout=5000)
        url = np_.url
        np_.close()
    except Exception as e:
        print("   popup: " + str(e)[:120])
    time.sleep(0.8)
    res(url.startswith("https://wa.me/?text=") and "Medicines%20order" in url, "Send opens WhatsApp with the order text")
    res(q("SELECT COUNT(*) FROM stock_orders WHERE status='OPEN'")[0][0] == 1, "Send saved the order")
    res(pg.locator("#ordRecv").is_visible(), "after sending: Order received shown")

    before = pg.request.get(B + "/api/stock").json()
    a0 = [r for r in before["rows"] if r["med_id"] == A["id"]][0]["current"]
    pg.once("dialog", lambda d: d.accept())
    pg.click("#ordRecv"); time.sleep(1.0)
    after = pg.request.get(B + "/api/stock").json()
    a1 = [r for r in after["rows"] if r["med_id"] == A["id"]][0]["current"]
    res(a1 > a0 and (a1 - a0) % 10 == 0, "Order received added whole strips to stock (%s -> %s)" % (a0, a1))
    res(pg.locator("#ordRecvUndo").is_visible() and not pg.locator("#ordSend").is_visible(),
        "after receipt: Undo shown, Send hidden")

    # --- the pack form ---
    row = pg.locator("#stList .strow", has_text=X["name"]).first
    row.locator(".sb .btn", has_text="Pack").click(); time.sleep(0.3)
    res(row.locator(".pk").count() == 1, "Pack opens the pack form in the row")
    row.locator(".pk-n").fill("15"); row.locator(".pk-t").select_option("strip"); row.locator(".pk-k").fill("15")
    row.locator(".pk .btn").click(); time.sleep(1.0)
    cfg = q("SELECT keep_units, pack_type FROM stock_order_cfg WHERE med_id=?", (X["id"],))
    ps = q("SELECT pack_size FROM prnmeds WHERE id=?", (X["id"],))[0][0]
    res(cfg and cfg[0][0] == 15 and cfg[0][1] == "strip" and ps == 15, "Pack form saved size, type and keep")
    row = pg.locator("#stList .strow", has_text=X["name"]).first
    res("strip of 15" in row.inner_text() and "keep 15" in row.inner_text(), "row shows its pack and keep")
    plan = pg.request.get(B + "/api/order").json()
    res(any(l["med_id"] == X["id"] and l["basis"] == "keep" for l in plan["lines"]),
        "the SOS medicine joins the plan once it has a keep figure")

    for theme in ("light", "dark"):
        pg.evaluate("t=>document.documentElement.setAttribute('data-theme',t)", theme); time.sleep(0.3)
        row = pg.locator("#stList .strow", has_text=A["name"]).first
        row.locator(".sb .btn", has_text="Pack").click(); time.sleep(0.3)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        res(sw <= 360, "%s: nothing scrolls sideways at 360px (scrollWidth %d)" % (theme, sw))
        pg.screenshot(path=os.path.join(SHOTS, "order_" + theme + ".png"), full_page=True)
        row.locator(".sb .btn", has_text="Pack").click(); time.sleep(0.2)
    res(not errs, "no page JS errors" + ("" if not errs else ": " + errs[0]))
    br.close()

srv.shutdown()
print("-" * 60)
print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
sys.exit(0 if ok else 1)
