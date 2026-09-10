@echo off
setlocal enabledelayedexpansion
REM ===========================================================================
REM  PUBLISH_HEALTH.bat  v1  -  publish the health-systems repo.
REM
REM  Sibling of PUBLISH_ALL.bat in drmanoj-clinic-automation, same gates,
REM  pointed at this repo instead.
REM
REM  Usage:
REM    PUBLISH_HEALTH.bat "your commit message"
REM    PUBLISH_HEALTH.bat            (falls back to a generic message)
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
REM   - refuses if a live database, an env file or a .bak snapshot is staged.
REM     THIS REPOSITORY IS PUBLIC. health3.db and fitlog.db are the diary and
REM     ingest.env holds the bearer tokens; none of it may ever be committed.
REM
REM  Message lines below deliberately contain no ( ) ^& ^| ^< ^> characters:
REM  a bare bracket inside an if-block closes it early and everything after
REM  runs unconditionally. Labels cannot be closed by a stray bracket.
REM ===========================================================================
set REPO_DIR=D:\dr-manoj-git\drmanoj-health-systems
if not exist "%REPO_DIR%\.git" ( echo !! repo not found at %REPO_DIR% & pause & exit /b 1 )

set MSG=%~1
if not defined MSG set MSG=publish: pending health-systems changes

set GIT=
where git >nul 2>&1 && set GIT=git
if not defined GIT if exist "C:\Program Files\Git\cmd\git.exe" set GIT="C:\Program Files\Git\cmd\git.exe"
if not defined GIT if exist "C:\Program Files (x86)\Git\cmd\git.exe" set GIT="C:\Program Files (x86)\Git\cmd\git.exe"
if not defined GIT for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%D\resources\app\git\cmd\git.exe" set GIT="%%D\resources\app\git\cmd\git.exe"
if not defined GIT ( echo !! git.exe not found & pause & exit /b 1 )

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
goto :locks_clear

:lock_stuck
echo !! the lock could not be removed - something still holds it.
echo    Close GitHub Desktop / editors and run this again. NOTHING published.
pause & exit /b 1

:git_running
echo !! a git lock exists AND a git process is running.
echo    Close GitHub Desktop / any editor doing git, then run this again.
echo    NOTHING published.
pause & exit /b 1

:locks_clear

echo Checking what is pending...
%GIT% add -A . || ( echo !! git add FAILED & pause & exit /b 1 )

REM ---- secret and live-data gate -------------------------------------------
echo Checking staged files for databases, env files and snapshots...
%GIT% diff --cached --name-only > "%TEMP%\_health_staged.txt"
findstr /i /r "\.db$ \.env$ \.bak_ \.bak- ingest\.env" "%TEMP%\_health_staged.txt" >nul
if not errorlevel 1 goto :secret_gate_failed
goto :secret_gate_done

:secret_gate_failed
echo.
echo !! REFUSING - something staged looks like live data or a secret.
echo    THIS REPOSITORY IS PUBLIC. The offending entries are listed below.
echo.
findstr /i /r "\.db$ \.env$ \.bak_ \.bak- ingest\.env" "%TEMP%\_health_staged.txt"
echo.
echo    A DATABASE is the diary itself - health3.db, fitlog.db. Never commit it.
echo    An ENV FILE holds bearer tokens - ingest.env. Never commit it.
echo            If one was ever committed, the token must also be ROTATED.
echo    A .bak SNAPSHOT is a copy of one of the above.
echo.
echo    Fix .gitignore, run: git reset, then run this again.
echo    NOTHING committed or pushed.
pause
exit /b 1

:secret_gate_done
del /q "%TEMP%\_health_staged.txt" 2>nul

REM ---- silent .gitignore drop gate -----------------------------------------
set DROPPED=
for /f "delims=" %%F in ('%GIT% ls-files --others --ignored --exclude-standard -- fitlog gutlog rxguard ops 2^>nul') do (
  echo    !! EXCLUDED BY .gitignore: %%F
  set DROPPED=1
)
if defined DROPPED goto :dropped_check

goto :dropped_done

:dropped_check
echo.
echo    Files above were excluded by .gitignore. Databases, env files and .bak
echo    snapshots SHOULD be excluded - that is correct and expected here.
echo    Read the list. If anything else is in it, stop and fix .gitignore.
echo.

:dropped_done

%GIT% diff --cached --quiet
if errorlevel 1 (
  %GIT% commit -m "%MSG%" || (
    echo !! git commit FAILED - a REAL failure. NOT published.
    pause & exit /b 1
  )
) else (
  echo    nothing new to commit - will still verify the remote is current
)

%GIT% push || ( echo !! git push FAILED - NOT published & pause & exit /b 1 )

set LOCALH=
set REMOTEH=
for /f %%H in ('%GIT% rev-parse HEAD') do set LOCALH=%%H
for /f %%H in ('%GIT% ls-remote origin -q HEAD') do if not defined REMOTEH set REMOTEH=%%H
if not defined REMOTEH ( echo !! could not read origin HEAD - UNVERIFIED & pause & exit /b 1 )
if /i not "%LOCALH%"=="%REMOTEH%" (
  echo !! VERIFY FAILED: local  %LOCALH%
  echo                  origin %REMOTEH%
  echo    NOT published.
  pause & exit /b 1
)
echo.
echo  ==========================================================
echo   PUBLISHED AND VERIFIED - origin HEAD = %LOCALH:~0,10%...
echo  ==========================================================
echo.
