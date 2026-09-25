#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entry_gut.py -- GutLog for one family member.

FAMILY_EDITION_V1. `python3 -m gunicorn ... entry_gut:application`, run by
family-gut@<slug>.service as the member's own Linux user. It imports the
owner's GutLog code UNCHANGED and adds, for a member only:

  * every path pointed into the member's folder (family_env);
  * no owner banners: the Health Mirror line is off, the sign-in page names
    nobody, owner-only seed files simply do not exist in the family code tree;
  * the caretaker layer (family_care): /care/in, tagging, the switch;
  * /welcome -- the first-run setup (name, age, sex, height, weight,
    conditions, allergies, usual meal times, night tablet);
  * /care -- the member's switch and the list of caretaker changes;
  * /api/care/status -- the ONE endpoint the owner's Family page reads:
    bearer-gated, summary fields only, and {"access": "off"} when off;
  * the profile (gut / joint / general) choosing what the Now page shows first;
  * the path prefix (family_prefix).

Python 3.9.
"""
import html
import json
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import family_env  # noqa: E402

M = family_env.Member()
_gdir = M.path("gutlog")
M.apply_env(dict({
    "GUTLOG_DB": os.path.join(_gdir, "health.db"),
    "GUTLOG_UPLOADS": os.path.join(_gdir, "uploads"),
    "GUTLOG_PLANS": os.path.join(_gdir, "plans_files"),
    "GUTLOG_FEED_TOKEN_FILE": M.path("feed.token"),
    "GUTLOG_LINKS": "1",
    "GUTLOG_RXGUARD_URL": M.local("rx"),
    "GUTLOG_FITLOG_URL": M.local("fit"),
    "GUTLOG_MIRROR_STAMP": os.path.join(_gdir, "no-mirror"),
    "GUTLOG_MEALS_FILE": os.path.join(_gdir, "meals.local.json"),
    "GUTLOG_PLAN_FILE": os.path.join(_gdir, "diet_plan.local.json"),
    # GutLog v3.38.0 -- the Family Kitchen, as this member.
    "GUTLOG_KITCHEN_URL": "http://127.0.0.1:%s/kitchen" % os.environ.get("FAMILY_KITCHEN_PORT", "8199"),
    "GUTLOG_KITCHEN_TOKEN_FILE": M.path("kitchen.token"),
    "GUTLOG_KITCHEN_CAPTURE_FILE": M.path("kitchen.capture"),
    "GUTLOG_KITCHEN_CAPTURE_URL": M.base + "/kitchen/capture/" + M.slug,
    "GUTLOG_KITCHEN_HELP_URL": M.base + "/kitchen/help",
    "GUTLOG_INSECURE": "1" if M.insecure else "0",
}, **M.sso_env()))
if not M.insecure:
    os.environ.pop("GUTLOG_INSECURE", None)

sys.path.insert(0, family_env.code_dir("gut"))
import app as gut  # noqa: E402  -- the owner's GutLog, unchanged
import family_care  # noqa: E402
import family_prefix  # noqa: E402
from flask import request, session, jsonify, redirect, Response  # noqa: E402

flask_app = gut.app
flask_app.config.update(SESSION_COOKIE_NAME=M.cookie("gut"),
                        SESSION_COOKIE_PATH=M.prefixes["gut"] + "/")

# ------------------------------------------------------------ owner bits off
# The mirror is the owner's; a member has none, so there is nothing to warn about.
flask_app.view_functions["api_mirror"] = lambda: jsonify(ok=True, off=True, text="")
# The Family page is the owner's; in a member's copy it does not exist.
for _ep in ("family_page", "family_open"):
    if _ep in flask_app.view_functions:
        flask_app.view_functions[_ep] = lambda **kw: ("Not found", 404)
if "api_family_has" in flask_app.view_functions:
    flask_app.view_functions["api_family_has"] = lambda: jsonify(n=0)
gut.AUTH_PAGE = gut.AUTH_PAGE.replace("Personal health diary - Dr. Manoj Agarwal",
                                      "Family health diary")

# ------------------------------------------------------------ member profile
CONDITIONS = family_env.MEMBER_CONDITIONS   # code, label; shared with RxGuard where it has one
COND_CODES = set(c for c, _ in CONDITIONS)


def member_profile():
    try:
        p = json.loads(gut.setting("member_profile") or "{}")
        return p if isinstance(p, dict) else {}
    except ValueError:
        return {}


def member_age(p=None):
    p = p if p is not None else member_profile()
    try:
        a = int(p.get("age"))
        return a if 0 < a < 120 else None
    except (TypeError, ValueError):
        return None


def _feed_profile():
    """The member's condition codes and age, for their own RxGuard. Codes
    only -- never the free text."""
    p = member_profile()
    codes = [c for c in (p.get("conditions") or []) if c in COND_CODES]
    return jsonify(ok=True, app="gutlog", conditions=codes, age=member_age(p))


flask_app.view_functions["api_feed_profile"] = gut.feed_required(_feed_profile)

# ------------------------------------------------------------ caretaker layer
CARE = family_care.Care(M.slug, M.dir, M.base, M.prefixes)

LABELS = {
    "api_prnmeds": "Added a medicine", "api_schedule_post": "Set a medicine schedule",
    "api_schedule_close": "Ended a medicine schedule", "api_salt": "Set salts / strength",
    "api_now_dose": "Logged a dose", "api_now_undo": "Undid a dose", "api_doses": "Logged a dose",
    "api_dose_plus": "Logged a dose", "api_meals": "Logged a meal",
    "api_mealcards_log": "Logged a meal", "api_meal_replace": "Changed a meal",
    "api_meal_delete": "Deleted a meal", "api_meal_again": "Logged a meal again",
    "api_dish_edit": "Edited a dish", "api_foods_new": "Added a food",
    "api_library_add": "Added a food", "api_library_edit": "Edited a food",
    "api_vitals": "Recorded vitals", "api_episodes": "Recorded a symptom",
    "api_pain": "Recorded pain", "api_episode_eased": "Marked pain eased",
    "api_upload": "Added a report / file", "api_delete": "Deleted an entry",
    "api_retime": "Changed a time", "api_ft_log": "Food test step",
    "api_ft_score": "Food test score", "api_ft_outcome": "Food test outcome",
    "api_consults": "Recorded a consultation", "api_labs": "Recorded a lab value",
    "api_activity_add": "Recorded activity", "api_day": "Updated the day",
    "api_rec_plan_set": "Updated the test plan", "api_plans_add": "Added a plan",
    "api_plans_edit": "Edited a plan", "api_quickbite": "Logged a quick bite",
    "api_snack_late": "Logged a late snack", "welcome": "Filled the setup form",
    "api_joint": "Recorded joint pain",
}


def what_for(endpoint, path, method):
    return LABELS.get(endpoint) or (endpoint or path).replace("_", " ").strip()


import family_auth  # noqa: E402

family_care.install(flask_app, CARE, "gut", lambda s: gut.stamp_session(),
                    what_for=what_for,
                    blocked=("account", "setup", "care_switch", "login") + family_auth.CARETAKER_BLOCKED,
                    health_sso=gut.health_sso)
# PIN sign-in with lockout, Face ID / Touch ID, 12 months on the member's own device.
AUTH = family_auth.install(flask_app, "gut", M, CARE, lambda s: gut.stamp_session(),
                           lambda s: bool(s.get("ok")), passkeys=True)


def signed_in():
    return bool(session.get("ok")) and session.get("ep") == gut.auth_epoch()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%(title)s</title>
<style>
:root{--bg:#f6f7f9;--fg:#17202a;--mut:#5b6773;--card:#fff;--line:#d9dee4;--acc:#1f6f5c;--warn:#8a4b00}
@media (prefers-color-scheme: dark){:root{--bg:#111821;--fg:#e6edf3;--mut:#9fb0bf;--card:#18222d;--line:#2b3947;--acc:#7ec8a8;--warn:#f0b35a}}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:34rem;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 12px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 12px}
label{display:block;font-weight:600;margin:10px 0 4px}input,select,textarea{width:100%%;box-sizing:border-box;font:inherit;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
.row{display:flex;gap:8px}.row>*{flex:1;min-width:0}.chk{display:flex;align-items:center;gap:10px;font-weight:400;margin:6px 0}
.chk input{width:22px;height:22px;flex:none}button,.btn{display:inline-block;font:inherit;font-weight:600;padding:12px 16px;border-radius:10px;border:0;background:var(--acc);color:#fff;text-decoration:none;margin-top:12px}
.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc)}.hint{color:var(--mut);font-size:15px}
.log{border-top:1px solid var(--line);padding:8px 0}.log:first-child{border-top:0}.tag{color:var(--warn);font-weight:600}
</style></head><body><main>%(body)s</main></body></html>"""


def page(title, body, code=200):
    return Response(PAGE % {"title": html.escape(title), "body": body}, status=code,
                    mimetype="text/html")


# ------------------------------------------------------------ first run
@flask_app.route("/welcome", methods=["GET", "POST"])
@gut.login_required
def welcome():
    p = member_profile()
    if request.method == "POST":
        f = request.form
        conds = [c for c in f.getlist("cond") if c in COND_CODES]

        def num(k, lo, hi):
            try:
                v = float(f.get(k, ""))
                return v if lo <= v <= hi else None
            except ValueError:
                return None
        age = num("age", 1, 119)
        p = {"name": (f.get("name") or "").strip()[:60],
             "age": int(age) if age else None,
             "sex": f.get("sex") if f.get("sex") in ("F", "M", "Other") else "",
             "height_cm": num("height_cm", 50, 250), "weight_kg": num("weight_kg", 20, 300),
             "conditions": conds, "conditions_other": (f.get("cond_other") or "").strip()[:500],
             "allergies": (f.get("allergies") or "").strip()[:300],
             "meal_times": {k: gut._valid_hm(f.get("t_" + k)) or "" for k in ("breakfast", "lunch", "dinner")},
             "night_tablet": f.get("night_tablet") == "yes",
             "updated": gut.now_s()}
        gut.set_setting("member_profile", json.dumps(p))
        gut.set_setting("member_setup_done", "1")
        if p["weight_kg"]:
            gut.db().execute("INSERT INTO vitals(day, vtime, weight, notes, created) VALUES(?,?,?,?,?)",
                             (gut.today(), gut.now_hm(), p["weight_kg"], "from setup", gut.now_s()))
            gut.db().commit()
        return redirect("/")
    e = lambda k: html.escape(str(p.get(k) or ""))
    mt = p.get("meal_times") or {}
    chks = "".join('<label class="chk"><input type="checkbox" name="cond" value="%s"%s> %s</label>'
                   % (c, " checked" if c in (p.get("conditions") or []) else "", html.escape(lb))
                   for c, lb in CONDITIONS)
    sex = p.get("sex") or ""
    sel = lambda v: " selected" if sex == v else ""
    body = ("<h1>A few details to begin</h1>"
            "<p class='hint'>Filled once, by you or your caretaker. You can change it later from "
            "the Caretaker page.</p><form method='post' class='card'>"
            "<label>Name</label><input name='name' value='%s' autocomplete='off'>"
            "<div class='row'><div><label>Age</label><input name='age' inputmode='numeric' value='%s'></div>"
            "<div><label>Sex</label><select name='sex'><option value=''></option>"
            "<option value='F'%s>Female</option><option value='M'%s>Male</option>"
            "<option value='Other'%s>Other</option></select></div></div>"
            "<div class='row'><div><label>Height (cm)</label><input name='height_cm' inputmode='decimal' value='%s'></div>"
            "<div><label>Weight (kg)</label><input name='weight_kg' inputmode='decimal' value='%s'></div></div>"
            "<label>Conditions</label>%s"
            "<label>Other conditions</label><textarea name='cond_other' rows='2'>%s</textarea>"
            "<label>Allergies</label><input name='allergies' value='%s'>"
            "<label>Usual meal times</label><div class='row'>"
            "<div><span class='hint'>Breakfast</span><input type='time' name='t_breakfast' value='%s'></div>"
            "<div><span class='hint'>Lunch</span><input type='time' name='t_lunch' value='%s'></div>"
            "<div><span class='hint'>Dinner</span><input type='time' name='t_dinner' value='%s'></div></div>"
            "<label>A tablet at night to sleep?</label><select name='night_tablet'>"
            "<option value='no'>No</option><option value='yes'%s>Yes</option></select>"
            "<button>Save</button></form>"
            "<p class='hint'>Medicines are added from the Now page: Add medicine, then Salts, then Schedule.</p>"
            % (e("name"), e("age"), sel("F"), sel("M"), sel("Other"), e("height_cm"), e("weight_kg"),
               chks, e("conditions_other"), e("allergies"),
               html.escape(mt.get("breakfast", "")), html.escape(mt.get("lunch", "")),
               html.escape(mt.get("dinner", "")), " selected" if p.get("night_tablet") else ""))
    return page("Setup", body)


@flask_app.before_request
def _first_run():
    if request.endpoint == "home" and request.method == "GET" and signed_in() \
            and not gut.setting("member_setup_done"):
        return redirect("/welcome")
    return None


# ------------------------------------------------------------ member's own page
@flask_app.route("/care")
@gut.login_required
def care_page():
    is_care = bool(session.get("care"))
    on = CARE.access_on()
    rows = CARE.entries()
    items = "".join(
        "<div class='log'><span class='tag'>by caretaker (%s)</span> &middot; %s IST<br>%s"
        "<span class='hint'> &middot; %s</span></div>"
        % (html.escape(r["who"]), html.escape(r["at"][:16].replace("T", " ")),
           html.escape(r["what"] or r["path"]), html.escape({"gut": "GutLog", "rx": "RxGuard",
                                                               "fit": "FitLog"}.get(r["app"], r["app"])))
        for r in rows) or "<p class='hint'>No changes by a caretaker yet.</p>"
    if is_care:
        top = ("<div class='card'><p>You are signed in as <b>caretaker</b>. "
               "Every change you make is shown to them here.</p></div>")
    else:
        top = ("<div class='card'><p>Caretaker access is <b>%s</b>.</p>"
               "<p class='hint'>When it is on, your caretaker can set up your medicines, schedule, "
               "salts, conditions, reports, dishes and food test, see what you log, and log for you "
               "when you ask. Every change they make is listed below. When it is off, they get "
               "nothing at all.</p>"
               "<form method='post' action='/care/switch'><input type='hidden' name='on' value='%s'>"
               "<button>%s</button></form></div>"
               % ("ON" if on else "OFF", "0" if on else "1",
                  "Switch caretaker access off" if on else "Switch caretaker access on"))
        CARE.mark_seen()
    body = ("<h1>Caretaker</h1>%s<div class='card'><p><b>Changes by your caretaker</b></p>%s</div>%s"
            "<a class='btn ghost' href='/welcome'>Your details</a> <a class='btn ghost' href='/'>Back</a>"
            % (top, items, signin_card(is_care)))
    return page("Caretaker", body)


def signin_card(is_care):
    """Sign-in: every attempt (wrong PIN, locked out, signed in), the Face ID
    devices, change the PIN, sign out everywhere. The caretaker sees, never changes."""
    esc = html.escape
    log = "".join("<div class='log'>%s IST &middot; %s<span class='hint'> &middot; %s &middot; %s</span></div>"
                  % (esc(r["at"][:16]), esc(r["result"]), esc(r["app"]), esc(r["ip"] or ""))
                  for r in AUTH.entries(15)) or "<p class='hint'>No sign-ins yet.</p>"
    pks = AUTH.passkeys()
    dev = "".join("<div class='log'>%s &middot; set up %s%s%s</div>"
                  % (esc(p["label"] or "a device"), esc(p["created"] or ""),
                     (" &middot; last used " + esc(p["last_used"])) if p["last_used"] else "",
                     "" if is_care else (" <form method='post' action='/passkey/remove/%d' style='display:inline'>"
                                         "<button class='ghost' style='padding:4px 10px;margin:0'>Remove</button>"
                                         "</form>" % p["id"]))
                  for p in pks) or "<p class='hint'>No Face ID / Touch ID yet. It is offered after a PIN sign-in.</p>"
    msg = {"changed": "<p><b>PIN changed.</b></p>",
           "refused": "<p class='tag'>PIN not changed: check the current PIN, and choose 6 digits that are not "
                      "all the same or in a straight run.</p>"}.get(request.args.get("pin"), "")
    own = "" if is_care else (
        "<p><b>Change your PIN</b></p>%s<form method='post' action='/pin/change'>"
        "<div class='row'><div><label>Current PIN</label><input name='old' type='password' inputmode='numeric' "
        "maxlength='6'></div><div><label>New PIN</label><input name='new' type='password' inputmode='numeric' "
        "maxlength='6'></div></div><button>Change PIN</button></form>"
        "<form method='post' action='/signout-all'><button class='ghost'>Sign out on all devices</button></form>" % msg)
    return ("<div class='card'><p><b>Sign-in</b></p><p class='hint'>You stay signed in on your own phone for a "
            "year unless you sign out. Five wrong PINs pause sign-in for 15 minutes, longer each time.</p>"
            "<p><b>Face ID / Touch ID</b></p>%s%s<p><b>Recent sign-in attempts</b></p>%s</div>" % (dev, own, log))


@flask_app.route("/care/switch", methods=["POST"])
@gut.login_required
def care_switch():
    CARE.set_access(request.form.get("on") == "1", "member")
    return redirect("/care")


@flask_app.route("/api/care/me")
@gut.login_required
def api_care_me():
    unseen = sum(1 for r in CARE.entries() if not r["seen"])
    return jsonify(care=session.get("care") or None, access=CARE.access_on(),
                   unseen=unseen, profile=M.profile, order=NOW_ORDER.get(M.profile, []))


# ------------------------------------------------------------ status (Family page)
def _minutes(hm):
    try:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def status_summary():
    con = gut.db()
    tday = gut.today()
    last = None
    for sql in ("SELECT MAX(created) FROM meals", "SELECT MAX(created) FROM doses",
                "SELECT MAX(created) FROM episodes", "SELECT MAX(created) FROM vitals",
                "SELECT MAX(created) FROM activities", "SELECT MAX(created) FROM ft_log",
                "SELECT MAX(updated) FROM days"):
        try:
            v = con.execute(sql).fetchone()[0]
        except Exception:
            v = None
        if v and (last is None or v > last):
            last = v
    rows = con.execute(
        "SELECT s.slot, d.status FROM med_schedule s LEFT JOIN doses d "
        "ON d.sched_id = s.id AND d.day = ? WHERE s.valid_from <= ? "
        "AND (s.valid_to = '' OR s.valid_to >= ?)", (tday, tday, tday)).fetchall()
    slot_t = dict((s, t) for s, _lb, t in gut.SLOTS)
    try:
        window = int(gut.setting("slot_window_min") or 120)
    except ValueError:
        window = 120
    nowm = _minutes(gut.now_hm())
    taken = missed = due = 0
    for r in rows:
        if r["status"] in ("TAKEN", "SKIPPED"):
            taken += 1
        else:
            t = _minutes(slot_t.get(r["slot"], "23:59"))
            if t is not None and nowm is not None and nowm > t + window:
                missed += 1
            else:
                due += 1
    bp = con.execute("SELECT day, vtime, sys, dia FROM vitals WHERE sys IS NOT NULL "
                     "ORDER BY day DESC, vtime DESC LIMIT 1").fetchone()
    rep = None
    for sql in ("SELECT MAX(day) FROM rec_docs", "SELECT MAX(day) FROM files"):
        try:
            v = con.execute(sql).fetchone()[0]
        except Exception:
            v = None
        if v and (rep is None or v > rep):
            rep = v
    days_rep = None
    if rep:
        try:
            days_rep = (date.today() - date.fromisoformat(rep[:10])).days
        except ValueError:
            days_rep = None
    rx = gut._link_get(gut.RXGUARD_URL + "/api/feed/status", ttl=120) or {}
    return {"last_entry": last,
            "doses": {"total": len(rows), "taken": taken, "due": due, "missed": missed},
            "rx": {"red": rx.get("red"), "amber": rx.get("amber")} if rx.get("ok") else None,
            "bp": {"day": bp["day"], "time": bp["vtime"], "sys": bp["sys"], "dia": bp["dia"]} if bp else None,
            "days_since_report": days_rep}


@flask_app.route("/api/care/status")
def api_care_status():
    if not CARE.status_ok(request.headers.get("Authorization")):
        return jsonify(ok=False, err="unauthorised"), 401
    if not CARE.access_on():
        return jsonify(ok=True, access="off")
    out = status_summary()
    out.update(ok=True, access="on")
    return jsonify(out)


# ------------------------------------------------------------ Now page: bar + profile order
NOW_ORDER = {
    "gut": [],
    "general": ["nowDoses", "nowBP", "nowMeal", "nowPain", "nowAct", "nowSym"],
    "joint": ["nowJoint", "nowPainMeds", "nowJointWatch", "nowDoses", "nowPain", "nowBP", "nowLipid",
              "nowAct", "nowMeal"],
}

NOW_SNIPPET = """
<div id="famBar" style="margin:0 0 10px"></div>
<script>
/* FAMILY_EDITION_V1 -- caretaker bar and the profile's card order. */
(function(){
  function go(){
    fetch('/api/care/me',{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(j){
      var tab=document.getElementById('tab-now'), bar=document.getElementById('famBar');
      if(!tab||!bar)return;
      var anchor=bar.nextSibling;
      (j.order||[]).slice().reverse().forEach(function(id){
        var c=document.getElementById(id);
        if(c&&c.parentNode===tab)tab.insertBefore(c,anchor);
        anchor=c&&c.parentNode===tab?c:anchor;
      });
      var t;
      if(j.care){t='Signed in as caretaker ('+j.care+'). Your changes are shown to them.';}
      else if(j.unseen){t=j.unseen+' change'+(j.unseen>1?'s':'')+' by your caretaker. ';}
      else{t='Caretaker access: '+(j.access?'on':'off')+'. ';}
      bar.innerHTML='';
      var p=document.createElement('p');p.className='hint';p.style.margin='0 2px';
      p.appendChild(document.createTextNode(t+' '));
      var a=document.createElement('a');a.href='/care';a.textContent=j.care?'Changes':'Caretaker';
      p.appendChild(a);bar.appendChild(p);
    }).catch(function(){});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();
})();
</script>
"""
_NOW_ANCHOR = '<section class="tab sel" id="tab-now">'
_APPLE_TITLE = '<meta name="apple-mobile-web-app-title" content="GutLog">'


@flask_app.after_request
def _inject_now(resp):
    try:
        if request.endpoint == "home" and resp.status_code == 200 \
                and (resp.mimetype or "") == "text/html":
            t = resp.get_data(as_text=True)
            if _NOW_ANCHOR in t and "famBar" not in t:
                t = t.replace(_NOW_ANCHOR, _NOW_ANCHOR + NOW_SNIPPET, 1)
                # Add to Home Screen from the diary names the member, as the sign-in page does.
                t = t.replace(_APPLE_TITLE, '<meta name="apple-mobile-web-app-title" content="%s">'
                              % html.escape(M.name[:30], quote=True), 1)
                resp.set_data(t)
    except Exception:
        pass
    return resp


application = family_prefix.PrefixApp(
    flask_app.wsgi_app, M.prefixes["gut"],
    family_prefix.route_segments(flask_app.url_map),
    host_map=M.host_map(), manifest_label=M.name, manifest_name=family_auth.title_for("gut", M.name))
