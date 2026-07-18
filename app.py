from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import re
import logging
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

USE_PG = bool(os.environ.get("DATABASE_URL", "").startswith("postgres"))
if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trss")

app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
if not os.environ.get("SECRET_KEY") and not os.environ.get("RENDER"):
    logger.warning("SECRET_KEY not set — using random key")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 1800
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

if os.environ.get("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per minute"], storage_uri="memory://")

DB_URL = os.environ.get("DATABASE_URL", "")


def sanitize(value):
    if not isinstance(value, str):
        return value
    return re.sub(r'[<>"\'`;(){}]', '', value).strip()


def validate_int(value, default=0, minimum=0, maximum=999999999):
    try:
        v = int(float(value))
        return max(minimum, min(maximum, v))
    except (ValueError, TypeError):
        return default


def validate_float(value, default=0.0, minimum=0.0, maximum=999999999.0):
    try:
        v = float(value)
        return max(minimum, min(maximum, v))
    except (ValueError, TypeError):
        return default


@app.template_filter("number_format")
def number_format(value):
    try:
        v = int(value)
        if v >= 1_000_000_000:
            return f"{v / 1_000_000_000:.1f}B".rstrip('0').rstrip('.')
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M".rstrip('0').rstrip('.')
        return f"{v:,}"
    except (ValueError, TypeError):
        return value


def ph(sql):
    return sql.replace("?", "%s") if USE_PG else sql


def get_db():
    if USE_PG:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
        return conn
    else:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def db_execute(conn, sql, params=None):
    params = params or ()
    if USE_PG:
        cur = conn.cursor()
        cur.execute(ph(sql), params)
        return cur
    else:
        return conn.execute(sql, params)


def db_fetchone(conn, sql, params=None):
    params = params or ()
    if USE_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(ph(sql), params)
        return cur.fetchone()
    else:
        return conn.execute(sql, params).fetchone()


def db_fetchall(conn, sql, params=None):
    params = params or ()
    if USE_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(ph(sql), params)
        return cur.fetchall()
    else:
        return conn.execute(sql, params).fetchall()


def db_commit(conn):
    if USE_PG:
        conn.commit()
    else:
        conn.commit()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kingdoms (
                id SERIAL PRIMARY KEY,
                user_id INTEGER DEFAULT 0,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                kingdom_id INTEGER NOT NULL,
                customer_name TEXT NOT NULL,
                food INTEGER DEFAULT 0,
                wood INTEGER DEFAULT 0,
                stone INTEGER DEFAULT 0,
                gold INTEGER DEFAULT 0,
                price REAL DEFAULT 0,
                payment_type TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT '',
                FOREIGN KEY (kingdom_id) REFERENCES kingdoms(id)
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kingdoms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 0,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kingdom_id INTEGER NOT NULL,
                customer_name TEXT NOT NULL,
                food INTEGER DEFAULT 0,
                wood INTEGER DEFAULT 0,
                stone INTEGER DEFAULT 0,
                gold INTEGER DEFAULT 0,
                price REAL DEFAULT 0,
                payment_type TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT '',
                FOREIGN KEY (kingdom_id) REFERENCES kingdoms(id)
            )
        """)
        for col, default in [("kingdom_id", 0), ("status", "'active'")]:
            try:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {col} INTEGER DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        try:
            cur.execute("ALTER TABLE kingdoms ADD COLUMN user_id INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    db_commit(conn)
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password:
        admin_email = admin_email.strip().lower()
        existing = db_fetchone(conn, "SELECT id FROM users WHERE email=?", (admin_email,))
        if not existing:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            db_execute(conn, "INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                       (admin_email, generate_password_hash(admin_password), "Admin", now))
            db_commit(conn)
            logger.info("Admin account auto-created: %s", admin_email)
    conn.close()


def get_stats(conn, kingdom_id):
    all_rows = db_fetchone(conn, "SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=?", (kingdom_id,))
    fin_rows = db_fetchone(conn, "SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=? AND status='finished'", (kingdom_id,))
    act_rows = db_fetchone(conn, "SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=? AND status='active'", (kingdom_id,))

    def safe(row):
        if not row:
            return [0, 0, 0, 0, 0, 0]
        if USE_PG:
            vals = list(row.values())
        else:
            vals = [row[i] for i in range(6)]
        return [int(v or 0) for v in vals]

    return {"all": safe(all_rows), "finished": safe(fin_rows), "active": safe(act_rows)}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            logger.info("Unauthenticated access attempt to %s from %s", request.path, request.remote_addr)
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def own_kingdom(kingdom_id):
    conn = get_db()
    k = db_fetchone(conn, "SELECT id FROM kingdoms WHERE id=? AND user_id=?", (kingdom_id, session["user_id"]))
    conn.close()
    return k is not None


def validate_password(password):
    if len(password) < 8:
        return "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
    if not re.search(r'[A-Z]', password):
        return "كلمة المرور يجب أن تحتوي حرف كبير واحد على الأقل"
    if not re.search(r'[a-z]', password):
        return "كلمة المرور يجب أن تحتوي حرف صغير واحد على الأقل"
    if not re.search(r'[0-9]', password):
        return "كلمة المرور يجب أن تحتوي رقم واحد على الأقل"
    return None


@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="الصفحة غير موجودة"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="وصول مرفوض"), 403


@app.errorhandler(429)
def rate_limited(e):
    return render_template("error.html", code=429, message="طلبات كثيرة جداً، حاول مرة أخرى لاحقاً"), 429


@app.errorhandler(500)
def server_error(e):
    logger.error("Internal server error: %s", e)
    return render_template("error.html", code=500, message="خطأ داخلي في الخادوم"), 500


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, message="الملف أكبر من الحجم المسموح"), 413


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = sanitize(request.form.get("email", "")).lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("جميع الحقول مطلوبة", "error")
        elif len(email) > 254 or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash("البريد الإلكتروني غير صحيح", "error")
        else:
            conn = get_db()
            user = db_fetchone(conn, "SELECT * FROM users WHERE email=?", (email,))
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_email"] = user["email"]
                session["login_time"] = datetime.now().isoformat()
                logger.info("Successful login: %s from %s", email, request.remote_addr)
                return redirect(url_for("index"))
            logger.warning("Failed login attempt for %s from %s", email, request.remote_addr)
            flash("البريد الإلكتروني أو كلمة المرور غير صحيحة", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""))
        email = sanitize(request.form.get("email", "")).lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or not password:
            flash("جميع الحقول مطلوبة", "error")
        elif len(name) > 100:
            flash("الاسم طويل جداً", "error")
        elif len(email) > 254 or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash("البريد الإلكتروني غير صحيح", "error")
        else:
            pw_error = validate_password(password)
            if pw_error:
                flash(pw_error, "error")
            elif password != confirm:
                flash("كلمتا المرور غير متطابقتين", "error")
            else:
                conn = get_db()
                existing = db_fetchone(conn, "SELECT id FROM users WHERE email=?", (email,))
                if existing:
                    flash("البريد الإلكتروني مستخدم بالفعل", "error")
                    conn.close()
                else:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    db_execute(conn, "INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                               (email, generate_password_hash(password), name, now))
                    db_commit(conn)
                    user = db_fetchone(conn, "SELECT * FROM users WHERE email=?", (email,))
                    conn.close()
                    session.clear()
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["user_name"] = user["name"]
                    session["user_email"] = user["email"]
                    session["login_time"] = datetime.now().isoformat()
                    logger.info("New user registered: %s from %s", email, request.remote_addr)
                    return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    if uid:
        logger.info("User %s logged out", uid)
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    conn = get_db()
    uid = session["user_id"]
    kingdoms = db_fetchall(conn, "SELECT * FROM kingdoms WHERE user_id=? ORDER BY id DESC", (uid,))
    kingdom_stats = []
    kid_list = []
    for k in kingdoms:
        rows = db_fetchone(conn, "SELECT COUNT(*), SUM(price) FROM orders WHERE kingdom_id=?", (k["id"],))
        if USE_PG:
            count = rows["count"] or 0
            total_price = int(rows["sum"] or 0)
        else:
            count = rows[0] or 0
            total_price = int(rows[1] or 0)
        kingdom_stats.append({"id": k["id"], "name": sanitize(k["name"]), "count": count, "total_price": total_price, "created_at": k["created_at"]})
        kid_list.append(k["id"])

    today = datetime.now().strftime("%Y-%m-%d")
    if kid_list:
        placeholders = ",".join(["%s"] * len(kid_list)) if USE_PG else ",".join("?" * len(kid_list))
        like_pattern = today + "%"
        orders_today = db_fetchone(conn, f"SELECT COUNT(*) FROM orders WHERE kingdom_id IN ({placeholders}) AND created_at LIKE ?", kid_list + [like_pattern])
        total_revenue = db_fetchone(conn, f"SELECT COALESCE(SUM(price), 0) FROM orders WHERE kingdom_id IN ({placeholders})", kid_list)
        total_customers = db_fetchone(conn, f"SELECT COUNT(DISTINCT customer_name) FROM orders WHERE kingdom_id IN ({placeholders})", kid_list)
        pending_orders = db_fetchone(conn, f"SELECT COUNT(*) FROM orders WHERE kingdom_id IN ({placeholders}) AND status='active'", kid_list)
        total_orders = db_fetchone(conn, f"SELECT COUNT(*) FROM orders WHERE kingdom_id IN ({placeholders})", kid_list)

        def gv(row):
            if USE_PG:
                return list(row.values())[0] if row else 0
            return row[0] if row else 0

        orders_today = gv(orders_today) or 0
        total_revenue = int(gv(total_revenue) or 0)
        total_customers = gv(total_customers) or 0
        pending_orders = gv(pending_orders) or 0
        total_orders = gv(total_orders) or 0
    else:
        orders_today = total_revenue = total_customers = pending_orders = total_orders = 0

    conn.close()
    return render_template("kingdoms.html", kingdoms=kingdom_stats,
                           orders_today=orders_today, total_revenue=total_revenue,
                           total_customers=total_customers, pending_orders=pending_orders,
                           total_orders=total_orders)


@app.route("/kingdom/new", methods=["POST"])
@login_required
def new_kingdom():
    name = sanitize(request.form.get("name", ""))
    if not name or len(name) > 200:
        return redirect(url_for("index"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    db_execute(conn, "INSERT INTO kingdoms (user_id, name, created_at) VALUES (?, ?, ?)",
               (session["user_id"], name, now))
    db_commit(conn)
    conn.close()
    logger.info("Kingdom '%s' created by user %s", name, session["user_id"])
    return redirect(url_for("index"))


@app.route("/kingdom/<int:kingdom_id>")
@login_required
def kingdom_page(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    conn = get_db()
    kingdom = db_fetchone(conn, "SELECT * FROM kingdoms WHERE id=?", (kingdom_id,))
    if not kingdom:
        conn.close()
        abort(404)
    orders = db_fetchall(conn, "SELECT * FROM orders WHERE kingdom_id=? ORDER BY id ASC", (kingdom_id,))
    stats = get_stats(conn, kingdom_id)
    conn.close()
    safe_orders = []
    for o in orders:
        safe_orders.append({
            "id": o["id"], "kingdom_id": o["kingdom_id"],
            "customer_name": sanitize(o["customer_name"]),
            "food": o["food"], "wood": o["wood"], "stone": o["stone"], "gold": o["gold"],
            "price": o["price"],
            "payment_type": sanitize(o["payment_type"]),
            "notes": sanitize(o["notes"]),
            "status": o["status"], "created_at": o["created_at"]
        })
    return render_template("index.html", orders=safe_orders, stats=stats, kingdom={"id": kingdom["id"], "name": sanitize(kingdom["name"]), "created_at": kingdom["created_at"]})


@app.route("/kingdom/<int:kingdom_id>/add", methods=["POST"])
@login_required
def add_order(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    customer = sanitize(request.form.get("customer_name", ""))
    if not customer or len(customer) > 200:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))
    food = validate_int(request.form.get("food", 0))
    wood = validate_int(request.form.get("wood", 0))
    stone = validate_int(request.form.get("stone", 0))
    gold = validate_int(request.form.get("gold", 0))
    price = validate_float(request.form.get("price", 0))
    payment = sanitize(request.form.get("payment_type", ""))[:50]
    notes = sanitize(request.form.get("notes", ""))[:500]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    db_execute(conn,
        "INSERT INTO orders (kingdom_id, customer_name, food, wood, stone, gold, price, payment_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kingdom_id, customer, food, wood, stone, gold, price, payment, notes, now))
    db_commit(conn)
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/edit/<int:order_id>", methods=["POST"])
@login_required
def edit_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    customer = sanitize(request.form.get("customer_name", ""))
    if not customer or len(customer) > 200:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))
    food = validate_int(request.form.get("food", 0))
    wood = validate_int(request.form.get("wood", 0))
    stone = validate_int(request.form.get("stone", 0))
    gold = validate_int(request.form.get("gold", 0))
    price = validate_float(request.form.get("price", 0))
    payment = sanitize(request.form.get("payment_type", ""))[:50]
    notes = sanitize(request.form.get("notes", ""))[:500]
    conn = get_db()
    db_execute(conn,
        "UPDATE orders SET customer_name=?, food=?, wood=?, stone=?, gold=?, price=?, payment_type=?, notes=? WHERE id=? AND kingdom_id=?",
        (customer, food, wood, stone, gold, price, payment, notes, order_id, kingdom_id))
    db_commit(conn)
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/toggle/<int:order_id>", methods=["POST"])
@login_required
def toggle_status(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    conn = get_db()
    order = db_fetchone(conn, "SELECT status FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id))
    if order:
        new_status = "finished" if order["status"] == "active" else "active"
        db_execute(conn, "UPDATE orders SET status=? WHERE id=? AND kingdom_id=?", (new_status, order_id, kingdom_id))
        db_commit(conn)
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/copy/<int:order_id>", methods=["POST"])
@login_required
def copy_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    conn = get_db()
    order = db_fetchone(conn, "SELECT * FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id))
    if order:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        db_execute(conn,
            "INSERT INTO orders (kingdom_id, customer_name, food, wood, stone, gold, price, payment_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kingdom_id, order["customer_name"], order["food"], order["wood"], order["stone"], order["gold"], order["price"], order["payment_type"], order["notes"], now))
        db_commit(conn)
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete/<int:order_id>", methods=["POST"])
@login_required
def delete_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    conn = get_db()
    db_execute(conn, "DELETE FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id))
    db_commit(conn)
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete", methods=["POST"])
@login_required
def delete_kingdom(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    conn = get_db()
    db_execute(conn, "DELETE FROM orders WHERE kingdom_id=?", (kingdom_id,))
    db_execute(conn, "DELETE FROM kingdoms WHERE id=?", (kingdom_id,))
    db_commit(conn)
    conn.close()
    logger.info("Kingdom %s deleted by user %s", kingdom_id, session["user_id"])
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=8081)
else:
    init_db()
