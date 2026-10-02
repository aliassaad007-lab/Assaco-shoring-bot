import os
import sqlite3
import calendar
from datetime import date, datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DB = os.getenv("DATABASE_PATH", "assaco.db")
ADMINS = {
    int(x.strip())
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
}

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS clients(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    client_id INTEGER,
    location TEXT,
    status TEXT DEFAULT 'Active',
    installation_date TEXT,
    dismantling_date TEXT,
    quantity REAL DEFAULT 0,
    quantity_unit TEXT DEFAULT 'm3',
    first_rate REAL DEFAULT 0,
    extra_rate REAL DEFAULT 0,
    notes TEXT,
    FOREIGN KEY(client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS rentals(
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    period_no INTEGER NOT NULL,
    period_start TEXT,
    period_end TEXT,
    due_date TEXT,
    amount REAL DEFAULT 0,
    paid REAL DEFAULT 0,
    UNIQUE(project_id,period_no),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS receipts(
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    rental_id INTEGER,
    amount REAL NOT NULL,
    received_on TEXT NOT NULL,
    note TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(rental_id) REFERENCES rentals(id)
);

CREATE TABLE IF NOT EXISTS expenses(
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    category TEXT,
    amount REAL NOT NULL,
    paid_on TEXT NOT NULL,
    money_source TEXT CHECK(
        money_source IN ('Project Cash','Own Pocket','Other Project')
    ),
    source_project_id INTEGER,
    note TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS inventory(
    id INTEGER PRIMARY KEY,
    item TEXT UNIQUE NOT NULL,
    initial_qty REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock_moves(
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    item_id INTEGER NOT NULL,
    direction TEXT CHECK(direction IN ('OUT','IN')),
    qty REAL NOT NULL,
    moved_on TEXT NOT NULL,
    note TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(item_id) REFERENCES inventory(id)
);

CREATE TABLE IF NOT EXISTS variations(
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    description TEXT,
    quantity REAL DEFAULT 0,
    unit TEXT,
    amount REAL DEFAULT 0,
    created_on TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
"""


# ---------------- DATABASE ----------------

def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with con() as c:
        c.executescript(SCHEMA)

        items = [
            "High Props",
            "Regular Props",
            "Doka Props",
            "2in Tubes",
            "H20 Beams",
            "1m Braces",
            "2m Braces",
            "U-Heads",
            "Joints",
            "Bolts",
        ]

        for item in items:
            c.execute(
                "INSERT OR IGNORE INTO inventory(item) VALUES(?)",
                (item,),
            )


def add_month(d, n=1):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(
        y,
        m,
        min(d.day, calendar.monthrange(y, m)[1]),
    )


def ensure_rental(project_id, period_no):
    with con() as c:
        p = c.execute(
            "SELECT * FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()

        if not p or not p["installation_date"]:
            return

        installation = date.fromisoformat(p["installation_date"])

        start = add_month(installation, period_no - 1)
        end = add_month(start, 1)

        rate = (
            p["first_rate"]
            if period_no == 1
            else p["extra_rate"]
        )

        amount = (p["quantity"] or 0) * (rate or 0)

        # ASSACO rule:
        # First month is due immediately when installation is completed.
        # Every additional month becomes due at the start of that month.
        due = (
            p["installation_date"]
            if period_no == 1
            else start.isoformat()
        )

        c.execute(
            """
            INSERT OR IGNORE INTO rentals(
                project_id,
                period_no,
                period_start,
                period_end,
                due_date,
                amount
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                project_id,
                period_no,
                start.isoformat(),
                end.isoformat(),
                due,
                amount,
            ),
        )


# ---------------- SECURITY ----------------

def auth(update):
    return (
        not ADMINS
        or update.effective_user.id in ADMINS
    )


# ---------------- KEYBOARDS ----------------

def main_kb():
    rows = [
        [
            ("🏗 Doka Projects", "projects"),
            ("💰 Receivables", "receivables"),
        ],
        [
            ("💸 Expenses", "expenses"),
            ("📦 Inventory", "inventory"),
        ],
        [
            ("📊 P&L", "pnl"),
            ("🔔 Due Dates", "dues"),
        ],
        [
            ("➕ New Project", "new_project"),
            ("📈 Dashboard", "dashboard"),
        ],
    ]

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text,
                    callback_data=data,
                )
                for text, data in row
            ]
            for row in rows
        ]
    )


def back_kb():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "⬅️ Menu",
                callback_data="home",
            )
        ]]
    )


# ---------------- START / MENU ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return await update.message.reply_text(
            "Unauthorized account."
        )

    await update.message.reply_text(
        "🏗 ASSACO Doka Management\n\nChoose an action:",
        reply_markup=main_kb(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update):
        return

    await update.message.reply_text(
        "🏗 ASSACO Doka Management",
        reply_markup=main_kb(),
    )


# ---------------- NEW PROJECT WIZARD ----------------

(
    PROJECT_CODE,
    CLIENT,
    LOCATION,
    INSTALL_DATE,
    QUANTITY,
    FIRST_RATE,
    EXTRA_RATE,
    CONFIRM_PROJECT,
) = range(8)


async def begin_new_project(update, context):
    q = update.callback_query
    await q.answer()

    if not auth(update):
        return ConversationHandler.END

    context.user_data["new_project"] = {}

    await q.edit_message_text(
        "➕ NEW DOKA PROJECT\n\n"
        "🏗 What is the Project / Lot number?\n\n"
        "Example: 4543\n\n"
        "Send /cancel anytime to stop."
    )

    return PROJECT_CODE


async def get_project_code(update, context):
    code = update.message.text.strip()

    if not code:
        await update.message.reply_text(
            "Please enter the Project / Lot number."
        )
        return PROJECT_CODE

    with con() as c:
        exists = c.execute(
            "SELECT id FROM projects WHERE code=?",
            (code,),
        ).fetchone()

    if exists:
        await update.message.reply_text(
            f"⚠️ Project {code} already exists.\n\n"
            "Please enter another Project / Lot number."
        )
        return PROJECT_CODE

    context.user_data["new_project"]["code"] = code

    await update.message.reply_text(
        "👤 What is the CLIENT name?\n\n"
        "Example: Arsh Consultancy"
    )

    return CLIENT


async def get_client(update, context):
    client = update.message.text.strip()

    if not client:
        await update.message.reply_text(
            "Please enter the client name."
        )
        return CLIENT

    context.user_data["new_project"]["client"] = client

    await update.message.reply_text(
        "📍 What is the project LOCATION?\n\n"
        "Example: Hadath"
    )

    return LOCATION


async def get_location(update, context):
    location = update.message.text.strip()

    if not location:
        await update.message.reply_text(
            "Please enter the project location."
        )
        return LOCATION

    context.user_data["new_project"]["location"] = location

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "📅 Today",
                callback_data="project_date_today",
            )
        ]]
    )

    await update.message.reply_text(
        "📅 What is the INSTALLATION DATE?\n\n"
        "Write it as:\n"
        "YYYY-MM-DD\n\n"
        "Example: 2026-10-02\n\n"
        "Or press Today:",
        reply_markup=keyboard,
    )

    return INSTALL_DATE


async def get_install_date(update, context):
    value = update.message.text.strip()

    try:
        d = date.fromisoformat(value)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid date.\n\n"
            "Please use YYYY-MM-DD\n"
            "Example: 2026-10-02"
        )
        return INSTALL_DATE

    context.user_data["new_project"]["installation_date"] = (
        d.isoformat()
    )

    await update.message.reply_text(
        "📦 What is the SHORING QUANTITY in m³?\n\n"
        "Example: 2000"
    )

    return QUANTITY


async def today_install_date(update, context):
    q = update.callback_query
    await q.answer()

    context.user_data["new_project"]["installation_date"] = (
        date.today().isoformat()
    )

    await q.edit_message_text(
        f"📅 Installation date: {date.today().isoformat()}"
    )

    await q.message.reply_text(
        "📦 What is the SHORING QUANTITY in m³?\n\n"
        "Example: 2000"
    )

    return QUANTITY


async def get_quantity(update, context):
    try:
        qty = float(
            update.message.text.strip().replace(",", "")
        )

        if qty <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Enter a valid quantity.\n"
            "Example: 2000"
        )
        return QUANTITY

    context.user_data["new_project"]["quantity"] = qty

    await update.message.reply_text(
        "💵 What is the FIRST MONTH rental rate?\n\n"
        "$ / m³\n\n"
        "Example: 3"
    )

    return FIRST_RATE


async def get_first_rate(update, context):
    try:
        rate = float(
            update.message.text.strip().replace("$", "")
        )

        if rate < 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Enter a valid rate.\n"
            "Example: 3"
        )
        return FIRST_RATE

    context.user_data["new_project"]["first_rate"] = rate

    await update.message.reply_text(
        "🔄 What is the ADDITIONAL MONTH rental rate?\n\n"
        "$ / m³ / month\n\n"
        "Example: 2"
    )

    return EXTRA_RATE


async def get_extra_rate(update, context):
    try:
        rate = float(
            update.message.text.strip().replace("$", "")
        )

        if rate < 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Enter a valid rate.\n"
            "Example: 2"
        )
        return EXTRA_RATE

    context.user_data["new_project"]["extra_rate"] = rate

    p = context.user_data["new_project"]

    first_amount = (
        p["quantity"] * p["first_rate"]
    )

    extra_amount = (
        p["quantity"] * p["extra_rate"]
    )

    installation = date.fromisoformat(
        p["installation_date"]
    )

    next_due = add_month(
        installation,
        1,
    ).isoformat()

    text = (
        "📋 PROJECT SUMMARY\n\n"
        f"🏗 Lot / Project: {p['code']}\n"
        f"👤 Client: {p['client']}\n"
        f"📍 Location: {p['location']}\n"
        f"📅 Installation: {p['installation_date']}\n"
        f"📦 Quantity: {p['quantity']:,.2f} m³\n\n"
        f"💵 First month rate: ${p['first_rate']:,.2f}/m³\n"
        f"💰 First month amount: ${first_amount:,.2f}\n\n"
        f"🔄 Extra month rate: ${p['extra_rate']:,.2f}/m³/month\n"
        f"💰 Extra month amount: ${extra_amount:,.2f}/month\n\n"
        f"🔔 Next additional rental due: {next_due}\n\n"
        "Save this project?"
    )

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ SAVE PROJECT",
                callback_data="save_new_project",
            ),
            InlineKeyboardButton(
                "❌ CANCEL",
                callback_data="cancel_new_project",
            ),
        ]]
    )

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )

    return CONFIRM_PROJECT


async def save_new_project(update, context):
    q = update.callback_query
    await q.answer()

    p = context.user_data.get("new_project")

    if not p:
        await q.edit_message_text(
            "Project information expired. Please start again."
        )
        return ConversationHandler.END

    try:
        with con() as c:

            client_row = c.execute(
                "SELECT id FROM clients "
                "WHERE lower(name)=lower(?)",
                (p["client"],),
            ).fetchone()

            if client_row:
                cid = client_row["id"]

            else:
                cur = c.execute(
                    "INSERT INTO clients(name) VALUES(?)",
                    (p["client"],),
                )
                cid = cur.lastrowid

            cur = c.execute(
                """
                INSERT INTO projects(
                    code,
                    client_id,
                    location,
                    installation_date,
                    quantity,
                    first_rate,
                    extra_rate
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    p["code"],
                    cid,
                    p["location"],
                    p["installation_date"],
                    p["quantity"],
                    p["first_rate"],
                    p["extra_rate"],
                ),
            )

            project_id = cur.lastrowid

        # Generate first month + next additional month.
        ensure_rental(project_id, 1)
        ensure_rental(project_id, 2)

        first_amount = (
            p["quantity"] * p["first_rate"]
        )

        extra_amount = (
            p["quantity"] * p["extra_rate"]
        )

        next_due = add_month(
            date.fromisoformat(p["installation_date"]),
            1,
        ).isoformat()

        context.user_data.pop("new_project", None)

        await q.edit_message_text(
            "✅ PROJECT SAVED\n\n"
            f"🏗 {p['code']} — {p['client']}\n"
            f"📍 {p['location']}\n"
            f"📦 {p['quantity']:,.2f} m³\n\n"
            f"💰 First month: ${first_amount:,.2f}\n"
            f"🔄 Additional rental: ${extra_amount:,.2f}/month\n"
            f"🔔 Next due: {next_due}",
            reply_markup=back_kb(),
        )

    except sqlite3.IntegrityError:
        await q.edit_message_text(
            "⚠️ This project code already exists.",
            reply_markup=back_kb(),
        )

    return ConversationHandler.END


async def cancel_new_project_button(update, context):
    q = update.callback_query
    await q.answer()

    context.user_data.pop("new_project", None)

    await q.edit_message_text(
        "❌ New project cancelled.",
        reply_markup=back_kb(),
    )

    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.pop("new_project", None)

    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=main_kb(),
    )

    return ConversationHandler.END


# ---------------- MAIN BUTTON CALLBACKS ----------------

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    if not auth(update):
        return await q.edit_message_text(
            "Unauthorized account."
        )

    key = q.data

    if key == "home":
        await q.edit_message_text(
            "🏗 ASSACO Doka Management",
            reply_markup=main_kb(),
        )
        return

    with con() as c:

        if key == "projects":

            ps = c.execute(
                """
                SELECT p.*, cl.name client
                FROM projects p
                LEFT JOIN clients cl
                    ON cl.id=p.client_id
                ORDER BY p.id DESC
                LIMIT 30
                """
            ).fetchall()

            body = (
                "\n".join(
                    f"• {p['code']} — "
                    f"{p['client'] or '-'} — "
                    f"{p['status']}"
                    for p in ps
                )
                or "No projects yet."
            )

            txt = (
                "🏗 Doka Projects\n\n"
                + body
            )

        elif key == "receivables":

            rows = c.execute(
                """
                SELECT
                    p.code,
                    SUM(r.amount-r.paid) bal
                FROM rentals r
                JOIN projects p
                    ON p.id=r.project_id
                GROUP BY p.id
                HAVING bal>0
                ORDER BY bal DESC
                """
            ).fetchall()

            body = (
                "\n".join(
                    f"• {x['code']}: "
                    f"${x['bal']:,.2f}"
                    for x in rows
                )
                or "Nothing outstanding."
            )

            txt = (
                "💰 Outstanding Receivables\n\n"
                + body
            )

        elif key == "dues":

            rows = c.execute(
                """
                SELECT
                    p.code,
                    r.period_no,
                    r.due_date,
                    (r.amount-r.paid) bal
                FROM rentals r
                JOIN projects p
                    ON p.id=r.project_id
                WHERE r.amount>r.paid
                ORDER BY r.due_date
                LIMIT 30
                """
            ).fetchall()

            body = (
                "\n".join(
                    f"• {x['code']} M{x['period_no']} — "
                    f"{x['due_date']} — "
                    f"${x['bal']:,.2f}"
                    for x in rows
                )
                or "No unpaid rental periods."
            )

            txt = (
                "🔔 Rental Due Dates\n\n"
                + body
            )

        elif key == "inventory":

            rows = c.execute(
                """
                SELECT
                    i.item,
                    i.initial_qty +
                    COALESCE(
                        SUM(
                            CASE
                                WHEN s.direction='IN'
                                THEN s.qty
                                ELSE -s.qty
                            END
                        ),
                        0
                    ) avail
                FROM inventory i
                LEFT JOIN stock_moves s
                    ON s.item_id=i.id
                GROUP BY i.id
                ORDER BY i.item
                """
            ).fetchall()

            txt = (
                "📦 Available Stock\n\n"
                + "\n".join(
                    f"• {x['item']}: {x['avail']:g}"
                    for x in rows
                )
            )

        elif key == "pnl":

            rows = c.execute(
                """
                SELECT
                    p.code,

                    COALESCE(
                        (
                            SELECT SUM(amount)
                            FROM rentals
                            WHERE project_id=p.id
                        ),
                        0
                    )
                    +
                    COALESCE(
                        (
                            SELECT SUM(amount)
                            FROM variations
                            WHERE project_id=p.id
                        ),
                        0
                    ) revenue,

                    COALESCE(
                        (
                            SELECT SUM(amount)
                            FROM expenses
                            WHERE project_id=p.id
                        ),
                        0
                    ) cost

                FROM projects p
                ORDER BY p.id DESC
                """
            ).fetchall()

            body = (
                "\n".join(
                    f"• {x['code']}: "
                    f"Rev ${x['revenue']:,.0f} | "
                    f"Cost ${x['cost']:,.0f} | "
                    f"P&L ${x['revenue']-x['cost']:,.0f}"
                    for x in rows
                )
                or "No projects yet."
            )

            txt = (
                "📊 Project P&L\n\n"
                + body
            )

        elif key == "dashboard":

            active = c.execute(
                """
                SELECT COUNT(*) n
                FROM projects
                WHERE status='Active'
                """
            ).fetchone()["n"]

            outstanding = c.execute(
                """
                SELECT COALESCE(
                    SUM(amount-paid),
                    0
                ) v
                FROM rentals
                """
            ).fetchone()["v"]

            expenses = c.execute(
                """
                SELECT COALESCE(
                    SUM(amount),
                    0
                ) v
                FROM expenses
                """
            ).fetchone()["v"]

            txt = (
                "📈 ASSACO Dashboard\n\n"
                f"🏗 Active Doka projects: {active}\n"
                f"💰 Outstanding rental: "
                f"${outstanding:,.2f}\n"
                f"💸 Recorded expenses: "
                f"${expenses:,.2f}"
            )

        elif key == "expenses":

            txt = (
                "💸 Add Expense\n\n"
                "For now use:\n"
                "/expense PROJECT | CATEGORY | "
                "AMOUNT | SOURCE | NOTE\n\n"
                "Sources:\n"
                "• Project Cash\n"
                "• Own Pocket\n"
                "• Other Project"
            )

        else:

            txt = "🏗 ASSACO Doka Management"

    await q.edit_message_text(
        txt,
        reply_markup=back_kb(),
    )


# ---------------- EXISTING COMMANDS ----------------

async def newproject_command(update, context):

    if not auth(update):
        return

    await update.message.reply_text(
        "For easier entry, open /menu and press "
        "➕ New Project."
    )


async def expense(update, context):

    if not auth(update):
        return

    try:

        s = update.message.text.split(" ", 1)[1]

        code, cat, amount, source, note = [
            x.strip()
            for x in s.split("|", 4)
        ]

        if source not in [
            "Project Cash",
            "Own Pocket",
            "Other Project",
        ]:
            raise ValueError

        with con() as c:

            p = c.execute(
                "SELECT id FROM projects WHERE code=?",
                (code,),
            ).fetchone()

            if not p:
                raise ValueError

            c.execute(
                """
                INSERT INTO expenses(
                    project_id,
                    category,
                    amount,
                    paid_on,
                    money_source,
                    note
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    p["id"],
                    cat,
                    float(amount),
                    date.today().isoformat(),
                    source,
                    note,
                ),
            )

        await update.message.reply_text(
            f"✅ Expense ${float(amount):,.2f} "
            f"recorded for {code} from {source}."
        )

    except Exception:

        await update.message.reply_text(
            "Use:\n"
            "/expense PROJECT | CATEGORY | "
            "AMOUNT | SOURCE | NOTE"
        )


async def payment(update, context):

    if not auth(update):
        return

    try:

        parts = [
            x.strip()
            for x in update.message.text
            .split(" ", 1)[1]
            .split("|")
        ]

        code = parts[0]
        amount = float(parts[1])
        note = (
            parts[2]
            if len(parts) > 2
            else ""
        )

        with con() as c:

            p = c.execute(
                "SELECT id FROM projects WHERE code=?",
                (code,),
            ).fetchone()

            if not p:
                raise ValueError

            remaining = amount

            rents = c.execute(
                """
                SELECT *
                FROM rentals
                WHERE project_id=?
                AND paid<amount
                ORDER BY period_no
                """,
                (p["id"],),
            ).fetchall()

            for r in rents:

                if remaining <= 0:
                    break

                allocation = min(
                    remaining,
                    r["amount"] - r["paid"],
                )

                c.execute(
                    """
                    UPDATE rentals
                    SET paid=paid+?
                    WHERE id=?
                    """,
                    (
                        allocation,
                        r["id"],
                    ),
                )

                c.execute(
                    """
                    INSERT INTO receipts(
                        project_id,
                        rental_id,
                        amount,
                        received_on,
                        note
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        p["id"],
                        r["id"],
                        allocation,
                        date.today().isoformat(),
                        note,
                    ),
                )

                remaining -= allocation

        await update.message.reply_text(
            f"✅ ${amount:,.2f} received for {code}.\n"
            "Payment allocated to the oldest "
            "unpaid rental first."
        )

    except Exception:

        await update.message.reply_text(
            "Use:\n"
            "/payment PROJECT | AMOUNT | NOTE"
        )


async def stock(update, context, direction):

    if not auth(update):
        return

    try:

        code, item, qty = [
            x.strip()
            for x in update.message.text
            .split(" ", 1)[1]
            .split("|")
        ]

        with con() as c:

            p = c.execute(
                "SELECT id FROM projects WHERE code=?",
                (code,),
            ).fetchone()

            i = c.execute(
                """
                SELECT id
                FROM inventory
                WHERE lower(item)=lower(?)
                """,
                (item,),
            ).fetchone()

            if not p or not i:
                raise ValueError

            c.execute(
                """
                INSERT INTO stock_moves(
                    project_id,
                    item_id,
                    direction,
                    qty,
                    moved_on
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    p["id"],
                    i["id"],
                    direction,
                    float(qty),
                    date.today().isoformat(),
                ),
            )

        action = (
            "dispatched to"
            if direction == "OUT"
            else "returned from"
        )

        await update.message.reply_text(
            f"✅ {qty} {item} {action} {code}."
        )

    except Exception:

        command = (
            "dispatch"
            if direction == "OUT"
            else "return"
        )

        await update.message.reply_text(
            f"Use:\n/{command} PROJECT | ITEM | QTY"
        )


async def dispatch(update, context):
    await stock(update, context, "OUT")


async def returned(update, context):
    await stock(update, context, "IN")


async def unknown(update, context):

    if auth(update):
        await update.message.reply_text(
            "Use /menu to open the ASSACO control panel."
        )


# ---------------- RUN ----------------

def run():

    init_db()

    if not TOKEN:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN in Railway Variables"
        )

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    new_project_conversation = ConversationHandler(

        entry_points=[
            CallbackQueryHandler(
                begin_new_project,
                pattern="^new_project$",
            )
        ],

        states={

            PROJECT_CODE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_project_code,
                )
            ],

            CLIENT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_client,
                )
            ],

            LOCATION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_location,
                )
            ],

            INSTALL_DATE: [
                CallbackQueryHandler(
                    today_install_date,
                    pattern="^project_date_today$",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_install_date,
                ),
            ],

            QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_quantity,
                )
            ],

            FIRST_RATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_first_rate,
                )
            ],

            EXTRA_RATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_extra_rate,
                )
            ],

            CONFIRM_PROJECT: [
                CallbackQueryHandler(
                    save_new_project,
                    pattern="^save_new_project$",
                ),
                CallbackQueryHandler(
                    cancel_new_project_button,
                    pattern="^cancel_new_project$",
                ),
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],

        allow_reentry=True,
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "menu",
            menu,
        )
    )

    app.add_handler(
        CommandHandler(
            "newproject",
            newproject_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "expense",
            expense,
        )
    )

    app.add_handler(
        CommandHandler(
            "payment",
            payment,
        )
    )

    app.add_handler(
        CommandHandler(
            "dispatch",
            dispatch,
        )
    )

    app.add_handler(
        CommandHandler(
            "return",
            returned,
        )
    )

    # Must come before general callback handler.
    app.add_handler(
        new_project_conversation
    )

    app.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            unknown,
        )
    )

    print("ASSACO Doka Management Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    run()
