# RxGuard — capture status: COMPLETE (2026-08-02)
app.py = server state incl. Apps switcher group. All knowledge, validation cases,
service, backup, README, env.example captured.
Excluded BY DESIGN (never commit): rxguard.db (live data), rxguard.env (live
secret — regenerate from rxguard.env.example on any new deploy), *.log.
