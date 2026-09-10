# FitLog Phase 3.5 — Deploy From the Galaxy Fold Only

No PC. No copy-pasting code. Five files uploaded by tapping, then one command.

---

## What you need once

**Termius** from the Play Store — free tier is enough. It has both an SSH
terminal and an SFTP file browser, which is the whole reason to use it over a
plain terminal app.

Set up the host once:

- Hosts → **+** → Label `fitlog`, Address `93.127.195.49`, Port `22`
- Username `root`, then your password or key
- Tap it once to confirm it connects, then back out

Use the **inner screen** for this. It makes the terminal output readable
without squinting.

---

## Step 1 — Save the six files to the phone

From this chat, save all six files. They land in `Downloads`.

## Step 2 — Upload them

In Termius: **SFTP** tab → select `fitlog` → navigate the right pane to
`/root/fitlog/`.

Left pane is your phone. Go to `Downloads`, select these five, upload:

```
deploy_phase35.py
health_ingest.py
migrate_health_ingest.py
patch_register_ingest.py
test_health_ingest.py
```

(`DEPLOY_Phase3.5.md` and `CLAUDE_CODE_BRIEF.md` stay on the phone — reference
only, nothing to upload.)

## Step 3 — Run it

Open the **Terminal** tab on `fitlog`, and type exactly this:

```
cd /root/fitlog && python3 deploy_phase35.py
```

That is the only thing you type. It will:

- find your real database and app file rather than assuming names
- generate the ingest token, mode 600
- migrate the schema, taking its own backup first
- register the blueprint via the anchor-verified patcher
- run the 23/23 smoke suite against a temp database
- restart the service
- verify the endpoint is properly gated, write a probe row, confirm S01
  resolves it, then delete the probe
- **roll back everything automatically** if any check fails

Takes under a minute. Read the last screen.

### If it says `ambiguous`

It found more than one candidate and refused to guess. It prints the
candidates. Re-run naming the right one:

```
cd /root/fitlog && python3 deploy_phase35.py --db /root/fitlog/<name>.db
```

### If it rolls back

Nothing is left half-applied — it restores both the app file and the database
and restarts the service. Send me the output and I'll cut a fix.

### To look without touching anything

```
cd /root/fitlog && python3 deploy_phase35.py --dry-run
```

---

## Step 4 — The token

The last screen prints the ingest token. **Long-press to copy it now**, or
screenshot it. You need it on both phones. To see it again later:

```
cat /root/fitlog/ingest.env
```

---

## Step 5 — Galaxy Fold side (do this now, on this phone)

Samsung Health → Settings → Data management → Health Connect → enable sync.

In Health Connect → App permissions → Samsung Health, confirm write access for
**Steps, Sleep, Heart rate, Exercise**.

Then install any Health Connect exporter that supports HTTP POST and point it
at:

```
https://fit.dr-manoj.in/api/ingest?source=healthconnect
```

Header: `Authorization: Bearer <token>`

This feed only ever fills dates the Watch missed. It cannot inflate a day the
Watch already covered.

## Step 6 — iPhone side (whenever you next pick it up)

Full settings are in `DEPLOY_Phase3.5.md`. Short version:

- Auto-Lock → **Never**, plugged in, black wallpaper, minimum brightness
- Background App Refresh **on** for Health Auto Export
- Health Auto Export → new Automation → REST API
- URL: `https://fit.dr-manoj.in/api/ingest?source=applewatch`
- JSON, Export Version 2, header `Authorization: Bearer <token>`
- Daily **23:00**, window **48 hours**

---

## One thing that may still need me

If the orchestrator reports that your owner-key gate is a `before_request`
hook in the app, the phones will hit that wall instead of bearer auth — the
localhost checks won't catch it. It tells you clearly if so. Send me the
`before_request` block and I'll cut that patcher.

You'll know within a day either way: the FitLog `/api/ingest/status` endpoint
will show `applewatch` as a source once the first export lands.
