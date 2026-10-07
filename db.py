"""BusGo persistence layer for local SQLite and configured MySQL deployments."""
import sqlite3, hashlib, os, json, random, uuid, re, time
from datetime import datetime, date
import pandas as pd
from config import get_setting

DB = get_setting("BUSGO_SQLITE_PATH", os.path.join(os.path.dirname(__file__), "busgo.db"))
DB_ENGINE = get_setting("BUSGO_DB_ENGINE", "sqlite").strip().lower()
TAX, FEE = 0.05, 20.0
PROMOS = {"BUSGO10": 0.10, "WELCOME50": 0.05}


def conn():
    if DB_ENGINE == "sqlite":
        c = sqlite3.connect(DB, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    if DB_ENGINE != "mysql":
        raise ValueError("BUSGO_DB_ENGINE must be 'sqlite' or 'mysql'.")

    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    mysql_settings = {name: get_setting(name) for name in required}
    missing = [name for name, value in mysql_settings.items() if not value]
    if missing:
        raise RuntimeError(f"Missing MySQL configuration: {', '.join(missing)}")
    import pymysql

    return pymysql.connect(
        host=mysql_settings["MYSQL_HOST"],
        port=int(get_setting("MYSQL_PORT", "3306")),
        database=mysql_settings["MYSQL_DATABASE"],
        user=mysql_settings["MYSQL_USER"],
        password=mysql_settings["MYSQL_PASSWORD"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _sql(sql):
    if DB_ENGINE != "mysql":
        return sql
    sql = sql.replace("?", "%s")
    return re.sub(r"(?<!`)\brows\b(?!`)", "`rows`", sql, flags=re.IGNORECASE)


def q(sql, args=(), one=False, commit=False):
    c = conn()
    try:
        if DB_ENGINE == "mysql":
            with c.cursor() as cur:
                cur.execute(_sql(sql), args)
                result = cur.lastrowid if commit else cur.fetchall()
            if commit:
                c.commit()
                return result
        else:
            cur = c.execute(sql, args)
            if commit:
                result = cur.lastrowid
                c.commit()
                return result
            result = cur.fetchall()
        return (result[0] if result else None) if one else result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def df(sql, args=()):
    if DB_ENGINE == "mysql":
        return pd.DataFrame([dict(row) for row in q(sql, args)])
    with conn() as c:
        return pd.read_sql_query(sql, c, params=args)


def hash_pw(pw, salt=None):
    salt = salt or os.urandom(8).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 60000).hex()
    return f"{salt}${h}"


def check_pw(pw, stored):
    salt = stored.split("$")[0]
    return hash_pw(pw, salt) == stored


def init():
    if DB_ENGINE == "mysql":
        statements = [
            """CREATE TABLE IF NOT EXISTS users(id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL, email VARCHAR(320) NOT NULL UNIQUE, phone VARCHAR(32) NOT NULL,
                password VARCHAR(255) NOT NULL, role VARCHAR(32) NOT NULL DEFAULT 'user') ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS buses(id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                operator VARCHAR(255) NOT NULL, number VARCHAR(64) NOT NULL, bus_type VARCHAR(128) NOT NULL,
                ac TINYINT NOT NULL, seat_kind VARCHAR(32) NOT NULL, src VARCHAR(128) NOT NULL,
                dst VARCHAR(128) NOT NULL, dep VARCHAR(8) NOT NULL, arr VARCHAR(8) NOT NULL,
                price DECIMAL(10,2) NOT NULL, `rows` INT NOT NULL, rating DECIMAL(3,2) NOT NULL) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS routes(id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                src VARCHAR(128) NOT NULL, dst VARCHAR(128) NOT NULL, distance DECIMAL(8,2) NOT NULL) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS bookings(id VARCHAR(10) NOT NULL PRIMARY KEY,
                user_id INT NOT NULL, bus_id INT NOT NULL, journey_date VARCHAR(10) NOT NULL,
                passengers LONGTEXT NOT NULL, seats TEXT NOT NULL, base DECIMAL(10,2) NOT NULL,
                taxes DECIMAL(10,2) NOT NULL, fee DECIMAL(10,2) NOT NULL, discount DECIMAL(10,2) NOT NULL,
                total DECIMAL(10,2) NOT NULL, pay_method VARCHAR(64) NOT NULL, pay_status VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL, created VARCHAR(32) NOT NULL,
                INDEX idx_booking_bus_date (bus_id, journey_date, status),
                INDEX idx_booking_user_created (user_id, created)) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS payment_attempts(booking_id VARCHAR(10) NOT NULL PRIMARY KEY,
                provider_link_id VARCHAR(64) UNIQUE, provider_payment_id VARCHAR(64) UNIQUE,
                checkout_url VARCHAR(1024), expires_at BIGINT NOT NULL, paid_at VARCHAR(32), status VARCHAR(32) NOT NULL,
                created VARCHAR(32) NOT NULL, INDEX idx_payment_expiry (expires_at, status)) ENGINE=InnoDB""",
        ]
        with conn() as c:
            try:
                with c.cursor() as cur:
                    for statement in statements:
                        cur.execute(statement)
                c.commit()
            except Exception:
                c.rollback()
                raise
    else:
        with conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE,
                phone TEXT, password TEXT, role TEXT DEFAULT 'user');
            CREATE TABLE IF NOT EXISTS buses(id INTEGER PRIMARY KEY, operator TEXT, number TEXT,
                bus_type TEXT, ac INTEGER, seat_kind TEXT, src TEXT, dst TEXT, dep TEXT, arr TEXT,
                price REAL, rows INTEGER, rating REAL);
            CREATE TABLE IF NOT EXISTS routes(id INTEGER PRIMARY KEY, src TEXT, dst TEXT, distance REAL);
            CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY, user_id INTEGER, bus_id INTEGER,
                journey_date TEXT, passengers TEXT, seats TEXT, base REAL, taxes REAL, fee REAL,
                discount REAL, total REAL, pay_method TEXT, pay_status TEXT, status TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS payment_attempts(booking_id TEXT PRIMARY KEY, provider_link_id TEXT UNIQUE,
                provider_payment_id TEXT UNIQUE, checkout_url TEXT, expires_at INTEGER NOT NULL, paid_at TEXT,
                status TEXT NOT NULL, created TEXT NOT NULL);
            """)
    if DB_ENGINE == "sqlite" and not q("SELECT 1 FROM users LIMIT 1"):
        q("INSERT INTO users(name,email,phone,password,role) VALUES(?,?,?,?,?)",
          ("Admin", "admin@busgo.com", "9999999999", hash_pw("admin123"), "admin"), commit=True)
        q("INSERT INTO users(name,email,phone,password,role) VALUES(?,?,?,?,?)",
          ("Demo User", "user@busgo.com", "8888888888", hash_pw("user123"), "user"), commit=True)
    if DB_ENGINE == "mysql":
        admin_email = get_setting("BUSGO_ADMIN_EMAIL", "").strip().lower()
        admin_password = get_setting("BUSGO_ADMIN_PASSWORD", "")
        if admin_email and admin_password and not q("SELECT 1 FROM users WHERE email=?", (admin_email,), one=True):
            q("INSERT INTO users(name,email,phone,password,role) VALUES(?,?,?,?,?)",
              (get_setting("BUSGO_ADMIN_NAME", "Administrator"), admin_email,
               get_setting("BUSGO_ADMIN_PHONE", "0000000000"), hash_pw(admin_password), "admin"), commit=True)
    env_default = "production" if DB_ENGINE == "mysql" else "development"
    seed_demo = DB_ENGINE == "sqlite" or (
        DB_ENGINE == "mysql"
        and get_setting("BUSGO_SEED_DEMO_DATA") == "1"
        and get_setting("BUSGO_ENV", env_default).strip().lower() != "production"
    )
    if seed_demo and not q("SELECT 1 FROM routes LIMIT 1"):
        routes = [("Mumbai", "Pune", 150), ("Pune", "Mumbai", 150), ("Mumbai", "Nashik", 167),
                  ("Nashik", "Mumbai", 167), ("Pune", "Goa", 450), ("Goa", "Pune", 450)]
        for r in routes:
            q("INSERT INTO routes(src,dst,distance) VALUES(?,?,?)", r, commit=True)
        ops = [("RedLine Travels", "Volvo Multi-Axle", 1, "Seater", 6.0, 4.5, 450),
               ("Orange Tours", "Scania Sleeper", 1, "Sleeper", 6.5, 4.3, 700),
               ("Shivneri Express", "Semi-Luxury", 0, "Seater", 7.0, 4.0, 300),
               ("NightRider", "Mercedes Sleeper", 1, "Sleeper", 6.5, 4.6, 900),
               ("CityLink", "Ordinary", 0, "Seater", 8.0, 3.8, 220)]
        deps = ["06:00", "09:30", "14:00", "18:30", "22:30"]
        for src, dst, dist in routes:
            hrs_scale = dist / 150
            for i, (op, bt, ac, kind, hrs, rat, pr) in enumerate(ops):
                dh = int(hrs * hrs_scale); dm = int((hrs * hrs_scale - dh) * 60)
                h, m = map(int, deps[i].split(":"))
                tot = (h * 60 + m + dh * 60 + dm) % 1440
                q("""INSERT INTO buses(operator,number,bus_type,ac,seat_kind,src,dst,dep,arr,price,rows,rating)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (op, f"MH{12+i:02d}-{random.randint(1000,9999)}", bt, ac, kind, src, dst, deps[i],
                   f"{tot//60:02d}:{tot%60:02d}", round(pr * hrs_scale, -1), 10, rat), commit=True)
    if seed_demo and not q("SELECT 1 FROM buses WHERE src=? AND dst=? LIMIT 1", ("Goa", "Mumbai"), one=True):
        if not q("SELECT 1 FROM routes WHERE src=? AND dst=? LIMIT 1", ("Goa", "Mumbai"), one=True):
            q("INSERT INTO routes(src,dst,distance) VALUES(?,?,?)", ("Goa", "Mumbai", 590), commit=True)
        templates = q("SELECT * FROM buses WHERE src=? AND dst=? ORDER BY id LIMIT 5", ("Mumbai", "Pune"))
        departures = ["06:00", "08:30", "11:00", "18:00", "21:00"]
        durations = [690, 750, 810, 720, 840]
        for i, template in enumerate(templates):
            hour, minute = map(int, departures[i].split(":"))
            arrival = (hour * 60 + minute + durations[i]) % 1440
            q("""INSERT INTO buses(operator,number,bus_type,ac,seat_kind,src,dst,dep,arr,price,rows,rating)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (template["operator"], f"GA{12+i:02d}-{random.randint(1000,9999)}", template["bus_type"],
               template["ac"], template["seat_kind"], "Goa", "Mumbai", departures[i],
               f"{arrival//60:02d}:{arrival%60:02d}", round(float(template["price"]) * 590 / 150, -1),
               template["rows"], template["rating"]), commit=True)


# ---------- users ----------
def register(name, email, phone, pw):
    if q("SELECT 1 FROM users WHERE email=?", (email.lower(),)):
        return False, "Email already registered."
    q("INSERT INTO users(name,email,phone,password) VALUES(?,?,?,?)",
      (name, email.lower(), phone, hash_pw(pw)), commit=True)
    return True, "Account created. Please log in."


def login(email, pw):
    u = q("SELECT * FROM users WHERE email=?", (email.lower(),), one=True)
    return dict(u) if u and check_pw(pw, u["password"]) else None


def reset_password(email, phone, new_pw):
    u = q("SELECT * FROM users WHERE email=? AND phone=?", (email.lower(), phone), one=True)
    if not u:
        return False
    q("UPDATE users SET password=? WHERE id=?", (hash_pw(new_pw), u["id"]), commit=True)
    return True


def update_profile(uid, name, phone):
    q("UPDATE users SET name=?, phone=? WHERE id=?", (name, phone, uid), commit=True)


# ---------- buses & seats ----------
def cities():
    d = df("SELECT src FROM routes UNION SELECT dst FROM routes")
    return sorted(d["src"].tolist())


def duration_min(dep, arr):
    h1, m1 = map(int, dep.split(":")); h2, m2 = map(int, arr.split(":"))
    return (h2 * 60 + m2 - h1 * 60 - m1) % 1440


def fmt_dur(mins):
    return f"{mins//60}h {mins%60:02d}m"


def seat_labels(rows):
    return [f"{r}{c}" for r in range(1, rows + 1) for c in "ABCD"]


def reserved(rows):
    """Ladies-reserved: row 1 (A,B). Male-reserved: row 2 (C,D)."""
    return {"1A": "F", "1B": "F", "2C": "M", "2D": "M"}


def taken_seats(bus_id, jdate):
    bus = q("SELECT rows FROM buses WHERE id=?", (bus_id,), one=True)
    rnd = random.Random(f"{bus_id}-{jdate}")
    taken = {s for s in seat_labels(bus["rows"]) if rnd.random() < 0.25 and s not in reserved(0)}
    for r in q("""SELECT b.seats FROM bookings b LEFT JOIN payment_attempts p ON p.booking_id=b.id
               WHERE b.bus_id=? AND b.journey_date=? AND
               (b.status='Confirmed' OR (b.status='Pending' AND p.expires_at>?))""",
               (bus_id, str(jdate), int(time.time()))):
        taken |= set(json.loads(r["seats"]))
    return taken


def search_buses(src, dst, jdate):
    d = df("SELECT * FROM buses WHERE src=? AND dst=?", (src, dst))
    if d.empty:
        return d
    d["duration"] = d.apply(lambda r: duration_min(r.dep, r.arr), axis=1)
    d["available"] = d.apply(lambda r: r.rows * 4 - len(taken_seats(r.id, jdate)), axis=1)
    return d


def fare(price, n, promo=""):
    base = price * n
    taxes = round(base * TAX, 2)
    disc = round(base * PROMOS.get(promo.upper().strip(), 0), 2)
    return dict(base=base, taxes=taxes, fee=FEE, discount=disc, total=round(base + taxes + FEE - disc, 2))


# ---------- bookings ----------
def create_pending_booking(uid, bus_id, jdate, passengers, seats, f, method, expires_at):
    bid = "BG" + uuid.uuid4().hex[:8].upper()
    values = (bid, uid, bus_id, str(jdate), json.dumps(passengers), json.dumps(seats), f["base"], f["taxes"],
              f["fee"], f["discount"], f["total"], method, "Pending", "Pending", datetime.now().isoformat())
    c = conn()
    try:
        if DB_ENGINE == "mysql":
            with c.cursor() as cur:
                cur.execute("SELECT id FROM buses WHERE id=? FOR UPDATE", (bus_id,))
                if not cur.fetchone():
                    c.rollback()
                    return None
        else:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT id FROM buses WHERE id=?", (bus_id,)).fetchone():
                c.rollback()
                return None
        if set(seats) & taken_seats(bus_id, jdate):
            c.rollback()
            return None
        if DB_ENGINE == "mysql":
            with c.cursor() as cur:
                cur.execute(_sql("INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"), values)
                cur.execute(_sql("INSERT INTO payment_attempts(booking_id,expires_at,status,created) VALUES(?,?,?,?)"),
                            (bid, expires_at, "Pending", datetime.now().isoformat()))
        else:
            c.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            c.execute("INSERT INTO payment_attempts(booking_id,expires_at,status,created) VALUES(?,?,?,?)",
                      (bid, expires_at, "Pending", datetime.now().isoformat()))
        c.commit()
        return bid
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def save_payment_link(bid, link_id, checkout_url):
    q("UPDATE payment_attempts SET provider_link_id=?, checkout_url=? WHERE booking_id=? AND status='Pending'",
      (link_id, checkout_url, bid), commit=True)


def payment_attempt(bid):
    return q("SELECT * FROM payment_attempts WHERE booking_id=?", (bid,), one=True)


def complete_payment(bid, link_id, payment_id, method):
    c = conn()
    try:
        if DB_ENGINE == "mysql":
            with c.cursor() as cur:
                cur.execute(_sql("SELECT * FROM payment_attempts WHERE booking_id=? FOR UPDATE"), (bid,))
                attempt = cur.fetchone()
                cur.execute(_sql("SELECT status FROM bookings WHERE id=? FOR UPDATE"), (bid,))
                booking = cur.fetchone()
        else:
            c.execute("BEGIN IMMEDIATE")
            attempt = c.execute("SELECT * FROM payment_attempts WHERE booking_id=?", (bid,)).fetchone()
            booking = c.execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()
        if not attempt or not booking or attempt["provider_link_id"] != link_id:
            c.rollback()
            return False
        if booking["status"] == "Confirmed":
            c.rollback()
            return attempt["provider_payment_id"] == payment_id
        if booking["status"] != "Pending":
            c.rollback()
            return False
        if DB_ENGINE == "mysql":
            with c.cursor() as cur:
                cur.execute(_sql("UPDATE payment_attempts SET provider_payment_id=?,paid_at=?,status='Paid' WHERE booking_id=?"),
                            (payment_id, datetime.now().isoformat(), bid))
                cur.execute(_sql("UPDATE bookings SET pay_method=?,pay_status='Paid',status='Confirmed' WHERE id=?"),
                            (method, bid))
        else:
            c.execute("UPDATE payment_attempts SET provider_payment_id=?,paid_at=?,status='Paid' WHERE booking_id=?",
                      (payment_id, datetime.now().isoformat(), bid))
            c.execute("UPDATE bookings SET pay_method=?,pay_status='Paid',status='Confirmed' WHERE id=?",
                      (method, bid))
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def fail_payment(bid):
    q("UPDATE payment_attempts SET status='Failed' WHERE booking_id=? AND status='Pending'", (bid,), commit=True)
    q("UPDATE bookings SET status='Cancelled',pay_status='Failed' WHERE id=? AND status='Pending'", (bid,), commit=True)


def complete_demo_payment(bid, method):
    default_env = "development" if DB_ENGINE == "sqlite" else "production"
    if DB_ENGINE != "sqlite" or get_setting("BUSGO_ENV", default_env).strip().lower() == "production":
        return False
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        attempt = c.execute("SELECT * FROM payment_attempts WHERE booking_id=?", (bid,)).fetchone()
        booking = c.execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()
        if (not attempt or not booking or booking["status"] != "Pending"
                or attempt["expires_at"] <= int(time.time())):
            c.rollback()
            return False
        c.execute("UPDATE payment_attempts SET provider_payment_id=?,paid_at=?,status='Demo Paid' WHERE booking_id=?",
                  (f"DEMO-{bid}", datetime.now().isoformat(), bid))
        c.execute("UPDATE bookings SET pay_method=?,pay_status='Demo Paid',status='Confirmed' WHERE id=?",
                  (method, bid))
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def get_booking(bid):
    result = q("""SELECT b.*, s.operator, s.bus_type, s.number, s.src, s.dst, s.dep, s.arr,
                p.provider_payment_id,p.paid_at
                FROM bookings b JOIN buses s ON s.id=b.bus_id
                LEFT JOIN payment_attempts p ON p.booking_id=b.id WHERE b.id=?""", (bid,), one=True)
    return dict(result) if result else None


def bookings_of(uid=None):
    sql = """SELECT b.*, s.operator, s.src, s.dst, s.dep, s.arr, u.name AS user_name,
             p.checkout_url,p.expires_at
             FROM bookings b JOIN buses s ON s.id=b.bus_id JOIN users u ON u.id=b.user_id
             LEFT JOIN payment_attempts p ON p.booking_id=b.id"""
    rows = q(sql + (" WHERE b.user_id=?" if uid else "") + " ORDER BY b.created DESC", (uid,) if uid else ())
    return [dict(r) for r in rows]


def cancel_booking(bid):
        q("UPDATE bookings SET status='Cancelled',pay_status=CASE WHEN pay_status='Paid' THEN 'Refund required' ELSE 'Cancelled' END WHERE id=?",
            (bid,), commit=True)
        q("UPDATE payment_attempts SET status='Cancelled' WHERE booking_id=? AND status='Pending'", (bid,), commit=True)


def group(b):
    if b["status"] == "Cancelled":
        return "Cancelled"
    if b["status"] == "Pending":
        return "Pending" if b.get("expires_at", 0) > time.time() else "Cancelled"
    return "Upcoming" if b["journey_date"] >= str(date.today()) else "Completed"


# ---------- admin ----------
def stats():
    t = str(date.today())
    return dict(
        buses=q("SELECT COUNT(*) c FROM buses", one=True)["c"],
        users=q("SELECT COUNT(*) c FROM users", one=True)["c"],
        today=q("SELECT COUNT(*) c FROM bookings WHERE substr(created,1,10)=? AND status='Confirmed'", (t,), one=True)["c"],
        revenue=q("SELECT COALESCE(SUM(total),0) c FROM bookings WHERE status='Confirmed'", one=True)["c"],
        seats=sum(r["rows"] * 4 for r in q("SELECT rows FROM buses")),
    )


def save_bus(v, bid=None):
    cols = ["operator", "number", "bus_type", "ac", "seat_kind", "src", "dst", "dep", "arr", "price", "rows", "rating"]
    vals = [v[c] for c in cols]
    if bid:
        q(f"UPDATE buses SET {','.join(c+'=?' for c in cols)} WHERE id=?", (*vals, bid), commit=True)
    else:
        q(f"INSERT INTO buses({','.join(cols)}) VALUES({','.join('?'*len(cols))})", vals, commit=True)


def delete_bus(bid):
    q("DELETE FROM buses WHERE id=?", (bid,), commit=True)


def save_route(src, dst, dist):
    q("INSERT INTO routes(src,dst,distance) VALUES(?,?,?)", (src, dst, dist), commit=True)


def delete_route(rid):
    q("DELETE FROM routes WHERE id=?", (rid,), commit=True)
