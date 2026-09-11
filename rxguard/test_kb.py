#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxGuard v1.2.0 -- sources review test (kb_sources + kb_sync + /kb pages).

A local fake server stands in for RxNorm, openFDA, the FDA CYP page,
DDInter and PvPI, using the real response SHAPES (field names, CSV header,
table layout). The label text and pairs below are SYNTHETIC -- written to
exercise the parser, not quoted from any real label. Scratch database,
scratch knowledge folder; nothing live is touched. Python 3.9.

  python3 test_kb.py        -> must print 29/29 passed
"""
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, fn):
    try:
        RESULTS.append((True, name, fn() or ""))
    except AssertionError as exc:
        RESULTS.append((False, name, str(exc)))
    except Exception as exc:
        import traceback
        RESULTS.append((False, name, type(exc).__name__ + ": " + str(exc) + " " +
                        traceback.format_exc().splitlines()[-3]))


# ------------------------------------------------------------------ fixtures
FDA_HTML = """<html><body><p>Content current as of: 05/29/2026</p><table>
<tr><th>Drug or Other Substance</th><th>CYP Strg INH</th><th>CYP Mod INH</th><th>CYP WK INH</th>
<th>CYP Strg IND</th><th>CYP Mod IND</th><th>CYP WK IND</th><th>CYP SENS SUB</th>
<th>CYP Mod SENS SUB</th><th>TRNSP INH</th><th>TRNSP SUB</th></tr>
<tr><td>diltiazem</td><td>&nbsp;</td><td>3A&nbsp; moderate&nbsp; inhibitor</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>P-gp&nbsp; inhibitor</td><td>&nbsp;</td></tr>
<tr><td>fluconazole</td><td>2C19&nbsp; strong&nbsp; inhibitor</td><td>3A; 2C9&nbsp; moderate&nbsp; inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>ciprofloxacin</td><td></td><td>1A220; 3A &nbsp;moderate inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>OAT1; OAT3&nbsp; substrate</td></tr>
<tr><td>ritonavir&nbsp;14,&nbsp;15, 16</td><td>3A&nbsp; strong&nbsp; inhibitor</td><td></td><td></td><td></td><td></td><td>2B6; 2C9; 2C19&nbsp; weak&nbsp; inducer</td><td></td><td></td><td></td><td></td></tr>
<tr><td>aprepitant</td><td></td><td></td><td></td><td></td><td></td><td>2C9&nbsp; weak&nbsp; inducer</td><td></td><td>3A&nbsp; moderate&nbsp; sensitive&nbsp; substrate</td><td></td><td></td></tr>
<tr><td>aprepitant</td><td></td><td>3A&nbsp; moderate&nbsp; inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>propranolol</td><td></td><td></td><td></td><td></td><td></td><td></td><td>2D6&nbsp; sensitive&nbsp; substrate</td><td></td><td></td><td></td></tr>
""" + "".join("<tr><td>filler%s</td><td>3A strong inhibitor</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>" % (chr(97 + i // 26) + chr(97 + i % 26)) for i in range(110)) + "</table></body></html>"

DDI_HEAD = "DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level\n"
DDI_ROWS = {
    "C": 'DDInter1,Diltiazem,DDInter2,Propranolol,Major\nDDInter1,Diltiazem,DDInter3,Alprazolam,Moderate\n'
         'DDInter4,Losartan,DDInter3,Alprazolam,Moderate\nDDInter1,Diltiazem,DDInter5,Ramelteon,Moderate\n'
         'DDInter6,Acetaminophen,DDInter1,Diltiazem,Unknown\n',
    "N": 'DDInter5,Ramelteon,DDInter3,Alprazolam,Moderate\nDDInter3,Alprazolam,DDInter7,Lubiprostone,Minor\n'
         '"DDInter8","Polyethylene glycol (3350, with electrolytes)",DDInter4,Losartan,Moderate\n',
}
DDI_ROWS["C"] += "".join("DDInter9%04d,Fill%04d,DDInter8%04d,Fillb%04d,Moderate\n" % (i, i, i, i) for i in range(1100))

LABELS = {
    "ramelteon": [{"set_id": "set-ram-old", "effective_time": "20200101", "openfda": {"generic_name": ["RAMELTEON TARTRATE"], "brand_name": ["OldZ"]},
                  "warnings": ["Old text."]},
                 {"set_id": "set-ram", "effective_time": "20250301", "openfda": {"generic_name": ["RAMELTEON TARTRATE"], "brand_name": ["SynthZ"]},
                  "boxed_warning": ["WARNING: COMPLEX SLEEP BEHAVIORS. Complex sleep behaviors may occur."],
                  "warnings_and_cautions": ["CNS Depressant Effects and Next-Day Impairment: This drug is a central nervous system (CNS) depressant and can impair daytime function. Hypotension has not been reported."],
                  "drug_interactions": ["Coadministration with diltiazem increased exposure in a study. Concomitant use with alprazolam is contraindicated in this synthetic example."],
                  "use_in_specific_populations": ["Hepatic Impairment: reduce the dose in patients with hepatic impairment."]},
                 {"set_id": "set-combo", "effective_time": "20260101", "openfda": {"generic_name": ["RAMELTEON AND SOMETHING"]}}],
    "lubiprostone": [{"set_id": "set-lin", "effective_time": "20240601", "openfda": {"generic_name": ["LUBIPROSTONE"], "brand_name": ["SynthL"]},
                     "boxed_warning": ["WARNING: RISK OF SERIOUS DEHYDRATION IN PEDIATRIC PATIENTS."],
                     "warnings_and_cautions": ["Diarrhea: diarrhea was the most common adverse reaction."],
                     "clinical_pharmacology": ["Lubiprostone is minimally absorbed and is not a substrate of CYP enzymes in a meaningful way."]}],
    "diltiazem": [{"set_id": "set-ver", "effective_time": "20240522", "openfda": {"generic_name": ["DILTIAZEM HYDROCHLORIDE"]},
                   "drug_interactions": ["Cytochrome inducers/inhibitors: diltiazem is metabolized by CYP3A4."]}],
    "propranolol": [{"set_id": "set-pro", "effective_time": "20230101", "openfda": {"generic_name": ["PROPRANOLOL"]},
                   "warnings": ["Bradycardia may occur. Abrupt cessation may exacerbate angina; avoid abrupt discontinuation."],
                   "clinical_pharmacology": ["Propranolol is metabolized primarily by CYP2D6."]}],
}
RX = {
    "ramelteon": ("39993", "ramelteon", "IN", [("N05CF", "Benzodiazepine related drugs")]),
    "lubiprostone": ("1307404", "lubiprostone", "IN", [("A06AX", "Other drugs for constipation")]),
    "propranolol": ("31555", "propranolol", "IN", [("C07AB", "Beta blocking agents, selective")]),
    "comboolol": ("99001", "comboolol", "IN", [("C09DX", "Angiotensin II receptor blockers (ARBs), other combinations"),
                                              ("C07AB", "Beta blocking agents, selective")]),
    "tylenolx": ("202433", "Tylenol", "BN", []),
}
STATE = {"down": False, "fda_calls": 0, "label_newer": False, "ddi_fail": set()}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if STATE["down"]:
            self.send(503, "")
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p == "/REST/approximateTerm.json":
            t = q.get("term", [""])[0].lower()
            if t in RX:
                self.send(200, json.dumps({"approximateGroup": {"candidate": [{"rxcui": RX[t][0], "rank": "1"}]}}))
            else:
                self.send(200, json.dumps({"approximateGroup": {"inputTerm": None}}))
        elif p.startswith("/REST/rxcui/") and p.endswith("/properties.json"):
            rid = p.split("/")[3]
            for k, v in RX.items():
                if v[0] == rid:
                    self.send(200, json.dumps({"properties": {"rxcui": rid, "name": v[1], "tty": v[2]}}))
                    return
            self.send(404, "")
        elif p.startswith("/REST/rxcui/") and p.endswith("/related.json"):
            self.send(200, json.dumps({"relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": [
                {"rxcui": "161", "name": "acetaminophen", "tty": "IN"}]}]}}))
        elif p == "/REST/rxclass/class/byRxcui.json":
            rid = q.get("rxcui", [""])[0]
            items = []
            for k, v in RX.items():
                if v[0] == rid:
                    items = [{"rxclassMinConceptItem": {"classId": c, "className": n, "classType": "ATC1-4"}}
                             for c, n in v[3]]
            self.send(200, json.dumps({"rxclassDrugInfoList": {"rxclassDrugInfo": items}} if items else {}))
        elif p == "/REST/spellingsuggestions.json":
            self.send(200, json.dumps({"suggestionGroup": {"suggestionList": {"suggestion": ["lubiprostone"]}}}))
        elif p == "/drug/label.json":
            s = unquote(q.get("search", [""])[0]).lower()
            name = s.split('"')[1] if '"' in s else ""
            res = [dict(r) for r in LABELS.get(name, [])]
            if STATE["label_newer"] and name == "ramelteon":
                for r in res:
                    if r["set_id"] == "set-ram":
                        r["effective_time"] = "20261001"
            if res:
                self.send(200, json.dumps({"results": res}))
            else:
                self.send(404, json.dumps({"error": {"code": "NOT_FOUND"}}))
        elif p == "/fda-cyp":
            STATE["fda_calls"] += 1
            self.send(200, FDA_HTML, "text/html")
        elif p == "/slow":
            import time as _t
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(15):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    _t.sleep(0.2)
            except OSError:
                pass
        elif p.startswith("/static/media/download/ddinter_downloads_code_"):
            c = p[-5]
            if c in STATE["ddi_fail"]:
                self.send(503, "")
                return
            self.send(200, DDI_HEAD + DDI_ROWS.get(c, ""), "text/csv")
        elif p == "/pvpi":
            self.send(200, '<a href="/x?id=1:drug-alerts-2026">Drug Alerts 2026</a> '
                           '<a href="/x?id=2:drug-alerts-2025"> Drug Alerts 2025</a> <a href="/home">Home</a>',
                      "text/html")
        else:
            self.send(404, "")


def main():
    work = tempfile.mkdtemp()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = HTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.update(RXGUARD_RXNAV=base, RXGUARD_OPENFDA=base, RXGUARD_FDA_CYP_URL=base + "/fda-cyp",
                      RXGUARD_DDINTER=base, RXGUARD_PVPI_URL=base + "/pvpi",
                      RXGUARD_KB_CACHE=os.path.join(work, "cache"), RXGUARD_KB_NOSPAWN="1",
                      RXGUARD_GUTLOG_FEED="0", GUTLOG_FEED_TOKEN_FILE=os.path.join(work, "feed.token"))
    open(os.environ["GUTLOG_FEED_TOKEN_FILE"], "w").write("t" * 64)
    # the app reads knowledge from its own folder: run a copy in the scratch dir
    appdir = os.path.join(work, "rx")
    os.makedirs(appdir)
    for f in ("app.py", "kb_sources.py", "kb_sync.py"):
        shutil.copy(os.path.join(HERE, f), appdir)
    shutil.copytree(os.path.join(HERE, "knowledge"), os.path.join(appdir, "knowledge"),
                    ignore=shutil.ignore_patterns("*.local.json", "cache"))
    os.environ["RXGUARD_DB"] = os.path.join(work, "r.db")
    sys.path.insert(0, appdir)
    import app as rx
    import kb_sources as ks
    import kb_sync
    rapp = rx.create_app(db_path=os.environ["RXGUARD_DB"], secret="t")
    rapp.config["TESTING"] = True
    c = rapp.test_client()
    c.post("/login", data={"password": "testpassword1"})
    con = sqlite3.connect(os.environ["RXGUARD_DB"])
    con.executescript(rx.SCHEMA)
    feed = {"ok": True, "days": 30, "regimen": [
        {"name": "Test A", "molecule": "diltiazem", "strength": "120 mg"},
        {"name": "Test B", "molecule": "propranolol", "strength": "5 mg"},
        {"name": "Test C", "molecule": "losartan", "strength": "40 mg"}],
        "taken": [{"name": "Test D", "molecule": "ramelteon", "strength": "5 mg"},
                  {"name": "Test E", "molecule": "alprazolam", "strength": "0.5 mg"},
                  {"name": "Test F", "molecule": "lubiprostone", "strength": "145 mcg"},
                  {"name": "Mystery", "molecule": "unknownium", "strength": ""},
                  {"name": "TylX", "molecule": "tylenolx", "strength": "500 mg"}]}
    ctx = {}

    def drafts():
        return dict((r[0], (r[1], json.loads(r[2]))) for r in
                    con.execute("SELECT key, status, draft_json FROM kb_drafts").fetchall())

    def t00_fda_parse():
        t = ks.parse_fda_cyp(FDA_HTML)
        v, f, cp, rt, ap = t["diltiazem"], t["fluconazole"], t["ciprofloxacin"], t["ritonavir"], t["aprepitant"]
        assert v["inhibitor"] == {"CYP3A4": "moderate", "P-gp": "moderate"}, str(v)
        assert f["inhibitor"] == {"CYP2C19": "strong", "CYP3A4": "moderate", "CYP2C9": "moderate"}, str(f)
        assert cp["inhibitor"] == {"CYP1A2": "moderate", "CYP3A4": "moderate"}, "footnote glued to 1A2: " + str(cp)
        assert cp["substrate"] == {"OAT1": "moderate", "OAT3": "moderate"}, str(cp)
        assert rt["inducer"] == {"CYP2B6": "weak", "CYP2C9": "weak", "CYP2C19": "weak"}, str(rt)
        assert ap["inhibitor"] == {"CYP3A4": "moderate"} and ap["substrate"] == {"CYP3A4": "moderate"}, str(ap)
        assert t["propranolol"]["substrate"] == {"CYP2D6": "major"}, str(t["propranolol"])
        return "real FDA layout: footnotes, merged rows, transporters, name cleanup"

    def t01_fda_cache():
        table, meta = ks.fda_cyp_table()
        assert meta["ok"] and meta["rows"] >= 100, str(meta)
        n = STATE["fda_calls"]
        ks.fda_cyp_table()
        assert STATE["fda_calls"] == n, "cache not used"
        STATE["down"] = True
        os.utime(os.path.join(os.environ["RXGUARD_KB_CACHE"], "fda_cyp.json"), (1, 1))
        table2, meta2 = ks.fda_cyp_table()
        STATE["down"] = False
        assert table2 and "using copy" in meta2.get("err", ""), "outage lost the table: " + str(meta2)
        return "cached monthly; an outage keeps using the last good copy"

    def t02_ddinter():
        path, meta = ks.ddinter_db()
        assert meta["ok"] and meta["pairs"] > 1000, str(meta)
        assert ks.ddinter_level(path, "propranolol", "diltiazem") == "Major", "pair order"
        assert ks.ddinter_level(path, "alprazolam", "lubiprostone") == "Minor"
        assert ks.ddinter_level(path, "paracetamol", "diltiazem") == "", "Unknown level must be dropped"
        assert ks.ddinter_level(path, "polyethylene_glycol_(3350,_with_electrolytes)", "losartan") == "Moderate", \
            "quoted CSV field"
        ctx["ddi"] = path
        return "14 files indexed; symmetric; Unknown dropped; quoted names parsed"

    def t03_rxnorm():
        z = ks.rxnorm_identify("ramelteon")
        assert z["ok"] and z["rxcui"] == "39993" and z["atc"] == [("N05CF", "Benzodiazepine related drugs")], str(z)
        b = ks.rxnorm_identify("tylenolx")
        assert b["name"] == "acetaminophen" and b["tty"] == "IN", "brand not resolved to ingredient: " + str(b)
        u = ks.rxnorm_identify("unknownium")
        assert not u["ok"] and u["err"] == "no RxNorm match", str(u)
        return "ingredient, brand -> ingredient, no-match"

    def t04_openfda():
        lab = ks.openfda_label("ramelteon")
        assert lab["ok"] and lab["set_id"] == "set-ram", "newest single-ingredient label not chosen: " + lab["set_id"]
        assert "alprazolam" in lab["sections"]["drug_interactions"]
        miss = ks.openfda_label("unknownium")
        assert not miss["ok"] and "no US label" in miss["err"], str(miss)
        return "newest single-ingredient label; combination and older labels skipped"

    def t05_draft_props():
        table, _ = ks.fda_cyp_table()
        d = ks.build_draft("ramelteon", "5 mg", [("diltiazem", "diltiazem"), ("alprazolam", "alprazolam")],
                           table, ctx["ddi"])
        f = dict((p["field"], p) for p in d["props"])
        assert f["class"]["value"] == "benzodiazepine related drugs", str(f.get("class"))
        assert f["burden.sedation"]["value"] == 2 and "CNS" in f["burden.sedation"]["quote"], str(f.get("burden.sedation"))
        assert "hepatic impairment" in f["hepatic"]["value"], "hepatic sentence"
        assert "bp" in f, "hypotension wording"
        assert any(g.startswith("QT") for g in d["gaps"]), "QT gap not listed: " + str(d["gaps"])
        ctx["zdraft"] = d
        return "class, sedation 2 with quote, hepatic, gaps listed (QT silent)"

    def t06_draft_pairs():
        pr = [(p["b"], p["flag"], p["kind"]) for p in ctx["zdraft"]["pairs"]]
        assert ("alprazolam", "RED", "label") in pr, "contraindicated wording should be RED: " + str(pr)
        assert ("diltiazem", "AMBER", "label") in pr, str(pr)
        assert ("diltiazem", "AMBER", "ddinter") in pr and ("alprazolam", "AMBER", "ddinter") in pr, str(pr)
        return "label pairs (RED on 'contraindicated') and DDInter pairs, each cited"

    def t07_cyp_table_in_draft():
        table, _ = ks.fda_cyp_table()
        d = ks.build_draft("propranolol", "5 mg", [], table, ctx["ddi"])
        f = dict((p["field"], p) for p in d["props"])
        assert f["cyp.substrate.CYP2D6"]["value"] == "major" and f["cyp.substrate.CYP2D6"]["source"].startswith("FDA CYP"), \
            "FDA table should beat label wording: " + str(f.get("cyp.substrate.CYP2D6"))
        assert f["hr"]["value"] == "decrease" and "withdrawal_note" in f, str(list(f))
        return "FDA table wins over label wording for the same CYP field"

    def t08_sync_run():
        st = kb_sync.run(con, data=feed, now="2026-09-11 08:00")
        dr = drafts()
        assert set(dr) == {"propranolol", "ramelteon", "lubiprostone", "unknownium", "tylenolx"}, str(sorted(dr))
        assert dr["tylenolx"][1].get("alias_of") == "paracetamol", "brand alias: " + str(dr["tylenolx"][1])
        assert dr["unknownium"][1]["gaps"], "no-source molecule should list gaps"
        assert st["pvpi"] == "2 links", st["pvpi"]
        pairs = con.execute("SELECT a, b, flag FROM kb_pairs").fetchall()
        assert ("alprazolam", "losartan", "AMBER") in pairs and ("alprazolam", "diltiazem", "AMBER") in pairs, str(pairs)
        return "5 drafts (1 alias), known-known pairs, PvPI baseline"

    def t09_sync_idempotent():
        n1 = (con.execute("SELECT COUNT(*) FROM kb_drafts").fetchone()[0],
              con.execute("SELECT COUNT(*) FROM kb_pairs").fetchone()[0],
              con.execute("SELECT COUNT(*) FROM kb_alerts").fetchone()[0])
        st = kb_sync.run(con, data=feed, now="2026-09-11 08:30")
        n2 = (con.execute("SELECT COUNT(*) FROM kb_drafts").fetchone()[0],
              con.execute("SELECT COUNT(*) FROM kb_pairs").fetchone()[0],
              con.execute("SELECT COUNT(*) FROM kb_alerts").fetchone()[0])
        assert n1 == n2 and st["drafts_made"] == 0, "rerun duplicated: %s -> %s" % (n1, n2)
        assert con.execute("SELECT COUNT(*) FROM kb_alerts WHERE status='new'").fetchone()[0] == 0, \
            "first PvPI run should be baseline, not new"
        return "second run adds nothing; first PvPI run is baseline"

    def t10_page():
        h = c.get("/kb").get_data(as_text=True)
        for s in ("Sources review", "ramelteon", "Benzodiazepine related drugs", "contraindicated",
                  "DDInter 2.0 risk level: Moderate", "Approve ticked", "already covers"):
            assert s in h, "page missing: " + s
        assert "GREEN" not in h
        return "drafts, quotes, sources and the alias card render; no GREEN"

    def did(key):
        return con.execute("SELECT id FROM kb_drafts WHERE key=?", (key,)).fetchone()[0]

    def t11_approve_partial():
        d = drafts()["ramelteon"][1]
        keep = [str(i) for i, p in enumerate(d["props"]) if p["field"] != "bp"]
        pairs = [str(i) for i, p in enumerate(d["pairs"]) if p["kind"] == "label"]
        r = c.post("/kb/draft/%d" % did("ramelteon"), data={"action": "approve", "prop": keep, "pair": pairs})
        assert r.status_code == 302, r.status_code
        loc = json.load(open(os.path.join(appdir, "knowledge", "drugs.local.json")))
        z = loc["drugs"]["ramelteon"]
        assert z["burden"]["sedation"] == 2 and z["bp"] == "none", "unticked bp applied or sedation lost: " + str(z["bp"])
        assert z["evidence"] and z["reviewed"] and "approved by owner" in z["source"]
        return "only ticked properties written, with evidence and date"

    def t12_engine_sees_it():
        con2 = sqlite3.connect(os.environ["RXGUARD_DB"])
        con2.execute("INSERT INTO medications(drug_key, raw_name, status) VALUES('alprazolam','c','active')")
        con2.commit()
        con2.close()
        with rapp.test_request_context():
            rx.g.db_path = os.environ["RXGUARD_DB"]
            assert rx.norm_key("ramelteon") in rx.DRUGS, "approved molecule not loaded"
            res = rx.analyse("ramelteon")
            ids = [f.get("rule_id") for f in res["findings"]]
            titles = " | ".join(f["title"] for f in res["findings"])
        assert any(i and i.startswith("LB") for i in ids), "approved label pair did not fire: " + str(ids)
        assert "Sedation burden" in titles or "sedation" in titles.lower(), "sedation burden not counted: " + titles
        return "Quick check now finds the approved pair and counts sedation"

    def t13_reject():
        c.post("/kb/draft/%d" % did("unknownium"), data={"action": "reject"})
        assert drafts()["unknownium"][0] == "rejected"
        assert "unknownium" not in rx.DRUGS
        return "rejected stays not checkable"

    def t14_alias():
        c.post("/kb/draft/%d" % did("tylenolx"), data={"action": "approve"})
        with rapp.test_request_context():
            assert rx.norm_key("tylenolx") == "paracetamol", "alias not applied: " + rx.norm_key("tylenolx")
        return "brand alias links to the curated entry"

    def t15_pairs_decide():
        ids = [str(r[0]) for r in con.execute("SELECT id FROM kb_pairs WHERE a='alprazolam' AND b='losartan'")]
        c.post("/kb/pairs", data={"action": "approve", "pid": ids})
        loc = json.load(open(os.path.join(appdir, "knowledge", "rules.local.json")))
        dd = [r for r in loc["pairwise"] if r["id"].startswith("DD")]
        assert any(r["a"] == "alprazolam" and r["b"] == "losartan" for r in dd), str(dd)
        ids2 = [str(r[0]) for r in con.execute("SELECT id FROM kb_pairs WHERE status='pending'")]
        c.post("/kb/pairs", data={"action": "dismiss", "pid": ids2})
        assert con.execute("SELECT COUNT(*) FROM kb_pairs WHERE status='pending'").fetchone()[0] == 0
        return "approve writes a DD rule; dismiss clears the rest"

    def t16_curated_wins():
        path = os.path.join(appdir, "knowledge", "drugs.local.json")
        loc = json.load(open(path))
        loc["drugs"]["diltiazem"] = {"class": "HIJACK", "cyp": {}, "burden": {}}
        json.dump(loc, open(path, "w"))
        rx.reload_overlay(force=True)
        assert rx.DRUGS["diltiazem"]["class"] != "HIJACK", "overlay overrode a curated entry"
        return "an overlay entry can never replace a curated one"

    def t17_status_feed():
        assert c.get("/api/feed/status").status_code == 401, "status open without token"
        r = rapp.test_client().get("/api/feed/status", headers={"Authorization": "Bearer " + "t" * 64})
        j = r.get_json() if r.is_json else json.loads(r.get_data(as_text=True))
        assert r.status_code == 200 and j["ok"] and j["drafts"] == 2, str(j)
        return "token-gated counts for GutLog (2 drafts left)"

    def t18_reverify():
        STATE["label_newer"] = True
        kb_sync.run(con, data=feed, now="2026-09-12 03:00")
        STATE["label_newer"] = False
        assert drafts()["ramelteon"][0] == "source_changed", "label change not flagged: " + drafts()["ramelteon"][0]
        assert "re-review" in c.get("/kb").get_data(as_text=True)
        return "next-day re-verify flags an approved entry whose FDA label changed"

    def t19_sources_down():
        STATE["down"] = True
        feed2 = dict(feed)
        feed2["taken"] = feed["taken"] + [{"name": "New", "molecule": "newmolx", "strength": ""}]
        st = kb_sync.run(con, data=feed2, now="2026-09-12 03:30")
        STATE["down"] = False
        d = drafts()["newmolx"][1]
        assert d["gaps"] and not d["props"], "outage should produce gaps, not guesses: " + str(d)
        assert "using copy" in st["fda_cyp"].get("err", "") or st["fda_cyp"]["ok"], str(st["fda_cyp"])
        return "every source down: sync completes, draft is all gaps, cached tables used"

    def t20_fetch_button():
        r = c.post("/kb/fetch", follow_redirects=True)
        assert "test mode" in r.get_data(as_text=True)
        return "Fetch now answers without blocking"

    def t21_login():
        assert rapp.test_client().get("/kb").status_code == 302
        assert rapp.test_client().post("/kb/draft/1", data={"action": "approve"}).status_code == 302
        return "review and approve need the RxGuard login"

    def t22_only_names_leave():
        import inspect
        src = inspect.getsource(ks)
        for bad in ("session", "password", "gutlog_name", "strength"):
            assert ("urllib.parse.quote(" + bad) not in src
        return "only molecule names are put in outgoing URLs"

    def t23_smoke_untouched():
        import subprocess
        out = subprocess.run([sys.executable, "smoke_test.py"], cwd=HERE, capture_output=True, text=True).stdout
        assert "42 passed" in out, out[-300:]
        return "curated smoke suite still 42/42"

    def t24_report():
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kb_sync.report(con)
        out = buf.getvalue()
        assert "DRAFTS" in out and "PAIRS" in out and "ramelteon" in out and "SOURCES" in out, out[:300]
        lub = [l for l in out.splitlines() if "properties:" in l and "A06AX" in l]
        assert lub and "cyp." not in lub[0], "negated label CYP sentence was read as a role: " + str(lub)
        return "terminal report lists sources, drafts and pairs; 'not a substrate of CYP' is not a role"

    def t25_trickle():
        import time as _t
        t0 = _t.monotonic()
        code, body = ks._get(base + "/slow", timeout=2, deadline=1)
        took = _t.monotonic() - t0
        assert code == 0 and took < 2.5, "a trickling server held the call %.1f s (code %s)" % (took, code)
        return "a server that trickles bytes is cut off at the deadline (%.1f s)" % took

    def t26_ddinter_resume_and_rebuild():
        cdir = os.environ["RXGUARD_KB_CACHE"]
        for c in "ABCDGHJLMNPRSV":
            f = os.path.join(cdir, "ddinter_" + c + ".csv")
            if os.path.exists(f):
                os.remove(f)
        STATE["ddi_fail"] = set("NPRSV")
        path, meta = ks.ddinter_db()
        assert not meta["ok"] and meta["failed"] == list("NPRSV") and "files so far" in meta["err"], str(meta)
        assert not ks.ddinter_level(path, "ramelteon", "alprazolam"), "a missing file's pairs appeared"
        feed3 = dict(feed)
        feed3["taken"] = feed["taken"] + [{"name": "Later", "molecule": "laterolx", "strength": ""}]
        st = kb_sync.run(con, data=feed3, now="2026-09-12 04:00")
        assert st["drafts_made"] == 1 and st["stage"] == "done", str(st)
        d = json.loads(con.execute("SELECT draft_json FROM kb_drafts WHERE term='laterolx'").fetchone()[0])
        assert d["ddi_complete"] is False, "partial DDInter not recorded on the draft"
        STATE["ddi_fail"] = set()
        path, meta = ks.ddinter_db()
        assert meta["ok"] and not meta["failed"], "later run did not finish the download: " + str(meta)
        assert ks.ddinter_level(path, "ramelteon", "alprazolam") == "Moderate"
        st = kb_sync.run(con, data=feed3, now="2026-09-12 04:30")
        d = json.loads(con.execute("SELECT draft_json FROM kb_drafts WHERE term='laterolx'").fetchone()[0])
        assert st["drafts_rebuilt"] >= 1 and d["ddi_complete"] is True, str(st)
        return "5 files fail -> partial index, draft marked; next run finishes the files and rebuilds the draft"

    def t27_diag():
        rows = ks.diagnose()
        assert len(rows) == 5 and all(len(r) == 4 for r in rows), str(rows)
        return "connectivity check covers all 5 sources"

    def t28_quality_fixes():
        d = ks.build_draft("comboolol", "", [], {}, ctx["ddi"])
        cls = [p["value"] for p in d["props"] if p["field"] == "class"]
        assert cls == ["beta blocking agents, selective"], "combination class chosen: " + str(cls)
        ss = ks.sentences("Warnings \u2022 Withdrawal effects: symptoms may occur on stopping (5.6) \u2022 Elderly: use a lower dose")
        assert any(x.startswith("Withdrawal effects") and "Elderly" not in x for x in ss), str(ss)
        row = con.execute("SELECT id, draft_json FROM kb_drafts WHERE term='propranolol'").fetchone()
        dj = json.loads(row[1])
        dj["builder"] = 1
        con.execute("UPDATE kb_drafts SET status='pending', draft_json=? WHERE id=?", (json.dumps(dj), row[0]))
        con.commit()
        feed4 = json.loads(json.dumps(feed))
        for r in feed4["regimen"]:
            if r["molecule"] == "propranolol":
                r["strength"] = "2.5 mg"
        st = kb_sync.run(con, data=feed4, now="2026-09-12 05:00")
        r2 = con.execute("SELECT strength, draft_json FROM kb_drafts WHERE id=?", (row[0],)).fetchone()
        d2 = json.loads(r2[1])
        assert st["drafts_rebuilt"] >= 1 and d2["builder"] == ks.BUILDER, "older draft not rebuilt"
        assert r2[0] == "2.5 mg" and d2["strength"] == "2.5 mg", "strength not refreshed: " + str(r2[0])
        return "combination ATC skipped; label bullets split; older pending draft rebuilt with the new strength"

    tests = [("00 FDA table parser", t00_fda_parse), ("01 FDA cache + outage", t01_fda_cache),
             ("02 DDInter index", t02_ddinter), ("03 RxNorm identity", t03_rxnorm),
             ("04 openFDA label choice", t04_openfda), ("05 draft properties", t05_draft_props),
             ("06 draft interaction candidates", t06_draft_pairs), ("07 FDA table beats label", t07_cyp_table_in_draft),
             ("08 sync builds drafts", t08_sync_run), ("09 sync idempotent", t09_sync_idempotent),
             ("10 review page", t10_page), ("11 partial approval", t11_approve_partial),
             ("12 engine uses approval", t12_engine_sees_it), ("13 reject", t13_reject),
             ("14 alias", t14_alias), ("15 pair decisions", t15_pairs_decide),
             ("16 curated wins", t16_curated_wins), ("17 status feed", t17_status_feed),
             ("18 label re-verify", t18_reverify), ("19 all sources down", t19_sources_down),
             ("20 fetch button", t20_fetch_button), ("21 login", t21_login),
             ("22 names only", t22_only_names_leave), ("23 smoke untouched", t23_smoke_untouched),
             ("24 terminal report", t24_report),
             ("25 trickling server cut off", t25_trickle), ("26 DDInter resume + draft rebuild", t26_ddinter_resume_and_rebuild),
             ("27 connectivity check", t27_diag), ("28 draft quality fixes", t28_quality_fixes)]
    print("=" * 66)
    print("RxGuard v1.2.2 sources review - test")
    print("=" * 66)
    for name, fn in tests:
        check(name, fn)
    passed = 0
    for ok, name, detail in RESULTS:
        passed += 1 if ok else 0
        print("[" + ("PASS" if ok else "FAIL") + "] " + name + ("  -- " + detail if detail else ""))
    print("-" * 66)
    print(str(passed) + "/" + str(len(RESULTS)) + " passed")
    srv.shutdown()
    shutil.rmtree(work, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
