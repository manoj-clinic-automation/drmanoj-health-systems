"""test_ui_now.py -- OFFLINE ONLY (needs Playwright + Chromium; never run on the server).
Usage: python3 test_ui_now.py path/to/app.py
Real-browser test of the Now tab: tap the variant row, pick a dose, log it,
then tap the logged row and check the action strip. Runs the page's own JS."""
import importlib.util, os, sys, tempfile, threading, time
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

app_path = sys.argv[1]
work = tempfile.mkdtemp()
os.environ.update(GUTLOG_DB=os.path.join(work, "t.db"), GUTLOG_UPLOADS=os.path.join(work, "up"),
                  GUTLOG_INSECURE="1", GUTLOG_SECRET="ui-test-not-real",
                  GUTLOG_ICONS=os.path.dirname(os.path.abspath(app_path)))
sys.path.insert(0, os.path.dirname(os.path.abspath(app_path)))
spec = importlib.util.spec_from_file_location("g", app_path); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
srv = make_server("127.0.0.1", 8799, m.app); threading.Thread(target=srv.serve_forever, daemon=True).start()
B = "http://127.0.0.1:8799"
ok = True
def res(cond, msg):
    global ok; ok = ok and cond; print(("[PASS] " if cond else "[FAIL] ") + msg)

with sync_playwright() as p:
    br = p.chromium.launch(); pg = br.new_page(viewport={"width": 390, "height": 844})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/setup"); pg.fill("input[name=pw]", "testpassword1"); pg.fill("input[name=pw2]", "testpassword1")
    pg.locator("input[name=pw2]").press("Enter"); pg.wait_for_load_state("networkidle")
    meds = pg.request.get(B + "/api/prnmeds/full").json()
    import datetime as _dt
    Y1 = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    pg.request.post(B + "/api/schedule", data={"med_id": meds[0]["id"], "slot": "MORNING", "valid_from": Y1,
                    "dose_text": "", "with_food": "ANY", "variants": "72|145|290"})
    pg.request.post(B + "/api/schedule", data={"med_id": meds[1]["id"], "slot": "MORNING", "dose_text": "1 tab",
                    "valid_from": Y1})
    name = meds[0]["name"]
    pg.goto(B + "/"); pg.wait_for_load_state("networkidle")
    pg.click("#nowDoses .fold-h"); time.sleep(0.3)
    row = pg.locator("#nowSched .doserow", has_text=name).first
    row.locator(".nm").click(); time.sleep(0.4)
    res(pg.locator(".varpick").count() == 1, "tap variant row opens dose picker")
    if pg.locator(".varpick").count():
        pg.locator(".varpick .chip", has_text="145").click()
        pg.locator(".varpick .chip", has_text="72").click()
        pg.locator(".varpick .go").click(); time.sleep(0.6)
        j = pg.request.get(B + "/api/now").json()
        r = [x for s in j["slots"] for x in s["rows"] if x["med_id"] == meds[0]["id"]][0]
        res(r["status"] == "TAKEN" and r["logged_dose"] == "145 + 72", "logged TAKEN as '" + str(r["logged_dose"]) + "'")
        pg.locator("#nowSched .doserow", has_text=name).first.locator(".nm").click(); time.sleep(0.4)
        res(pg.locator(".varpick .ch").count() == 1 and pg.locator(".varpick .un").count() == 1,
            "tap logged row opens Undo / Change dose / Skip strip")
        pg.locator(".varpick .ch").click(); time.sleep(0.3)
        pg.locator(".varpick .chip", has_text="290").click(); pg.locator(".varpick .go").click(); time.sleep(0.6)
        j = pg.request.get(B + "/api/now").json()
        rows = [x for s in j["slots"] for x in s["rows"] if x["med_id"] == meds[0]["id"]]
        res(len(rows) == 1 and rows[0]["logged_dose"] == "290", "change dose corrects in place -> '" + str(rows[0]["logged_dose"]) + "'")
    pl = pg.locator("#nowSched .doserow", has_text=meds[1]["name"]).first
    pl.locator(".nm").click(); time.sleep(0.6)
    j = pg.request.get(B + "/api/now").json()
    r2 = [x for s in j["slots"] for x in s["rows"] if x["med_id"] == meds[1]["id"]][0]
    res(r2["status"] == "TAKEN", "plain row still one-tap TAKEN")
    # --- undo a wrong tick: small undo button, and the strip's Undo -------
    pl = pg.locator("#nowSched .doserow", has_text=meds[1]["name"]).first
    pl.locator(".sk").click(); time.sleep(0.6)
    j = pg.request.get(B + "/api/now").json()
    r2 = [x for s in j["slots"] for x in s["rows"] if x["med_id"] == meds[1]["id"]][0]
    res(r2["status"] is None, "small undo button clears a wrong tick")
    pg.locator("#nowSched .doserow", has_text=name).first.locator(".nm").click(); time.sleep(0.4)
    pg.locator(".varpick .un").click(); time.sleep(0.6)
    j = pg.request.get(B + "/api/now").json()
    r = [x for s in j["slots"] for x in s["rows"] if x["med_id"] == meds[0]["id"]][0]
    res(r["status"] is None, "strip Undo clears a logged dose")
    import sqlite3
    nd = lambda: sqlite3.connect(os.environ["GUTLOG_DB"]).execute("SELECT COUNT(*) FROM doses").fetchone()[0]
    res(nd() == 0, "no dose rows left behind after undo (" + str(nd()) + ")")
    # --- pain by site (v3.4.2) --------------------------------------------
    if pg.locator("#n_painSites .ptile").count():
        pg.click("#nowSym .fold-h"); time.sleep(0.3)
        tiles = pg.locator("#n_painSites .ptile")
        res(tiles.count() == 2 and pg.locator("#n_painSites .pscore:visible").count() == 0,
            "two pain tiles, score rows closed")
        li = tiles.filter(has_text="Left iliac pain"); hy = tiles.filter(has_text="Hypogastrium pain")
        li.locator(".ph").click(); time.sleep(0.2)
        res(li.locator(".pscore").is_visible(), "tapping Left iliac expands its score row")
        ep = lambda: sqlite3.connect(os.environ["GUTLOG_DB"]).execute(
            "SELECT etype,severity,bristol FROM episodes ORDER BY id").fetchall()
        pg.click("#n_symSave"); time.sleep(0.5)
        res(len(ep()) == 0, "unscored site refused, nothing saved")
        li.locator(".pscore .chip", has_text="6").click()
        hy.locator(".ph").click(); time.sleep(0.2); hy.locator(".pscore .chip").nth(2).click()
        pg.locator("#n_symType .chip", has_text="Bloating").click()
        pg.locator("#n_symSev .chip").nth(3).click()
        pg.locator("#n_symBristol .chip", has_text="4").click()
        res(li.locator(".pv").inner_text() == "6/10", "tile header shows its score")
        pg.click("#n_symSave"); time.sleep(0.8)
        rows = sorted(tuple(str(v) for v in r) for r in ep())
        want = sorted([("Bloating", "4", "4"), ("Left iliac pain", "6", "4"), ("Hypogastrium pain", "3", "4")])
        res(rows == want, "3 episodes, each with its own score: " + str(rows))
        res(pg.locator("#n_painSites .pscore:visible").count() == 0, "tiles reset after save")
        hy.locator(".ph").click(); time.sleep(0.2); hy.locator(".ph").click(); time.sleep(0.2)
        res(not hy.locator(".pscore").is_visible(), "tapping the name again clears the tile")
    # --- Phase B (v3.5.0) ------------------------------------------------
    if "loadDayView" in pg.content():
        def nrow(mid, day=None):
            j = pg.request.get(B + "/api/now" + ("?day=" + day if day else "")).json()
            return [x for s in j["slots"] for x in s["rows"] if x["med_id"] == mid][0]
        # retime from the Now strip, then change dose must keep that time
        pg.goto(B + "/"); pg.wait_for_load_state("networkidle"); pg.click("#nowDoses .fold-h"); time.sleep(0.3)
        pg.locator("#nowSched .doserow", has_text=name).first.locator(".nm").click(); time.sleep(0.3)
        pg.locator(".varpick .chip", has_text="145").click(); pg.locator(".varpick .go").click(); time.sleep(0.6)
        pg.locator("#nowSched .doserow", has_text=name).first.locator(".nm").click(); time.sleep(0.3)
        res(pg.locator(".varpick .tt").count() == 1, "Now strip shows the logged time")
        pg.locator(".varpick .tt").fill("00:00"); pg.locator(".varpick .tm").click(); time.sleep(0.6)
        res(nrow(meds[0]["id"])["dtime"] == "00:00", "Save time retimes the dose to 00:00")
        pg.locator("#nowSched .doserow", has_text=name).first.locator(".nm").click(); time.sleep(0.3)
        pg.locator(".varpick .ch").click(); time.sleep(0.2)
        pg.locator(".varpick .chip", has_text="72").click(); pg.locator(".varpick .go").click(); time.sleep(0.6)
        r0 = nrow(meds[0]["id"])
        res(r0["logged_dose"] == "72" and r0["dtime"] == "00:00", "change dose keeps the retimed time (" + str(r0["dtime"]) + ")")
        # day view
        pg.click('#nav button[data-t="review"]'); time.sleep(0.8)
        res(pg.locator("#dvList .dvrow").count() >= 1 and pg.locator("#dvNext").is_disabled(),
            "Day by day lists today; next-day arrow disabled")
        pg.locator("#dvList .dvrow", has_text=name).first.click(); time.sleep(0.3)
        pg.locator("#dayView .varpick .tt").fill("00:01"); pg.locator("#dayView .varpick .go").click(); time.sleep(0.7)
        res(nrow(meds[0]["id"])["dtime"] == "00:01", "day-view edit retimes")
        res("time edited" in pg.locator("#dvList .dvrow", has_text=name).first.inner_text(), "edited entry is marked")
        # backfill yesterday
        pg.click("#dvPrev"); time.sleep(0.8)
        res(pg.locator("#dvMiss .dvmiss").count() == 2, "yesterday shows 2 scheduled-not-logged")
        plain = pg.locator("#dvMiss .dvmiss", has_text=meds[1]["name"]).first
        plain.locator(".tt").fill("07:15"); plain.locator(".go").click(); time.sleep(0.7)
        ry = nrow(meds[1]["id"], Y1)
        res(ry["status"] == "TAKEN" and ry["dtime"] == "07:15", "backfilled yesterday at 07:15")
        var = pg.locator("#dvMiss .dvmiss", has_text=name).first
        var.locator(".go").click(); time.sleep(0.5)
        res(nrow(meds[0]["id"], Y1)["status"] is None, "variant backfill refused without a dose")
        var.locator(".chip", has_text="290").click(); var.locator(".go").click(); time.sleep(0.7)
        rv = nrow(meds[0]["id"], Y1)
        res(rv["status"] == "TAKEN" and rv["logged_dose"] == "290", "variant backfilled as 290")
        res(pg.locator("#dvMiss .dvmiss").count() == 0 and pg.locator("#dvList .dvrow").count() == 2,
            "yesterday now shows 2 logged, none missing")
        pg.locator("#dayView").screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "dayview.png"))
    pg.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_" + os.path.basename(app_path) + ".png"), full_page=True)
    res(not errs, "no JavaScript errors" + ("" if not errs else ": " + " | ".join(errs)))
    br.close()
srv.shutdown()
print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
