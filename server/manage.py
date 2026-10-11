"""Admin CLI for the server host (break-glass):

    docker compose exec api python -m server.manage users
    docker compose exec api python -m server.manage reset-password <username>   # prints a new random password
    docker compose exec api python -m server.manage make-admin <username>       # also re-enables the account
"""
import os, secrets, sys, time

if os.getuid() == 0:            # don't leave root-owned DB/WAL files behind for the api process
    os.setgid(int(os.environ.get("PGID", "1000"))); os.setuid(int(os.environ.get("PUID", "1000")))

from . import db as DB  # noqa: E402
from .security import hash_password  # noqa: E402


def main(argv):
    if not argv or argv[0] not in ("users", "reset-password", "make-admin") or (argv[0] != "users" and len(argv) < 2):
        print(__doc__); return 2
    DB.init()
    c = DB.connect()
    if argv[0] == "users":
        for r in c.execute("SELECT username, role, disabled, last_login FROM users ORDER BY username"):
            print(f"{r['username']:<24} {r['role']:<11} {'disabled' if r['disabled'] else 'active':<9} "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_login'])) if r['last_login'] else 'never'}")
        return 0
    u = c.execute("SELECT id FROM users WHERE username=?", (argv[1],)).fetchone()
    if not u:
        print("no such user"); return 1
    with DB.tx(c):
        if argv[0] == "reset-password":
            pw = "-".join(secrets.token_hex(3) for _ in range(3))
            c.execute("UPDATE users SET pw_hash=?, updated_at=? WHERE id=?", (hash_password(pw), time.time(), u["id"]))
            c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
            print(f"new password for {argv[1]}: {pw}   (change it after signing in)")
        else:
            c.execute("UPDATE users SET role='admin', disabled=0, updated_at=? WHERE id=?", (time.time(), u["id"]))
            print(f"{argv[1]} is now an active admin")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
