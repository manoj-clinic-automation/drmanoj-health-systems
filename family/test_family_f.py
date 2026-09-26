#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_f.py -- Family Edition Phase F: finding recipes (Kitchen 1.2.0,
GutLog v3.41.0), the Kitchen key file, the fuller food table.

    python3 -B family/test_family_f.py gutlog/app.py

The scratch family plus a kitchen member; synthetic recipes and names only.
Person -> category narrowing shows only that person's categories and counts;
search by ingredient, by contributor name and by a Hindi alias each find the
right recipes; filters combine; the same answers come through a full
member's GutLog and the kitchen-only book; the reader reads its keys from
its own file and says so when it is missing; fat, carbs and calcium appear
on a card from the refreshed table. Each rule has a mutation in
new_assertions_family_f.json. Python 3.9.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import famtest  # noqa: E402
from famtest import check  # noqa: E402
import test_family_c as TC  # noqa: E402
import test_family_d as TD  # noqa: E402

OWNER_APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(famtest.REPO, "gutlog", "app.py")


def ids(j):
    return sorted(x["id"] for x in (j or {}).get("recipes") or [])


def names(j):
    return sorted(x["name"] for x in (j or {}).get("recipes") or [])


def run(rig):
    owner = rig.owner_client()
    m1, m2 = rig.member_client("m1"), rig.member_client("m2")
    m1.post("/m1/welcome", {"name": "Member A", "age": "63", "cond": "ibs", "height_cm": "160"})
    m2.post("/m2/welcome", {"name": "Member B", "age": "68", "cond": "general", "height_cm": "155"})
    rig.stamp_kitchen("k1", "Member C")
    kc1 = rig.kitchen_client("k1")
    k1 = "/kitchen/k1"
    tok = rig.kitchen_tokens()

    # ---------------------------------------------------------------- the pool
    def add(cl, pre, name, grp, ings, method, extra=None, minutes=None):
        r = cl.post(pre + "/api/kitchen/drafts", {"text": "a recipe"}, ctype="json")
        did = (r.json() or {}).get("id")
        body = {"name": name, "grp": grp, "servings": 2, "ingredients": ings, "method": method, "force": True}
        if minutes:
            body["minutes"] = minutes
        body.update(extra or {})
        return (cl.post("%s/api/kitchen/drafts/%s/confirm" % (pre, did), body, ctype="json").json() or {}).get("id")
    A_dal = add(m1, "/m1", "Test lauki dal", "Dal & curry", [{"item": "bottle gourd", "qty": 200, "unit": "g"},
                                                            {"item": "moong dal", "qty": 100, "unit": "g"}],
                ["Cook for 25 min."])
    A_sabzi = add(m1, "/m1", "Test bhindi sabzi", "Sabzi", [{"item": "okra", "qty": 200, "unit": "g"},
                                                            {"item": "onion", "qty": 1, "unit": "medium"}], ["Fry."], minutes=15)
    # cottage cheese, not paneer: the bundled table knows the first and not the second
    B_paneer = add(m2, "/m2", "Test paneer bhurji", "Egg, paneer & fish", [{"item": "cottage cheese", "qty": 200, "unit": "g"},
                                                                          {"item": "tomato", "qty": 1, "unit": "medium"}],
                   ["Crumble.", "Cook 10 minutes."])
    # rice and sugar (the table's first "milk" match is a dry powder, which would make a kheer high-protein)
    B_kheer = add(m2, "/m2", "Test kheer", "Sweet", [{"item": "rice", "qty": 50, "unit": "g"}, {"item": "sugar", "qty": 20, "unit": "g"}],
                  ["Simmer for an hour."])
    O_egg = add(owner, "", "Test egg curry", "Dal & curry", [{"item": "egg", "qty": 4, "unit": "medium"},
                                                            {"item": "onion", "qty": 1, "unit": "medium"}], ["Boil, simmer."])
    _did, pr = TD.publish(kc1, k1, "Test ragi dosa", [{"item": "finger millet flour", "qty": 100, "unit": "g"}], ["Spread, cook."],
                          grp="Breakfast", extra={"minutes": 20})
    C_dosa = (pr.json() or {}).get("id")
    allids = sorted([A_dal, A_sabzi, B_paneer, B_kheer, O_egg, C_dosa])
    assert all(allids), (A_dal, A_sabzi, B_paneer, B_kheer, O_egg, C_dosa)

    def gb(cl, pre, **args):
        from urllib.parse import urlencode
        return cl.get(pre + "/api/kitchen/browse?" + urlencode(args)).json() or {}

    def kb(**args):
        from urllib.parse import urlencode
        return kc1.get(k1 + "/j/browse?" + urlencode(args)).json() or {}

    # ---------------------------------------------------------------- F01 person -> category
    j = gb(m1, "/m1")
    ppl = dict((p["name"], p["n"]) for p in j.get("people") or [])
    grps = dict((g["grp"], g["n"]) for g in j.get("groups") or [])
    check("F01 'Everyone' first with the whole count; every person with their count; the categories with counts",
          j.get("ok") and j.get("everyone") == 6 and ppl.get("Member A") == 2 and ppl.get("Member B") == 2
          and ppl.get("Member C") == 1 and grps.get("Dal & curry") == 2 and grps.get("Sweet") == 1
          and set(ids(j)) == set(allids), "%s %s %s" % (j.get("everyone"), ppl, grps))
    jb = gb(m1, "/m1", by="m2")
    grps_b = dict((g["grp"], g["n"]) for g in jb.get("groups") or [])
    check("F01 Person -> Category: choosing a person narrows the categories to theirs, with counts, and the "
          "list to their recipes",
          set(grps_b) == {"Egg, paneer & fish", "Sweet"} and grps_b["Sweet"] == 1 and ids(jb) == sorted([B_paneer, B_kheer])
          and all(x["added_by"] == "Member B" for x in jb["recipes"]), "%s %s" % (grps_b, names(jb)))
    jg = gb(m1, "/m1", grp="Dal & curry")
    ppl_g = dict((p["name"], p["n"]) for p in jg.get("people") or [])
    check("F01 Category -> Person: choosing a category narrows the people to those who have one there",
          set(ppl_g) == {"Member A", "Owner A"} or (set(ppl_g) >= {"Member A"} and len(ppl_g) == 2)
          and ids(jg) == sorted([A_dal, O_egg]) and jg.get("everyone") == 2, "%s %s" % (ppl_g, names(jg)))

    # ---------------------------------------------------------------- F07 full members credited like kitchen members
    rc_on = subprocess.run([sys.executable, "-B", os.path.join(famtest.fam_src(), "stamp_member.py"), "--no-system",
                            "--root", rig.root, "--code", rig.code, "--kitchen-sync", "--owner-name", "Owner Z"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode
    kall = kb()
    kppl = dict((p["name"], p["n"]) for p in kall.get("people") or [])
    gppl = dict((p["name"], p["n"]) for p in gb(m1, "/m1").get("people") or [])
    by_slug = dict((x["id"], x["added_by"]) for x in kall.get("recipes") or [])
    ka = kb(by="m1")
    check("F07 recipes added from a full member's GutLog are credited and listed in 'By person' exactly like a "
          "kitchen member's, in the kitchen member's own book too; the owner's cards carry the owner's name",
          rc_on == 0 and kppl.get("Member A") == 2 and kppl.get("Member B") == 2 and kppl.get("Member C") == 1
          and kppl.get("Owner Z") == 1 and kppl == gppl
          and by_slug.get(A_dal) == "Member A" and by_slug.get(B_kheer) == "Member B" and by_slug.get(O_egg) == "Owner Z"
          and by_slug.get(C_dosa) == "Member C"
          and ids(ka) == sorted([A_dal, A_sabzi]) and all(x["added_by"] == "Member A" for x in ka.get("recipes") or []),
          "rc %s k1 %s / m1 %s / cards %s" % (rc_on, kppl, gppl, by_slug))

    # ---------------------------------------------------------------- F02 the search box
    s1 = gb(m1, "/m1", q="moong")   # an ingredient that is not in any name (okra would be found through bhindi)
    s2 = gb(m1, "/m1", q="Member B")
    s3 = gb(m1, "/m1", q="ghiya")
    s4 = gb(m1, "/m1", q="lady finger")
    s5 = gb(m1, "/m1", q="sweet")
    s6 = kb(q="ragi")
    s7 = gb(m1, "/m1", q="kaddu")
    check("F02 one search box: an ingredient finds the recipe", ids(s1) == [A_dal], names(s1))
    check("F02 one search box: a contributor's name finds their recipes", ids(s2) == sorted([B_paneer, B_kheer]), names(s2))
    check("F02 one search box: a Hindi alias finds the English ingredient (ghiya -> bottle gourd), and a two-word "
          "English alias finds okra (lady finger); an alias with no recipe finds nothing",
          ids(s3) == [A_dal] and ids(s4) == [A_sabzi] and ids(s7) == [], "%s %s %s" % (names(s3), names(s4), names(s7)))
    check("F02 one search box: a category word finds the category; the kitchen-only book answers the same way "
          "(ragi -> finger millet, by name and by alias)",
          ids(s5) == [B_kheer] and ids(s6) == [C_dosa] and ids(kb(q="finger millet")) == [C_dosa]
          and ids(kb(q="lauki")) == [A_dal], "%s %s" % (names(s5), names(s6)))
    check("F02 results are grouped by category with 'Recipe by <Name>' on each",
          all(x.get("grp") and x.get("added_by") for x in gb(m1, "/m1").get("recipes") or []), "")

    # ---------------------------------------------------------------- F03 filters combine
    f_veg = gb(m1, "/m1", pref="veg")
    f_noon = gb(m1, "/m1", pref="noonion")
    f_meal = gb(m1, "/m1", meal="breakfast")
    f_quick = gb(m1, "/m1", quick="1")
    f_prot = gb(m1, "/m1", protein="1")
    f_mine = gb(m2, "/m2", mine="1")
    f_new = gb(m1, "/m1", new="1")
    check("F03 filters: Vegetarian drops the egg dish; No onion-garlic drops the onion dishes; Breakfast keeps "
          "the breakfast groups (Breakfast; Egg, paneer & fish); Quick keeps 20 minutes or less where a time is "
          "known (stated or in the method)",
          O_egg not in ids(f_veg) and A_dal in ids(f_veg)
          and set(ids(f_noon)) == set(allids) - {A_sabzi, O_egg}
          and ids(f_meal) == sorted([B_paneer, C_dosa]) and ids(f_quick) == sorted([A_sabzi, B_paneer, C_dosa]),
          "veg %s noonion %s meal %s quick %s" % (names(f_veg), names(f_noon), names(f_meal), names(f_quick)))
    check("F03 filters: High-protein keeps 10 g or more a serving (paneer, egg, dal); Made it by me keeps the "
          "person's own; New this week keeps this week's",
          set(ids(f_prot)) >= {B_paneer, O_egg} and B_kheer not in ids(f_prot)
          and ids(f_mine) == sorted([B_paneer, B_kheer]) and set(ids(f_new)) == set(allids),
          "prot %s mine %s new %s" % (names(f_prot), names(f_mine), names(f_new)))
    kc1.post(k1 + "/j/prefs", {"prefs": ["veg"]}, ctype="json")
    kv = kb()
    kv2 = kb(pref="")
    combo = gb(m1, "/m1", by="m1", pref="noonion", q="dal")
    combo2 = gb(m1, "/m1", grp="Sabzi", quick="1", pref="noonion")
    check("F03 filters combine with any view: person + preference + search; category + quick + preference; a "
          "kitchen member's saved preference applies until the page says otherwise; counts follow the filters",
          ids(combo) == [A_dal] and combo["everyone"] == 1 and ids(combo2) == [] and "loosen" in combo2.get("hint", "")
          and "food preference" in combo2["hint"] and "category" in combo2["hint"]
          and O_egg not in ids(kv) and O_egg in ids(kv2), "%s %s %s" % (names(combo), combo2.get("hint"), names(kv)))
    m1.post("/m1/api/kitchen/recipe/%d/rate" % B_paneer, {"stars": 5}, ctype="json")
    f_top = gb(m1, "/m1", top="1")
    check("F03 Top-rated keeps rated 4+ only", ids(f_top) == [B_paneer], names(f_top))

    # ---------------------------------------------------------------- F04 the recipe card, fat / carbs / calcium
    card = m1.get("/m1/api/kitchen/recipe/%d" % B_paneer).json() or {}
    per = (card.get("nutrition") or {}).get("per_serving") or {}
    kcard = kc1.get("%s/j/recipe/%d" % (k1, B_paneer)).json() or {}
    kper = (kcard.get("nutrition") or {}).get("per_serving") or {}
    check("F04 a recipe card shows fat, carbs and calcium per serving from the refreshed table, the same in a full "
          "member's GutLog and the kitchen-only book; the stated minutes show",
          per.get("fat") is not None and per.get("carbs") is not None and per.get("calcium") is not None
          and per.get("fat") > 0 and kper == per and (card.get("recipe") or {}).get("minutes") == 10
          and (m1.get("/m1/api/kitchen/recipe/%d" % A_sabzi).json() or {}).get("recipe", {}).get("minutes") == 15
          and (m1.get("/m1/api/kitchen/recipe/%d" % A_dal).json() or {}).get("recipe", {}).get("minutes") == 25,
          "gut %s / kitchen %s" % (per, kper))
    gut = TC.load(os.path.join(famtest.app_src("gut"), "app.py"), "gut_f",
                  {"GUTLOG_DB": os.path.join(tempfile.mkdtemp(prefix="famf_"), "g.db"), "GUTLOG_NOSPAWN": "1",
                   "GUTLOG_LINKS": "0", "GUTLOG_INSECURE": "1"})
    hit = [h for h in gut.food_lookup("cottage cheese", limit=3) if h.get("full")]
    doc, _byid = gut.food_table()
    check("F04 the bundled table carries the three new columns and GutLog's lookup returns them",
          doc and doc.get("columns")[5:] == ["fat_g", "carbs_g", "calcium_mg"] and hit and hit[0].get("fat") is not None
          and hit[0].get("calcium") is not None, (doc or {}).get("columns"))

    # ---------------------------------------------------------------- F05 the Kitchen key file
    kdir = tempfile.mkdtemp(prefix="famf_keys_")
    keyf = os.path.join(kdir, "kitchen_keys.env")
    with open(keyf, "w") as fh:
        fh.write("ANTHROPIC_API_KEY=test-anthropic-key-1234\nSARVAM_API_KEY=test-sarvam-key-5678\nOTHER=x\n")
    kr = TC.load(os.path.join(famtest.fam_src(), "kitchen_reader.py"), "kreader_f",
                 {"KITCHEN_KEYS_ENV": keyf, "ANTHROPIC_API_KEY": "", "SARVAM_API_KEY": ""})
    for k in ("ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
        os.environ.pop(k, None)
    got = (kr.read_key("ANTHROPIC_API_KEY"), kr.read_key("SARVAM_API_KEY"), kr.read_key("OTHER"))
    kr2 = TC.load(os.path.join(famtest.fam_src(), "kitchen_reader.py"), "kreader_f2",
                  {"KITCHEN_KEYS_ENV": os.path.join(kdir, "missing.env"), "ANTHROPIC_API_KEY": "", "SARVAM_API_KEY": ""})
    for k in ("ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
        os.environ.pop(k, None)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ex, note = kr2.llm_structure("some recipe text")
        ex2, note2 = kr2.llm_structure("another")
    said = buf.getvalue()
    src = open(os.path.join(famtest.fam_src(), "kitchen_reader.py"), encoding="utf-8").read()
    check("F05 the reader reads the two keys from its own key file (default /root/family/kitchen_keys.env), reads no "
          "other variable from it, never names the clinic's file, and with the file missing says so once and hands "
          "the draft to the manual screen",
          got == ("test-anthropic-key-1234", "test-sarvam-key-5678", "") and kr.KEYS_ENV == keyf
          and kr2.KEYS_ENV.endswith("missing.env") and ex is None and "by hand" in note and ex2 is None
          and said.count("kitchen key file") == 1 and "install -o root -g fam_kitchen -m 640" in said
          and "/root/wa/.env" not in src.split('"""')[2] and 'KITCHEN_KEYS_ENV", "/root/family/kitchen_keys.env"' in src,
          "got %s said %r note %r" % (got, said[:160], note))

    # ---------------------------------------------------------------- F06 nothing personal in the aliases file
    with open(os.path.join(famtest.fam_src(), "food_aliases.json"), encoding="utf-8") as fh:
        al = json.load(fh)
    flat = " ".join(w for g in al["groups"] for w in g).lower()
    check("F06 the alias file is plain food words (lauki = bottle gourd = ghiya ...), nothing else",
          any("bottle gourd" in g and "ghiya" in g for g in al["groups"]) and len(al["groups"]) >= 40
          and not any(w in flat for w in ("mg", "tablet", "dose", "member")), len(al["groups"]))
    _ = (sqlite3, datetime, timedelta)


def main():
    rig = famtest.Rig(OWNER_APP)
    try:
        run(rig)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("F00 the suite ran to the end", False, repr(exc))
        rig.dump_logs()
    finally:
        rig.stop()
    return famtest.finish()


if __name__ == "__main__":
    sys.exit(main())
