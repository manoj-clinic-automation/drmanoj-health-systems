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
    # --- Phase C (v3.6.0): stock + vitals log ----------------------------
    if "function loadStock(" in pg.content():
        import sqlite3 as _sq
        pg.goto(B + "/"); pg.wait_for_load_state("networkidle")
        pg.click('#nav button[data-t="meds"]'); time.sleep(0.3)
        pg.click('.seg[data-seg="meds"] button[data-s="stock"]'); time.sleep(0.8)
        res(pg.locator("#stList .strow").count() >= 3 and not pg.locator("#saveBtn").is_visible(),
            "Stock lists medicines; Save button hidden")
        xr = pg.locator("#stList .strow", has_text=meds[2]["name"]).first
        xr.locator(".sb button", has_text="Set count").click(); xr.locator(".sf input").fill("2")
        xr.locator(".sf button").click(); time.sleep(0.7)
        xr = pg.locator("#stList .strow", has_text=meds[2]["name"]).first
        res("2 left" in xr.inner_text(), "Set count shows 2 left")
        pr = pg.locator("#stList .strow", has_text=meds[1]["name"]).first
        pr.locator(".sb button", has_text="Set count").click(); pr.locator(".sf input").fill("20")
        pr.locator(".sf button").click(); time.sleep(0.7)
        res(meds[1]["name"] in pg.locator("#stPill .st-prev").inner_text(), "pillbox preview lists the counted pillbox medicine")
        pg.click("#stFill"); time.sleep(0.8)
        pr = pg.locator("#stList .strow", has_text=meds[1]["name"]).first
        res("13 left" in pr.inner_text(), "Pillbox filled takes 7 -> 13 left")
        vr = pg.locator("#stList .strow", has_text=name).first
        res("strengths vary" in vr.inner_text(), "variant medicine shown as not tracked")
        for dd in range(14):
            day = (_dt.date.today() - _dt.timedelta(days=dd)).isoformat()
            _c = _sq.connect(os.environ["GUTLOG_DB"])
            _c.execute("INSERT INTO doses(day,dtime,medicine,med_id,status,created) VALUES(?,?,?,?,?,?)",
                       (day, "00:00", "x", meds[2]["id"], "EXTRA", "t")); _c.commit(); _c.close()
        pg.click('#nav button[data-t="now"]'); time.sleep(0.8)
        res(pg.locator("#nowStock .stockalert").count() == 1 and meds[2]["name"] in pg.locator("#nowStock").inner_text(),
            "Now tab shows the refill banner")
        pg.locator("#nowStock").screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner.png"))
        pg.click("#nowStock .stockalert"); time.sleep(0.8)
        res(pg.locator("#meds-stock").is_visible(), "tapping the banner opens Stock")
        pg.locator("#meds-stock").screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock.png"))
        for i, (d, t, s_, di, pu) in enumerate([(1, "07:10", 132, 84, 70), (1, "21:00", 124, 80, 66),
                                                (2, "07:05", 138, 88, 72), (3, "20:30", 121, 79, 64)]):
            day = (_dt.date.today() - _dt.timedelta(days=d)).isoformat()
            pg.request.post(B + "/api/vitals", data={"day": day, "vtime": t, "sys": s_, "dia": di, "pulse": pu})
        pg.click('#nav button[data-t="review"]'); time.sleep(1.0)
        res(pg.locator("#vtChart svg path").count() == 3 and pg.locator("#vtTable tr").count() == 5,
            "Vitals log: 3 lines charted, 4 readings listed")
        res("average 129/83" in pg.locator("#vtSum").inner_text(), "Vitals summary average 129/83")
        pg.locator("#vitalsLog").screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "vitals.png"))
    # --- v3.7.0: medicine status, salts, activity -------------------------
    if pg.locator("#nowAct").count():
        HERE = os.path.dirname(os.path.abspath(__file__))
        pg.click('#nav button[data-t="now"]'); time.sleep(0.9)
        res("need a salt" in pg.locator("#nowMedStatus").inner_text(), "Now banner: medicines need a salt")
        pg.locator("#nowMedStatus .mlink").first.click(); time.sleep(0.8)
        res(pg.locator("#meds-salts").is_visible() and pg.locator("#saltList .strow").count() >= 3,
            "banner opens Meds > Salts with one row per medicine")
        sr = pg.locator("#saltList .strow", has_text=meds[1]["name"]).first
        sr.locator(".sm").fill("fluconazole"); sr.locator(".st").fill("150 mg")
        sr.locator(".go").click(); time.sleep(0.7)
        sj = dict((x["id"], x) for x in pg.request.get(B + "/api/salts").json()["meds"])
        res(sj[meds[1]["id"]]["molecule"] == "fluconazole" and sj[meds[1]["id"]]["strength"] == "150 mg",
            "Salts: Save stores salt and strength")
        nr = pg.locator("#saltList .strow", has_text=meds[2]["name"]).first
        nr.locator(".ns").click(); time.sleep(0.7)
        nr = pg.locator("#saltList .strow", has_text=meds[2]["name"]).first
        res("not a single drug" in nr.inner_text(), "Not a single drug marks the row")
        pg.locator("#meds-salts").screenshot(path=os.path.join(HERE, "salts.png"))
        pg.click('#nav button[data-t="now"]'); time.sleep(0.8)
        pg.click("#nowAct .fold-h"); time.sleep(0.3)
        tiles = pg.locator("#actTiles .ptile")
        res(tiles.count() == 5 and pg.locator("#actTiles .pscore:visible").count() == 0,
            "Activity: five tiles, all closed")
        wt = pg.locator("#actTiles .ptile[data-k=walk]")
        wt.locator(".ph").click(); time.sleep(0.2)
        res(pg.locator("#actTiles .pscore:visible").count() == 1, "tap Walk opens only its row")
        wt.locator(".as").click(); time.sleep(0.4)
        res(pg.request.get(B + "/api/activity").json()["items"] == [], "Save without minutes logs nothing")
        wt.locator(".am .chip", has_text="30").click(); wt.locator(".ai .chip", has_text="Moderate").click()
        res("30 min" in wt.locator(".pv").inner_text(), "tile shows the chosen minutes")
        wt.locator(".as").click(); time.sleep(0.8)
        mt = pg.locator("#actTiles .ptile[data-k=meditation]")
        res(mt.locator(".ai").count() == 0, "Meditation has no intensity")
        mt.locator(".ph").click(); mt.locator(".am .chip", has_text="15").click(); mt.locator(".as").click(); time.sleep(0.8)
        res(pg.locator("#actList .exrow").count() == 2 and "45 min" in pg.locator("#actSum").inner_text(),
            "two entries listed, header 45 min")
        res("Walk 30 min" in pg.locator("#actList").inner_text() and "moderate" in pg.locator("#actList").inner_text(),
            "row reads Walk 30 min, moderate")
        pg.locator("#nowAct").screenshot(path=os.path.join(HERE, "activity.png"))
        pg.locator("#actList .exrow", has_text="Meditation").locator(".u").click(); time.sleep(0.8)
        res(pg.locator("#actList .exrow").count() == 1 and "30 min" in pg.locator("#actSum").inner_text(),
            "Undo removes the entry")
        pg.click('#nav button[data-t="review"]'); time.sleep(0.6)
    # --- v3.8.0: records --------------------------------------------------
    if pg.locator("#files-summary").count():
        HERE = os.path.dirname(os.path.abspath(__file__))
        import json as _js, importlib.util as _iu
        rw = os.path.join(work, "recup"); os.makedirs(os.path.join(rw, "f", "01"), exist_ok=True)
        open(os.path.join(rw, "f", "01", "a.pdf"), "wb").write(b"%PDF-1.4 a")
        open(os.path.join(rw, "f", "01", "b.pdf"), "wb").write(b"%PDF-1.4 b")
        D0 = (_dt.date.today() - _dt.timedelta(days=300)).isoformat(); D1 = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
        _js.dump({"docs": [{"path": "01/a.pdf", "day": D0, "kind": "Blood", "title": "Panel A", "source": "Lab X", "finding": "Finding A " * 12},
                           {"path": "01/b.pdf", "day": D1, "kind": "Imaging", "title": "Scan B", "source": "Centre Y", "finding": "Finding B"}],
                  "labs": [{"day": D0, "test": "Testarate", "section": "S", "value": "30 **", "unit": "u", "ref": "0-20", "lab": "X"},
                           {"day": D1, "test": "Testarate", "section": "S", "value": "12", "unit": "u", "ref": "0-20", "lab": "X"}],
                  "plan": [{"pos": 1, "test": "Test one", "why": "because", "timing": "now"}],
                  "profile": {"updated": D1, "key_tests": ["Testarate"], "problems": [{"name": "Synthetic problem", "since": "2020", "status": "active"}],
                              "precautions": [{"flag": "RED", "title": "Synthetic precaution", "text": "why"}], "missing": ["Report Q"]}},
                 open(os.path.join(rw, "f", "records_manifest.local.json"), "w"))
        _sp = _iu.spec_from_file_location("imprec", os.path.join(os.path.dirname(os.path.abspath(app_path)), "import_records.py"))
        _im = _iu.module_from_spec(_sp); _sp.loader.exec_module(_im)
        m.PROFILE_FILE = os.path.join(work, "records_profile.local.json")
        _st = _im.run(rw, db_path=os.environ["GUTLOG_DB"], uploads=os.environ["GUTLOG_UPLOADS"], profile_path=m.PROFILE_FILE)
        res(_st.get("docs_new") == 2, "records import for UI test")
        pg.click('#nav button[data-t="files"]'); time.sleep(0.9)
        res(pg.locator("#nav button[data-t=files]").inner_text().strip().endswith("Records"), "nav reads Records")
        res(pg.locator("#files-summary").is_visible() and "Synthetic problem" in pg.locator("#rsBody").inner_text(),
            "Records opens on the Summary with problems")
        res("RED" in pg.locator("#rsBody").inner_text() and "Testarate" in pg.locator("#rsBody").inner_text(),
            "Summary shows precautions and key results")
        res(pg.locator(".save").is_hidden(), "no Save button on Summary")
        pg.locator("#files-summary").screenshot(path=os.path.join(HERE, "rec_summary.png"))
        pg.locator('.seg[data-seg="files"] button[data-s="reports"]').click(); time.sleep(0.7)
        res(pg.locator("#rdList .rd-row").count() == 2 and "Scan B" in pg.locator("#rdList .rd-row").first.inner_text(),
            "Reports newest first")
        pg.locator("#rdKinds .chip", has_text="Blood").click(); time.sleep(0.6)
        res(pg.locator("#rdList .rd-row").count() == 1, "filter by kind")
        res(pg.locator("#rdList a.rs-link").first.get_attribute("href").startswith("/rec/doc/"), "report opens the original")
        pg.locator("#files-reports").screenshot(path=os.path.join(HERE, "rec_reports.png"))
        pg.locator('.seg[data-seg="files"] button[data-s="trends"]').click(); time.sleep(0.7)
        res(pg.locator("#rtList .rt-spark").count() == 1, "trend chart drawn")
        pg.locator("#rtList .rt-head").first.click(); time.sleep(0.6)
        res(pg.locator("#rtList .rt-det .rs-hi").count() == 1, "series table marks the flagged value")
        pg.locator("#files-trends").screenshot(path=os.path.join(HERE, "rec_trends.png"))
        pg.locator('.seg[data-seg="files"] button[data-s="plan"]').click(); time.sleep(0.6)
        pg.locator("#rpList button").first.click(); time.sleep(0.6)
        res("Done" in pg.locator("#rpList button").first.inner_text(), "plan item marked done")
        pg.locator('.seg[data-seg="files"] button[data-s="vault"]').click(); time.sleep(0.4)
        res(pg.locator("#files-vault").is_visible() and pg.locator("#files-summary").is_hidden(), "second segment row switches to Upload")
    # --- v3.9.0: scanner ----------------------------------------------------
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(app_path)), "scanner_widget.js")) and "def scan_page" in open(app_path).read():
        HERE = os.path.dirname(os.path.abspath(__file__))
        from PIL import Image as _Im, ImageDraw as _Dr
        _img = _Im.new("RGB", (900, 1200), (120, 120, 120)); _d = _Dr.Draw(_img)
        _d.rectangle([150, 200, 750, 1000], fill=(250, 250, 250))
        for yy in range(260, 960, 40): _d.line([200, yy, 700, yy], fill=(40, 40, 40), width=3)
        _png = os.path.join(work, "doc.png"); _img.save(_png)
        pg.goto(B + "/scan"); pg.wait_for_load_state("networkidle"); time.sleep(0.6)
        res(pg.locator("#scanroot #addwhole").count() == 1, "scan page mounts the clinic scanner")
        pg.select_option("#s_type", "Imaging report")
        pg.locator("#scanroot #cam").set_input_files(_png); time.sleep(1.5)
        pg.locator("#scanroot #addwhole").click(); time.sleep(0.8)
        pg.screenshot(path=os.path.join(HERE, "scan_page.png"), full_page=False)
        pg.locator("#scanroot #savebtn").click(); time.sleep(2.0)
        _files = pg.request.get(B + "/api/records/docs").json()["uploads"]
        res(any(f["source"] == "Imaging report" for f in _files), "scanned page uploaded as an Imaging report")
        _ib = os.path.join(os.environ["GUTLOG_UPLOADS"], "inbox")
        res(os.path.isdir(_ib) and any(n.endswith((".jpg", ".pdf")) for n in os.listdir(_ib)), "scan copied to the inbox")
        pg.goto(B + "/?open=records"); time.sleep(1.2)
        res(pg.locator("#files-reports").is_visible() and pg.locator("#rdList .rd-row.inbox").count() >= 1,
            "Done returns to Records, Reports with the scan waiting")
    # --- v3.10.0: machine-read reports ----------------------------------------
    if "def _spawn_reader" in open(app_path).read():
        import sqlite3 as _sq3
        _c = _sq3.connect(os.environ["GUTLOG_DB"])
        _c.execute("INSERT INTO rec_docs(day,kind,title,source,finding,stored,orig,sha,status,created,origin,checked) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (_dt.date.today().isoformat(), "Blood", "Auto CBC", "Lab Z",
                   "Flagged: Platelet Count 1.05", "", "", "shaauto1", "filed", "t", "auto", 0)); _c.commit(); _c.close()
        pg.goto(B + "/?open=records"); time.sleep(1.2)
        _row = pg.locator("#rdList .rd-row", has_text="Auto CBC").first
        res(_row.locator(".rd-auto").count() == 1 and _row.locator(".rd-ok").count() == 1, "machine-read badge and Looks right")
        pg.locator('.seg[data-seg="files"] button[data-s="summary"]').click(); time.sleep(0.9)
        res("read automatically" in pg.locator("#rsBody").inner_text(), "Summary says a machine-read report awaits a glance")
        pg.locator('.seg[data-seg="files"] button[data-s="reports"]').click(); time.sleep(0.8)
        pg.locator("#rdList .rd-row", has_text="Auto CBC").first.locator(".rd-ok").click(); time.sleep(0.8)
        res(pg.locator("#rdList .rd-row", has_text="Auto CBC").first.locator(".rd-auto").count() == 0, "Looks right clears the badge")
    pg.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_" + os.path.basename(app_path) + ".png"), full_page=True)
    res(not errs, "no JavaScript errors" + ("" if not errs else ": " + " | ".join(errs)))
    br.close()
srv.shutdown()
print("RESULT: " + ("ALL PASS" if ok else "FAILURES"))
