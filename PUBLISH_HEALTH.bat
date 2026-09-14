@echo off
setlocal enabledelayedexpansion
REM ===========================================================================
REM  PUBLISH_HEALTH.bat  v2  -  publish the health-systems repo.
REM
REM  Sibling of PUBLISH_ALL.bat in drmanoj-clinic-automation, same gates,
REM  pointed at this repo instead.
REM
REM  Usage:
REM    PUBLISH_HEALTH.bat "your commit message"
REM    PUBLISH_HEALTH.bat            falls back to a generic message
REM
REM  Gates kept from PUBLISH_ALL:
REM   - stale git lock sweep: no git running + lock present = stale, cleared
REM     and said out loud. Git running + lock = real, refuse.
REM   - refuses if .gitignore silently drops a file from an app folder
REM   - a failed commit is a FAILURE, never "nothing to commit"
REM   - after pushing, VERIFIES origin HEAD == local HEAD or refuses to
REM     print success. The projection is the check.
REM
REM  Gate added for this repo:
REM   - refuses if a live database, an env file or a backup snapshot is staged.
REM     THIS REPOSITORY IS PUBLIC. health3.db and fitlog.db are the diary and
REM     ingest.env holds the bearer tokens; none of it may ever be committed.
REM
REM  2026-09-13: the backup half of that gate matched only .bak_ and .bak- .
REM  The patchers in this repo write FIVE spellings - .bak , .bak.STAMP ,
REM  .bak_STAMP , .bak-LABEL-STAMP and .bak-YYYY-MM-DD - so a .bak. copy of
REM  app.py sailed straight through. One pattern, [.]bak , now catches every
REM  variant; .deployed and a leftover .tmp write target are caught too.
REM  Square brackets are safe here: batch only treats round brackets and
REM  the and/pipe/redirect characters as special, and [.] is findstr's
REM  unambiguous way to mean a literal dot.
REM
REM  2026-09-14 - v2, three fixes, each one proved against a scratch repo
REM  with its own bare origin rather than reasoned about:
REM
REM   1. THE WINDOW CLOSED ON SUCCESS, so the one line worth reading - did
REM      this actually reach origin - was the one line nobody ever saw. Every
REM      exit path now ends the same way: call :summary, then pause, then
REM      exit. Success and failure alike. The summary names every gate and
REM      prints local HEAD and origin HEAD IN FULL, so the push is checked
REM      against GitHub by eye instead of taken on trust.
REM
REM   2. THE SECRET GATE HAD A HOLE, and it was the three worst patterns.
REM      `git diff --cached --name-only > file` writes LF line ends; findstr
REM      only recognises CRLF, so it read the whole staged list as ONE line
REM      and a $ anchor could only ever match at the end of the file. So
REM      [.]db$ , [.]env$ and [.]tmp$ - the diary, the bearer tokens, and a
REM      patcher's half-written copy - never matched. Proved: a lone staged
REM      health3.db went through the v1 gate, was committed AND pushed.
REM      Nothing ever leaked, because .gitignore was doing the real work -
REM      199 paths added across the whole history, none of them matching.
REM      But this was the second layer, and the second layer was not there.
REM      The list is now matched ONE PATH AT A TIME through a pipe, which
REM      ends the line with CRLF whatever git wrote, so the anchors work and
REM      the offending path is named instead of the list being searched twice.
REM
REM   3. EVERY WARNING MARKER WAS INVISIBLE. With delayed expansion on, a
REM      bare double-bang in an echo is swallowed by the parser, so every
REM      "REFUSING" and "NOT FOUND" line printed without its marker. Carets
REM      are processed before delayed expansion, so one caret is not enough
REM      either; two are. That is why the markers below are written doubled.
REM
REM  :selftest runs on EVERY publish and refuses if the gate cannot catch a
REM  name it must catch or wrongly catches one it must not. A gate nobody has
REM  seen fail is not a gate - CLAUDE.md rule 2a, applied to this script.
REM
REM  Message lines below deliberately contain no round brackets and none of
REM  the and/pipe/redirect characters: a bare bracket inside an if-block
REM  closes it early and everything after runs unconditionally. Labels cannot
REM  be closed by a stray bracket.
REM ===========================================================================
set REPO_DIR=D:\dr-manoj-git\drmanoj-health-systems

REM ---- what the run will report at the end -----------------------------------
REM  "not reached" is deliberate and is NOT a pass: it means the run stopped
REM  before that gate ran. A gate that never ran must never read as green.
set "G_LOCK=not reached"
set "G_SELFTEST=not reached"
set "G_PATHS=not reached"
set "G_SECRETS=not reached"
set "G_PARITY=not reached"
set "G_IGNORE=not reached"
set "G_COMMIT=not reached"
set "G_PUSH=not reached"
set "G_VERIFY=not reached"
set "LOCALH=not read"
set "REMOTEH=not read"
set "VERDICT=NOT PUBLISHED"

if exist "%REPO_DIR%\.git" goto :repo_ok
echo ^^!^^! repo not found at %REPO_DIR%
call :summary
pause
exit /b 1

:repo_ok
set MSG=%~1
if not defined MSG set MSG=publish: pending health-systems changes

set GIT=
where git >nul 2>&1 && set GIT=git
if not defined GIT if exist "C:\Program Files\Git\cmd\git.exe" set GIT="C:\Program Files\Git\cmd\git.exe"
if not defined GIT if exist "C:\Program Files (x86)\Git\cmd\git.exe" set GIT="C:\Program Files (x86)\Git\cmd\git.exe"
if not defined GIT for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%D\resources\app\git\cmd\git.exe" set GIT="%%D\resources\app\git\cmd\git.exe"
if defined GIT goto :git_ok
echo ^^!^^! git.exe not found
call :summary
pause
exit /b 1

:git_ok
cd /d "%REPO_DIR%"
echo Using git: %GIT%
echo Message  : %MSG%
echo.

REM ---- stale git lock sweep -------------------------------------------------
set LOCKFOUND=
if exist ".git\index.lock" set LOCKFOUND=1
if exist ".git\HEAD.lock" set LOCKFOUND=1
if exist ".git\config.lock" set LOCKFOUND=1
if exist ".git\shallow.lock" set LOCKFOUND=1
if not defined LOCKFOUND goto :locks_clear

tasklist /fi "IMAGENAME eq git.exe" 2>nul | find /i "git.exe" >nul
if not errorlevel 1 goto :git_running

echo    stale git lock found, and no git process is running - clearing it.
if exist ".git\index.lock" del /f /q ".git\index.lock"
if exist ".git\HEAD.lock" del /f /q ".git\HEAD.lock"
if exist ".git\config.lock" del /f /q ".git\config.lock"
if exist ".git\shallow.lock" del /f /q ".git\shallow.lock"
set LOCKFOUND=
if exist ".git\index.lock" set LOCKFOUND=1
if exist ".git\HEAD.lock" set LOCKFOUND=1
if defined LOCKFOUND goto :lock_stuck
set "G_LOCK=PASS - a stale lock was found and cleared"
goto :locks_read_head

:lock_stuck
set "G_LOCK=FAILED - the lock could not be removed"
echo ^^!^^! the lock could not be removed - something still holds it.
echo    Close GitHub Desktop / editors and run this again. NOTHING published.
call :summary
pause
exit /b 1

:git_running
set "G_LOCK=FAILED - a lock exists and git is running"
echo ^^!^^! a git lock exists AND a git process is running.
echo    Close GitHub Desktop / any editor doing git, then run this again.
echo    NOTHING published.
call :summary
pause
exit /b 1

:locks_clear
set "G_LOCK=PASS - no lock found"

:locks_read_head
REM  Read HEAD now, so a run that stops at an early gate still reports where
REM  the repo actually is rather than "not read".
for /f %%H in ('%GIT% rev-parse HEAD 2^>nul') do set "LOCALH=%%H"

echo Checking what is pending...
%GIT% add -A .
if not errorlevel 1 goto :add_ok
echo ^^!^^! git add FAILED
call :summary
pause
exit /b 1

:add_ok

REM ---- secret and live-data gate -------------------------------------------
REM  One definition, used by the self-test AND by the check, so the two can
REM  never drift apart.
set "BADPAT=[.]db$ [.]db[.] [.]env$ [.]bak [.]deployed [.]tmp$ [.]token [.]secret ingest[.]env"

REM ---- the gate proves itself before it is trusted --------------------------
echo Self-testing the secret gate...
set "SELFT="
for %%X in (gutlog/health3.db fitlog/fitlog.db fitlog/ingest.env gutlog/app.py.bak app.py.bak.20260913-182325 gutlog/app.py.deployed-2026-07-21 scratch.tmp gutlog/feed.token health3.db.secret) do (
  echo %%X| findstr /i /r "%BADPAT%" >nul
  if errorlevel 1 set "SELFT=a name that MUST be blocked was not caught: %%X"
)
for %%X in (gutlog/app.py CHANGELOG.md tools/NO_SECRETS.py fitlog/DOSSIER.md gutlog/test_phase_m.py) do (
  echo %%X| findstr /i /r "%BADPAT%" >nul
  if not errorlevel 1 set "SELFT=an ordinary file was wrongly blocked: %%X"
)
if defined SELFT goto :gate_broken
set "G_SELFTEST=PASS - catches every blocked shape, clears ordinary files"

echo Checking staged files for databases, env files and snapshots...
%GIT% diff --cached --name-only > "%TEMP%\_health_staged.txt"
set "BADFOUND="
for /f "usebackq delims=" %%F in ("%TEMP%\_health_staged.txt") do (
  echo %%F| findstr /i /r "%BADPAT%" >nul
  if not errorlevel 1 (
    echo    BLOCKED: %%F
    set "BADFOUND=1"
  )
)
if not defined BADFOUND goto :paths_clean
set "G_PATHS=FAILED - a staged path matches the block list"
goto :secret_gate_failed

:paths_clean
set "G_PATHS=PASS"

REM  tools\NO_SECRETS.py adds a clinical-disclosure NOTE on top of the path
REM  check above. It BLOCKS on secrets and only WARNS on drug names, so its
REM  exit code is still honoured - a non-zero means a real secret.
REM  `python -B` so the check does not create __pycache__ and trip the
REM  .gitignore gate below.
if not exist "tools\NO_SECRETS.py" goto :nosecrets_missing
python -B "tools\NO_SECRETS.py" --files-from "%TEMP%\_health_staged.txt" "%REPO_DIR%"
if not errorlevel 1 goto :secrets_clean
set "G_SECRETS=FAILED - NO_SECRETS.py refused"
goto :secret_gate_failed

:secrets_clean
set "G_SECRETS=PASS"
goto :secret_gate_done

:nosecrets_missing
set "G_SECRETS=DID NOT RUN - tools\NO_SECRETS.py not found"
echo    ^^!^^! tools\NO_SECRETS.py NOT FOUND - the clinical-disclosure check did
echo       NOT run. Publishing anyway, and saying so rather than staying quiet.
goto :secret_gate_done

:gate_broken
set "G_SELFTEST=FAILED - %SELFT%"
echo.
echo ^^!^^! REFUSING - the secret gate itself is broken.
echo    Its own self-test failed, so it cannot be trusted to catch anything
echo    and a clean run would mean nothing.
echo       %SELFT%
echo    Fix BADPAT or the matching loop in this file. Do not publish until
echo    the self-test passes. NOTHING committed or pushed.
call :summary
pause
exit /b 1

:secret_gate_failed
echo.
echo ^^!^^! REFUSING - something staged looks like live data or a secret.
echo    THIS REPOSITORY IS PUBLIC. What was refused is named above - either a
echo    line marked BLOCKED, or NO_SECRETS.py's own report.
echo.
echo    A DATABASE is the diary itself - health3.db, fitlog.db. Never commit it.
echo    An ENV FILE holds bearer tokens - ingest.env. Never commit it.
echo            If one was ever committed, the token must also be ROTATED.
echo    A BACKUP is a copy of one of the above. Every spelling the patchers
echo            write is caught: .bak , .bak.STAMP , .bak_STAMP ,
echo            .bak-LABEL-STAMP , .bak-YYYY-MM-DD , .deployed-STAMP
echo    A .tmp is a patcher's half-written copy of the file it was patching.
echo.
echo    Fix .gitignore, run: git reset, then run this again.
echo    NOTHING committed or pushed.
call :summary
pause
exit /b 1

:secret_gate_done
del /q "%TEMP%\_health_staged.txt" 2>nul

REM ---- working-folder parity gate ------------------------------------------
REM  fitlog-ingest\ is the working folder; fitlog\ gutlog\ rxguard\ ops\ are
REM  authoritative. A file in the working folder and in exactly ONE app folder
REM  must be byte-identical, line endings included. This BLOCKS, because a
REM  stale copy is silent: fitlog\test_activity_feed.py imports health_ingest
REM  from its OWN folder, so it will happily pass against a module that is not
REM  the one on the server. That nearly happened on 13-Sep-2026.
echo Checking the working folder agrees with the app folders...
if not exist "tools\CHECK_FOLDER_PARITY.py" goto :parity_missing
python -B "tools\CHECK_FOLDER_PARITY.py" "%REPO_DIR%"
if not errorlevel 1 goto :parity_ok
set "G_PARITY=FAILED - a working copy has drifted"
goto :parity_failed

:parity_ok
set "G_PARITY=PASS"
goto :parity_done

:parity_missing
set "G_PARITY=DID NOT RUN - tools\CHECK_FOLDER_PARITY.py not found"
echo    ^^!^^! tools\CHECK_FOLDER_PARITY.py NOT FOUND - the parity check did NOT
echo       run. Publishing anyway, and saying so rather than staying quiet.
goto :parity_done

:parity_failed
echo.
echo    A working copy has drifted - see the list above. Copy the right file
echo    over the other, then run this again. NOTHING committed or pushed.
call :summary
pause
exit /b 1

:parity_done

REM ---- silent .gitignore drop gate -----------------------------------------
set DROPPED=
for /f "delims=" %%F in ('%GIT% ls-files --others --ignored --exclude-standard -- fitlog fitlog-ingest gutlog rxguard ops 2^>nul') do (
  echo    EXCLUDED BY .gitignore: %%F
  set DROPPED=1
)
if defined DROPPED goto :dropped_check

set "G_IGNORE=PASS - nothing excluded"
goto :dropped_done

:dropped_check
set "G_IGNORE=files were excluded - listed above, read them"
echo.
echo    Files above were excluded by .gitignore. Databases, env files and .bak
echo    snapshots SHOULD be excluded - that is correct and expected here.
echo    Read the list. If anything else is in it, stop and fix .gitignore.
echo.

:dropped_done

%GIT% diff --cached --quiet
if errorlevel 1 goto :do_commit
echo    nothing new to commit - will still verify the remote is current
set "G_COMMIT=nothing new to commit"
goto :commit_done

:do_commit
%GIT% commit -m "%MSG%"
if not errorlevel 1 goto :commit_ok
set "G_COMMIT=FAILED - git commit refused"
echo ^^!^^! git commit FAILED - a REAL failure. NOT published.
call :summary
pause
exit /b 1

:commit_ok
set "G_COMMIT=PASS - a new commit was created"
REM  Re-read HEAD here, not only after the push. If the push then fails, the
REM  summary must show the sha that exists locally and did NOT reach origin -
REM  showing the pre-commit sha beside "a new commit was created" is a
REM  summary that contradicts itself.
for /f %%H in ('%GIT% rev-parse HEAD 2^>nul') do set "LOCALH=%%H"

:commit_done

%GIT% push
if not errorlevel 1 goto :push_ok
set "G_PUSH=FAILED - git push refused"
echo ^^!^^! git push FAILED - NOT published
call :summary
pause
exit /b 1

:push_ok
set "G_PUSH=PASS - git push returned success"

set REMOTEH=
for /f %%H in ('%GIT% rev-parse HEAD') do set LOCALH=%%H
for /f %%H in ('%GIT% ls-remote origin -q HEAD') do if not defined REMOTEH set REMOTEH=%%H
if defined REMOTEH goto :remote_read
set "REMOTEH=could not be read"
set "G_VERIFY=FAILED - origin HEAD could not be read"
echo ^^!^^! could not read origin HEAD - UNVERIFIED
call :summary
pause
exit /b 1

:remote_read
if /i "%LOCALH%"=="%REMOTEH%" goto :verify_ok
set "G_VERIFY=FAILED - origin HEAD does not match local HEAD"
echo ^^!^^! VERIFY FAILED: local  %LOCALH%
echo                  origin %REMOTEH%
echo    NOT published.
call :summary
pause
exit /b 1

:verify_ok
set "G_VERIFY=PASS - origin HEAD matches local HEAD"
set "VERDICT=PUBLISHED AND VERIFIED - the push reached origin"
if "%G_COMMIT%"=="nothing new to commit" set "VERDICT=NOTHING NEW TO PUBLISH - origin was already current"
call :summary
pause
exit /b 0

REM ===========================================================================
REM  :summary - the last thing every path prints, with the pause right behind
REM  it, so the answer is on the screen when the window stops. Read it; do not
REM  infer it. Both shas are printed IN FULL so origin can be checked against
REM  GitHub by eye rather than taken on trust.
REM ===========================================================================
:summary
echo.
echo  ==========================================================
echo   PUBLISH_HEALTH - RESULT
echo  ==========================================================
echo   stale git lock sweep  : %G_LOCK%
echo   secret gate self-test : %G_SELFTEST%
echo   staged-path gate      : %G_PATHS%
echo   NO_SECRETS.py         : %G_SECRETS%
echo   working-folder parity : %G_PARITY%
echo   .gitignore drop check : %G_IGNORE%
echo   commit                : %G_COMMIT%
echo   push                  : %G_PUSH%
echo   origin verify         : %G_VERIFY%
echo  ----------------------------------------------------------
echo   local  HEAD : %LOCALH%
echo   origin HEAD : %REMOTEH%
echo  ----------------------------------------------------------
echo   %VERDICT%
echo  ==========================================================
echo.
goto :eof
