from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "trss-orders-secret-key-change-in-prod-2025")
app.config["TEMPLATES_AUTO_RELOAD"] = True
DB_PATH = os.environ.get("DATABASE_URL", os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db"))


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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kingdoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
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
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN kingdom_id INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    admin_email = "ahmadalknk323@gmail.com"
    existing = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
    if not existing:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute("INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                     (admin_email, generate_password_hash("0567543987"), "Ahmad", now))
        conn.commit()
    conn.close()


def get_stats(conn, kingdom_id):
    all_rows = conn.execute("SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=?", (kingdom_id,)).fetchone()
    fin_rows = conn.execute("SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=? AND status='finished'", (kingdom_id,)).fetchone()
    act_rows = conn.execute("SELECT SUM(food), SUM(wood), SUM(stone), SUM(gold), SUM(price), COUNT(*) FROM orders WHERE kingdom_id=? AND status='active'", (kingdom_id,)).fetchone()

    def safe(row):
        if not row:
            return [0, 0, 0, 0, 0, 0]
        return [int(row[i] or 0) for i in range(6)]

    return {"all": safe(all_rows), "finished": safe(fin_rows), "active": safe(act_rows)}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            return redirect(url_for("index"))
        flash("البريد الإلكتروني أو كلمة المرور غير صحيحة", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or not password:
            flash("جميع الحقول مطلوبة", "error")
        elif len(password) < 6:
            flash("كلمة المرور يجب أن تكون 6 أحرف على الأقل", "error")
        elif password != confirm:
            flash("كلمتا المرور غير متطابقتين", "error")
        else:
            conn = get_db()
            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                flash("البريد الإلكتروني مستخدم بالفعل", "error")
                conn.close()
            else:
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                conn.execute("INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                             (email, generate_password_hash(password), name, now))
                conn.commit()
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                conn.close()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_email"] = user["email"]
                return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    conn = get_db()
    kingdoms = conn.execute("SELECT * FROM kingdoms ORDER BY id DESC").fetchall()
    kingdom_stats = []
    for k in kingdoms:
        rows = conn.execute("SELECT COUNT(*), SUM(price) FROM orders WHERE kingdom_id=?", (k["id"],)).fetchone()
        count = rows[0] or 0
        total_price = int(rows[1] or 0)
        kingdom_stats.append({"id": k["id"], "name": k["name"], "count": count, "total_price": total_price, "created_at": k["created_at"]})

    today = datetime.now().strftime("%Y-%m-%d")
    orders_today = conn.execute("SELECT COUNT(*) FROM orders WHERE created_at LIKE ?", (today + "%",)).fetchone()[0] or 0
    total_revenue = conn.execute("SELECT COALESCE(SUM(price), 0) FROM orders").fetchone()[0] or 0
    total_customers = conn.execute("SELECT COUNT(DISTINCT customer_name) FROM orders").fetchone()[0] or 0
    pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status='active'").fetchone()[0] or 0
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0

    conn.close()
    return render_template("kingdoms.html", kingdoms=kingdom_stats,
                           orders_today=orders_today, total_revenue=total_revenue,
                           total_customers=total_customers, pending_orders=pending_orders,
                           total_orders=total_orders)


@app.route("/kingdom/new", methods=["POST"])
@login_required
def new_kingdom():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("index"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute("INSERT INTO kingdoms (name, created_at) VALUES (?, ?)", (name, now))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/kingdom/<int:kingdom_id>")
@login_required
def kingdom_page(kingdom_id):
    conn = get_db()
    kingdom = conn.execute("SELECT * FROM kingdoms WHERE id=?", (kingdom_id,)).fetchone()
    if not kingdom:
        conn.close()
        return redirect(url_for("index"))
    orders = conn.execute("SELECT * FROM orders WHERE kingdom_id=? ORDER BY id ASC", (kingdom_id,)).fetchall()
    stats = get_stats(conn, kingdom_id)
    conn.close()
    return render_template("index.html", orders=orders, stats=stats, kingdom=kingdom)


@app.route("/kingdom/<int:kingdom_id>/add", methods=["POST"])
@login_required
def add_order(kingdom_id):
    customer = request.form.get("customer_name", "").strip()
    food = request.form.get("food", 0)
    wood = request.form.get("wood", 0)
    stone = request.form.get("stone", 0)
    gold = request.form.get("gold", 0)
    price = request.form.get("price", 0)
    payment = request.form.get("payment_type", "")
    notes = request.form.get("notes", "")

    if not customer:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))

    try:
        food = int(float(food)) if food else 0
        wood = int(float(wood)) if wood else 0
        stone = int(float(stone)) if stone else 0
        gold = int(float(gold)) if gold else 0
        price = float(price) if price else 0
    except ValueError:
        pass

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    conn.execute(
        "INSERT INTO orders (kingdom_id, customer_name, food, wood, stone, gold, price, payment_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kingdom_id, customer, food, wood, stone, gold, price, payment, notes, now),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/edit/<int:order_id>", methods=["POST"])
@login_required
def edit_order(kingdom_id, order_id):
    customer = request.form.get("customer_name", "").strip()
    food = request.form.get("food", 0)
    wood = request.form.get("wood", 0)
    stone = request.form.get("stone", 0)
    gold = request.form.get("gold", 0)
    price = request.form.get("price", 0)
    payment = request.form.get("payment_type", "")
    notes = request.form.get("notes", "")

    try:
        food = int(float(food)) if food else 0
        wood = int(float(wood)) if wood else 0
        stone = int(float(stone)) if stone else 0
        gold = int(float(gold)) if gold else 0
        price = float(price) if price else 0
    except ValueError:
        pass

    conn = get_db()
    conn.execute(
        "UPDATE orders SET customer_name=?, food=?, wood=?, stone=?, gold=?, price=?, payment_type=?, notes=? WHERE id=? AND kingdom_id=?",
        (customer, food, wood, stone, gold, price, payment, notes, order_id, kingdom_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/toggle/<int:order_id>", methods=["POST"])
@login_required
def toggle_status(kingdom_id, order_id):
    conn = get_db()
    order = conn.execute("SELECT status FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id)).fetchone()
    if order:
        new_status = "finished" if order["status"] == "active" else "active"
        conn.execute("UPDATE orders SET status=? WHERE id=? AND kingdom_id=?", (new_status, order_id, kingdom_id))
        conn.commit()
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/copy/<int:order_id>", methods=["POST"])
@login_required
def copy_order(kingdom_id, order_id):
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id)).fetchone()
    if order:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "INSERT INTO orders (kingdom_id, customer_name, food, wood, stone, gold, price, payment_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kingdom_id, order["customer_name"], order["food"], order["wood"], order["stone"], order["gold"], order["price"], order["payment_type"], order["notes"], now),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete/<int:order_id>", methods=["POST"])
@login_required
def delete_order(kingdom_id, order_id):
    conn = get_db()
    conn.execute("DELETE FROM orders WHERE id=? AND kingdom_id=?", (order_id, kingdom_id))
    conn.commit()
    conn.close()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete", methods=["POST"])
@login_required
def delete_kingdom(kingdom_id):
    conn = get_db()
    conn.execute("DELETE FROM orders WHERE kingdom_id=?", (kingdom_id,))
    conn.execute("DELETE FROM kingdoms WHERE id=?", (kingdom_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=8081)
else:
    init_db()
