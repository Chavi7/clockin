# CLOCKIN — classroom runbook

One page for running this in a classroom. Everything here is about *operating*
the app, not changing it.

## What it is

Students scan a QR badge (or type their Employee ID) at a kiosk to clock in and
out of class, as if reporting to work. Teachers see who's on shift and export
attendance to CSV. The database is one SQLite file inside the container.

## First deploy

**Step 0 — create the shared network, once, before either app.** Both stacks
expect an external Docker network called `ctec-net`, so a stack deployed
without it fails with "network ctec-net declared as external, but could not be
found". In Portainer: **Networks → Add network**, name `ctec-net`, driver
`bridge`. From a shell: `docker network create ctec-net`.

1. In Portainer: **Stacks → Add stack**, build method **Repository**,
   `https://github.com/Chavi7/clockin`, ref `refs/heads/main`, compose
   `compose.yml`.
2. Under **Environment variables**, set at minimum:

   | name | value |
   |---|---|
   | `CLOCKIN_SECRET` | a long random string — `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `CLOCKIN_BIND` | `0.0.0.0` so classroom PCs can reach the kiosk |
   | `TZ` | your timezone, e.g. `America/New_York` — otherwise the container runs in UTC and every recorded time is hours off |

   The app **refuses to start** if `CLOCKIN_SECRET` is empty or left as the
   placeholder. That is deliberate — a blank secret used to leave a container
   that looked healthy but returned 500 on every login.
3. Deploy, then open `/login` — with a fresh database you land on `/setup` and
   create the first admin account there.

## Check the deployment actually works

```bash
./scripts/smoke.sh http://<lab-server-ip>:5000
```

Read-only, takes a second, exits 0 if healthy. It checks the kiosk renders, the
login and setup pages don't 500, auth is enforced, and the scan API rejects junk
— which is exactly the set of things a blank secret used to break silently. Run
it right after deploying, and again if a period starts weird.

## The URL students use

`http://<lab-server-ip>:5000/` with `CLOCKIN_BIND=0.0.0.0`.

If you leave `CLOCKIN_BIND` at its default (`127.0.0.1`), the app is only
reachable from the server itself and the kiosk will not load from a classroom
PC. That default is the safe posture if you put a reverse proxy in front
instead.

**Camera scanning needs HTTPS.** Browsers only grant camera access on
`https://` or `localhost`. Over plain HTTP on the LAN, use the USB scanner or
typed IDs; for camera mode you need a reverse proxy with TLS.

## Daily use

- Kiosk is public — no login. Scan badge → green **CLOCKED IN**, scan again →
  blue **CLOCKED OUT** with duration. It resets 5 seconds after each scan.
- Teacher: `/login` → **TODAY** for who's in by period → **EXPORT CSV** for
  attendance entry.
- New roster: **ROSTER → EXAMPLE TEMPLATE**, edit in Excel, upload.
- Re-uploading an updated roster is safe: rows are matched by `student_id`
  (or by name + course when there's no student ID) and updated rather than
  duplicated. If a row matches more than one existing student, it is skipped
  and the page says so — add an `employee_id` column to disambiguate.

## Backup (do this before anything risky)

```bash
# from the lab server, with the container running
docker cp clockin:/app/data/clockin.db ./clockin-$(date +%F).db
```

The file is under 1 MB even after a year. Keep a copy off the server. This is
the entire app state — roster, shifts, role history, accounts.

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| Kiosk loads, login returns 500 | `CLOCKIN_SECRET` empty or placeholder | Set it to a long random value and redeploy. The container now exits at boot instead, with the reason in the logs. |
| Connection refused from a classroom PC | bound to `127.0.0.1` | Set `CLOCKIN_BIND=0.0.0.0`, or reach it through the proxy |
| Camera won't start | not a secure context | HTTPS, or use the USB scanner / typed IDs |
| Container restarts in a loop | almost always the secret | `docker compose logs` shows the exact message |
| Student scans but "not found" | not on the roster, or deactivated | ROSTER page — check the student is active and the Employee ID matches the badge |
| Badge scans as garbage | QR was printed from a different app or damaged | Re-print from **BADGES** |
| Teacher locked out | no email reset exists | An admin resets the password from **TEACHERS**; the temporary password appears once |

## End of the semester

Archive first, then reset:

```bash
docker cp clockin:/app/data/clockin.db ./clockin-archive-$(date +%F).db
```

Only then, if you truly want an empty system, `docker compose down -v` removes
the volume and the database with it. **There is no undo for that command** —
the archive you just made is the recovery path.

## Running the tests (only if you're changing code)

```bash
pip install -r requirements-dev.txt
python -m pytest
```

CI runs the same suite plus `pyflakes` on every pull request.
