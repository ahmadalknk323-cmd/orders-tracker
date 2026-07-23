from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import re
import logging
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

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

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL:
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    logger.info("Using PostgreSQL database")
else:
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    if os.environ.get("RENDER"):
        logger.error("DATABASE_URL not set on Render! Data will be lost on restart. Set DATABASE_URL to a PostgreSQL connection string.")
    else:
        logger.info("Using local SQLite database: %s", db_path)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 5,
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "max_overflow": 2,
}

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Text, unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text, default="")
    created_at = db.Column(db.Text, default="")
    kingdoms = db.relationship("Kingdom", backref="owner", lazy=True)


class Kingdom(db.Model):
    __tablename__ = "kingdoms"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), default=0)
    name = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, default="")
    orders = db.relationship("Order", backref="kingdom", lazy=True, cascade="all, delete-orphan")


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    kingdom_id = db.Column(db.Integer, db.ForeignKey("kingdoms.id"), nullable=False)
    customer_name = db.Column(db.Text, nullable=False)
    food = db.Column(db.Integer, default=0)
    wood = db.Column(db.Integer, default=0)
    stone = db.Column(db.Integer, default=0)
    gold = db.Column(db.Integer, default=0)
    price = db.Column(db.Float, default=0.0)
    payment_type = db.Column(db.Text, default="")
    notes = db.Column(db.Text, default="")
    status = db.Column(db.Text, default="active")
    created_at = db.Column(db.Text, default="")


db.Index("ix_orders_kingdom_id", Order.kingdom_id)
db.Index("ix_orders_status", Order.status)
db.Index("ix_orders_kingdom_status", Order.kingdom_id, Order.status)
db.Index("ix_kingdoms_user_id", Kingdom.user_id)


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


def migrate_sqlite_data():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")
    if not os.path.exists(db_path):
        return

    logger.info("SQLite database found. Migrating data to PostgreSQL...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, email, password_hash, name, created_at FROM users")
        users = cur.fetchall()
        uid_map = {}
        for u in users:
            existing = User.query.filter_by(email=u["email"]).first()
            if existing:
                uid_map[u["id"]] = existing.id
            else:
                new_user = User(
                    email=u["email"],
                    password_hash=u["password_hash"],
                    name=u["name"] or "",
                    created_at=u["created_at"] or "",
                )
                db.session.add(new_user)
                db.session.flush()
                uid_map[u["id"]] = new_user.id

        cur.execute("SELECT id, user_id, name, created_at FROM kingdoms")
        kingdoms = cur.fetchall()
        kid_map = {}
        for k in kingdoms:
            mapped_uid = uid_map.get(k["user_id"], 0)
            new_kingdom = Kingdom(
                user_id=mapped_uid,
                name=k["name"],
                created_at=k["created_at"] or "",
            )
            db.session.add(new_kingdom)
            db.session.flush()
            kid_map[k["id"]] = new_kingdom.id

        cur.execute("SELECT * FROM orders")
        orders = cur.fetchall()
        for o in orders:
            mapped_kid = kid_map.get(o["kingdom_id"])
            if mapped_kid is None:
                continue
            new_order = Order(
                kingdom_id=mapped_kid,
                customer_name=o["customer_name"] or "",
                food=o["food"] or 0,
                wood=o["wood"] or 0,
                stone=o["stone"] or 0,
                gold=o["gold"] or 0,
                price=o["price"] or 0.0,
                payment_type=o["payment_type"] or "",
                notes=o["notes"] or "",
                status=o["status"] or "active",
                created_at=o["created_at"] or "",
            )
            db.session.add(new_order)

        db.session.commit()
        logger.info("Migration complete: %d users, %d kingdoms, %d orders", len(users), len(kingdoms), len(orders))

        os.rename(db_path, db_path + ".migrated")
        logger.info("SQLite file renamed to orders.db.migrated")
    except Exception as e:
        db.session.rollback()
        logger.error("Migration failed: %s", e)
    finally:
        conn.close()


def init_app():
    with app.app_context():
        db.create_all()
        if DATABASE_URL:
            migrate_sqlite_data()
        admin_email = os.environ.get("ADMIN_EMAIL")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_email and admin_password:
            admin_email = admin_email.strip().lower()
            existing = User.query.filter_by(email=admin_email).first()
            if not existing:
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                admin = User(
                    email=admin_email,
                    password_hash=generate_password_hash(admin_password),
                    name="Admin",
                    created_at=now,
                )
                db.session.add(admin)
                db.session.commit()
                logger.info("Admin account auto-created: %s", admin_email)
        logger.info("Database initialized successfully")


def get_stats(kingdom_id):
    def safe(q):
        row = q.first()
        if not row:
            return [0, 0, 0, 0, 0, 0]
        if isinstance(row, tuple):
            return [int(v or 0) for v in row]
        return [int(v or 0) for v in row]

    all_q = db.session.query(
        db.func.sum(Order.food), db.func.sum(Order.wood),
        db.func.sum(Order.stone), db.func.sum(Order.gold),
        db.func.sum(Order.price), db.func.count(Order.id)
    ).filter(Order.kingdom_id == kingdom_id)

    fin_q = db.session.query(
        db.func.sum(Order.food), db.func.sum(Order.wood),
        db.func.sum(Order.stone), db.func.sum(Order.gold),
        db.func.sum(Order.price), db.func.count(Order.id)
    ).filter(Order.kingdom_id == kingdom_id, Order.status == "finished")

    act_q = db.session.query(
        db.func.sum(Order.food), db.func.sum(Order.wood),
        db.func.sum(Order.stone), db.func.sum(Order.gold),
        db.func.sum(Order.price), db.func.count(Order.id)
    ).filter(Order.kingdom_id == kingdom_id, Order.status == "active")

    return {"all": safe(all_q), "finished": safe(fin_q), "active": safe(act_q)}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            logger.info("Unauthenticated access attempt to %s from %s", request.path, request.remote_addr)
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def own_kingdom(kingdom_id):
    return Kingdom.query.filter_by(id=kingdom_id, user_id=session.get("user_id")).first() is not None


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
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                session.clear()
                session.permanent = True
                session["user_id"] = user.id
                session["user_name"] = user.name
                session["user_email"] = user.email
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
                existing = User.query.filter_by(email=email).first()
                if existing:
                    flash("البريد الإلكتروني مستخدم بالفعل", "error")
                else:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    user = User(
                        email=email,
                        password_hash=generate_password_hash(password),
                        name=name,
                        created_at=now,
                    )
                    db.session.add(user)
                    db.session.commit()
                    session.clear()
                    session.permanent = True
                    session["user_id"] = user.id
                    session["user_name"] = user.name
                    session["user_email"] = user.email
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
    uid = session["user_id"]
    kingdoms = Kingdom.query.filter_by(user_id=uid).order_by(Kingdom.id.desc()).all()
    kingdom_stats = []
    kid_list = []
    for k in kingdoms:
        count = Order.query.filter_by(kingdom_id=k.id).count()
        total_price = db.session.query(db.func.coalesce(db.func.sum(Order.price), 0)).filter(Order.kingdom_id == k.id).scalar()
        kingdom_stats.append({
            "id": k.id, "name": sanitize(k.name), "count": count,
            "total_price": int(total_price or 0), "created_at": k.created_at,
        })
        kid_list.append(k.id)

    today = datetime.now().strftime("%Y-%m-%d")
    if kid_list:
        base = Order.query.filter(Order.kingdom_id.in_(kid_list))
        orders_today = base.filter(Order.created_at.like(f"{today}%")).count()
        total_revenue = int(db.session.query(db.func.coalesce(db.func.sum(Order.price), 0)).filter(Order.kingdom_id.in_(kid_list)).scalar() or 0)
        total_customers = db.session.query(db.func.count(db.distinct(Order.customer_name))).filter(Order.kingdom_id.in_(kid_list)).scalar() or 0
        pending_orders = base.filter_by(status="active").count()
        total_orders = Order.query.filter(Order.kingdom_id.in_(kid_list)).count()
    else:
        orders_today = total_revenue = total_customers = pending_orders = total_orders = 0

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
    kingdom = Kingdom(user_id=session["user_id"], name=name, created_at=now)
    db.session.add(kingdom)
    db.session.commit()
    logger.info("Kingdom '%s' created by user %s", name, session["user_id"])
    return redirect(url_for("index"))


@app.route("/kingdom/<int:kingdom_id>")
@login_required
def kingdom_page(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    kingdom = Kingdom.query.get(kingdom_id)
    if not kingdom:
        abort(404)
    orders = Order.query.filter_by(kingdom_id=kingdom_id).order_by(Order.id.asc()).all()
    stats = get_stats(kingdom_id)
    safe_orders = []
    for o in orders:
        safe_orders.append({
            "id": o.id, "kingdom_id": o.kingdom_id,
            "customer_name": sanitize(o.customer_name),
            "food": o.food, "wood": o.wood, "stone": o.stone, "gold": o.gold,
            "price": o.price,
            "payment_type": sanitize(o.payment_type or ""),
            "notes": sanitize(o.notes or ""),
            "status": o.status, "created_at": o.created_at,
        })
    return render_template("index.html", orders=safe_orders, stats=stats,
                           kingdom={"id": kingdom.id, "name": sanitize(kingdom.name), "created_at": kingdom.created_at})


@app.route("/kingdom/<int:kingdom_id>/add", methods=["POST"])
@login_required
def add_order(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    customer = sanitize(request.form.get("customer_name", ""))
    if not customer or len(customer) > 200:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    order = Order(
        kingdom_id=kingdom_id,
        customer_name=customer,
        food=validate_int(request.form.get("food", 0)),
        wood=validate_int(request.form.get("wood", 0)),
        stone=validate_int(request.form.get("stone", 0)),
        gold=validate_int(request.form.get("gold", 0)),
        price=validate_float(request.form.get("price", 0)),
        payment_type=sanitize(request.form.get("payment_type", ""))[:50],
        notes=sanitize(request.form.get("notes", ""))[:500],
        created_at=now,
    )
    db.session.add(order)
    db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/edit/<int:order_id>", methods=["POST"])
@login_required
def edit_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    customer = sanitize(request.form.get("customer_name", ""))
    if not customer or len(customer) > 200:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))
    order = Order.query.filter_by(id=order_id, kingdom_id=kingdom_id).first()
    if not order:
        return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))
    order.customer_name = customer
    order.food = validate_int(request.form.get("food", 0))
    order.wood = validate_int(request.form.get("wood", 0))
    order.stone = validate_int(request.form.get("stone", 0))
    order.gold = validate_int(request.form.get("gold", 0))
    order.price = validate_float(request.form.get("price", 0))
    order.payment_type = sanitize(request.form.get("payment_type", ""))[:50]
    order.notes = sanitize(request.form.get("notes", ""))[:500]
    db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/toggle/<int:order_id>", methods=["POST"])
@login_required
def toggle_status(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    order = Order.query.filter_by(id=order_id, kingdom_id=kingdom_id).first()
    if order:
        order.status = "finished" if order.status != "finished" else "active"
        db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/progress/<int:order_id>", methods=["POST"])
@login_required
def set_in_progress(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    order = Order.query.filter_by(id=order_id, kingdom_id=kingdom_id).first()
    if order:
        order.status = "in_progress" if order.status != "in_progress" else "active"
        db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/copy/<int:order_id>", methods=["POST"])
@login_required
def copy_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    order = Order.query.filter_by(id=order_id, kingdom_id=kingdom_id).first()
    if order:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_order = Order(
            kingdom_id=kingdom_id,
            customer_name=order.customer_name,
            food=order.food, wood=order.wood, stone=order.stone, gold=order.gold,
            price=order.price, payment_type=order.payment_type, notes=order.notes,
            created_at=now,
        )
        db.session.add(new_order)
        db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete/<int:order_id>", methods=["POST"])
@login_required
def delete_order(kingdom_id, order_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    order = Order.query.filter_by(id=order_id, kingdom_id=kingdom_id).first()
    if order:
        db.session.delete(order)
        db.session.commit()
    return redirect(url_for("kingdom_page", kingdom_id=kingdom_id))


@app.route("/kingdom/<int:kingdom_id>/delete", methods=["POST"])
@login_required
def delete_kingdom(kingdom_id):
    if not own_kingdom(kingdom_id):
        abort(403)
    kingdom = Kingdom.query.get(kingdom_id)
    if kingdom:
        db.session.delete(kingdom)
        db.session.commit()
    logger.info("Kingdom %s deleted by user %s", kingdom_id, session["user_id"])
    return redirect(url_for("index"))


init_app()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8081)
