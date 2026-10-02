import os, sqlite3, calendar
from datetime import date, datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

load_dotenv()
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','')
DB=os.getenv('DATABASE_PATH','assaco.db')
ADMINS={int(x.strip()) for x in os.getenv('ADMIN_TELEGRAM_IDS','').split(',') if x.strip().isdigit()}

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, client_id INTEGER, location TEXT, status TEXT DEFAULT 'Active', installation_date TEXT, dismantling_date TEXT, quantity REAL DEFAULT 0, quantity_unit TEXT DEFAULT 'm3', first_rate REAL DEFAULT 0, extra_rate REAL DEFAULT 0, notes TEXT, FOREIGN KEY(client_id) REFERENCES clients(id));
CREATE TABLE IF NOT EXISTS rentals(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, period_no INTEGER NOT NULL, period_start TEXT, period_end TEXT, due_date TEXT, amount REAL DEFAULT 0, paid REAL DEFAULT 0, UNIQUE(project_id,period_no), FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, rental_id INTEGER, amount REAL NOT NULL, received_on TEXT NOT NULL, note TEXT, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(rental_id) REFERENCES rentals(id));
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, project_id INTEGER, category TEXT, amount REAL NOT NULL, paid_on TEXT NOT NULL, money_source TEXT CHECK(money_source IN ('Project Cash','Own Pocket','Other Project')), source_project_id INTEGER, note TEXT, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY, item TEXT UNIQUE NOT NULL, initial_qty REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_moves(id INTEGER PRIMARY KEY, project_id INTEGER, item_id INTEGER NOT NULL, direction TEXT CHECK(direction IN ('OUT','IN')), qty REAL NOT NULL, moved_on TEXT NOT NULL, note TEXT, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(item_id) REFERENCES inventory(id));
CREATE TABLE IF NOT EXISTS variations(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, description TEXT, quantity REAL DEFAULT 0, unit TEXT, amount REAL DEFAULT 0, created_on TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
'''

def con():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
    with con() as c:
        c.executescript(SCHEMA)
        for item in ['High Props','Regular Props','Doka Props','2in Tubes','H20 Beams','1m Braces','2m Braces','U-Heads','Joints','Bolts']:
            c.execute('INSERT OR IGNORE INTO inventory(item) VALUES(?)',(item,))

def add_month(d, n=1):
    y=d.year+(d.month-1+n)//12; m=(d.month-1+n)%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))

def ensure_rental(project_id, period_no):
    with con() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not p or not p['installation_date']: return
        start=add_month(date.fromisoformat(p['installation_date']), period_no-1); end=add_month(start,1)
        rate=p['first_rate'] if period_no==1 else p['extra_rate']; amount=(p['quantity'] or 0)*(rate or 0)
        due=p['installation_date'] if period_no==1 else start.isoformat()
        c.execute('INSERT OR IGNORE INTO rentals(project_id,period_no,period_start,period_end,due_date,amount) VALUES(?,?,?,?,?,?)',(project_id,period_no,start.isoformat(),end.isoformat(),due,amount))

def auth(update): return not ADMINS or update.effective_user.id in ADMINS

def main_kb():
    rows=[ [('🏗 Doka Projects','projects'),('💰 Receivables','receivables')], [('💸 Expenses','expenses'),('📦 Inventory','inventory')], [('📊 P&L','pnl'),('🔔 Due Dates','dues')], [('➕ New Project','new_project'),('📈 Dashboard','dashboard')] ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=c) for t,c in r] for r in rows])

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not auth(update): return await update.message.reply_text('Unauthorized account.')
    await update.message.reply_text('ASSACO Doka Management\nChoose an action:',reply_markup=main_kb())

async def menu(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text('ASSACO Doka Management',reply_markup=main_kb())

async def callbacks(update:Update, context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if not auth(update): return await q.edit_message_text('Unauthorized account.')
    key=q.data
    with con() as c:
        if key=='projects':
            ps=c.execute("SELECT p.*,cl.name client FROM projects p LEFT JOIN clients cl ON cl.id=p.client_id ORDER BY p.id DESC LIMIT 30").fetchall()
            txt='🏗 Doka Projects\n\n'+('\n'.join(f"• {p['code']} — {p['client'] or '-'} — {p['status']}" for p in ps) or 'No projects yet.')
        elif key=='receivables':
            r=c.execute("SELECT p.code,SUM(r.amount-r.paid) bal FROM rentals r JOIN projects p ON p.id=r.project_id GROUP BY p.id HAVING bal>0 ORDER BY bal DESC").fetchall()
            txt='💰 Outstanding Receivables\n\n'+('\n'.join(f"• {x['code']}: ${x['bal']:,.2f}" for x in r) or 'Nothing outstanding.')
        elif key=='dues':
            r=c.execute("SELECT p.code,r.period_no,r.due_date,(r.amount-r.paid) bal FROM rentals r JOIN projects p ON p.id=r.project_id WHERE r.amount>r.paid ORDER BY r.due_date LIMIT 30").fetchall()
            txt='🔔 Rental Due Dates\n\n'+('\n'.join(f"• {x['code']} M{x['period_no']} — {x['due_date']} — ${x['bal']:,.2f}" for x in r) or 'No unpaid rental periods.')
        elif key=='inventory':
            r=c.execute("SELECT i.item,i.initial_qty+COALESCE(SUM(CASE WHEN s.direction='IN' THEN s.qty ELSE -s.qty END),0) avail FROM inventory i LEFT JOIN stock_moves s ON s.item_id=i.id GROUP BY i.id ORDER BY i.item").fetchall()
            txt='📦 Available Stock\n\n'+'\n'.join(f"• {x['item']}: {x['avail']:g}" for x in r)
        elif key=='pnl':
            r=c.execute("SELECT p.code,COALESCE((SELECT SUM(amount) FROM rentals WHERE project_id=p.id),0)+COALESCE((SELECT SUM(amount) FROM variations WHERE project_id=p.id),0) revenue,COALESCE((SELECT SUM(amount) FROM expenses WHERE project_id=p.id),0) cost FROM projects p ORDER BY p.id DESC").fetchall()
            txt='📊 Project P&L\n\n'+('\n'.join(f"• {x['code']}: Rev ${x['revenue']:,.0f} | Cost ${x['cost']:,.0f} | P&L ${x['revenue']-x['cost']:,.0f}" for x in r) or 'No projects yet.')
        elif key=='dashboard':
            active=c.execute("SELECT COUNT(*) n FROM projects WHERE status='Active'").fetchone()['n']; out=c.execute('SELECT COALESCE(SUM(amount-paid),0) v FROM rentals').fetchone()['v']; exp=c.execute('SELECT COALESCE(SUM(amount),0) v FROM expenses').fetchone()['v']
            txt=f'📈 ASSACO Dashboard\n\nActive Doka projects: {active}\nOutstanding rental: ${out:,.2f}\nRecorded expenses: ${exp:,.2f}'
        elif key=='new_project': txt='➕ Add a project with:\n/newproject CODE | CLIENT | LOCATION | YYYY-MM-DD | QTY | FIRST_RATE | EXTRA_RATE\n\nExample:\n/newproject 4543 | Arsh | Hadath | 2026-10-02 | 2000 | 3 | 2'
        elif key=='expenses': txt='💸 Add expense:\n/expense PROJECT | CATEGORY | AMOUNT | SOURCE | NOTE\nSources: Project Cash / Own Pocket / Other Project'
        else: txt='ASSACO Doka Management'
    await q.edit_message_text(txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Menu',callback_data='home')]]) if key!='home' else main_kb())
    if key=='home': await q.edit_message_reply_markup(reply_markup=main_kb())

async def newproject(update, context):
    if not auth(update): return
    try:
        s=update.message.text.split(' ',1)[1]; code,client,loc,inst,qty,fr,er=[x.strip() for x in s.split('|')]
        with con() as c:
            c.execute('INSERT OR IGNORE INTO clients(name) VALUES(?)',(client,)); cid=c.execute('SELECT id FROM clients WHERE name=?',(client,)).fetchone()['id']
            c.execute('INSERT INTO projects(code,client_id,location,installation_date,quantity,first_rate,extra_rate) VALUES(?,?,?,?,?,?,?)',(code,cid,loc,inst,float(qty),float(fr),float(er))); pid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
        ensure_rental(pid,1); ensure_rental(pid,2)
        await update.message.reply_text(f'✅ Doka project {code} created. First and second rental periods generated.')
    except Exception as e: await update.message.reply_text('Format error. Use:\n/newproject CODE | CLIENT | LOCATION | YYYY-MM-DD | QTY | FIRST_RATE | EXTRA_RATE')

async def expense(update, context):
    if not auth(update): return
    try:
        s=update.message.text.split(' ',1)[1]; code,cat,amount,source,note=[x.strip() for x in s.split('|',4)]
        if source not in ['Project Cash','Own Pocket','Other Project']: raise ValueError()
        with con() as c:
            p=c.execute('SELECT id FROM projects WHERE code=?',(code,)).fetchone(); c.execute('INSERT INTO expenses(project_id,category,amount,paid_on,money_source,note) VALUES(?,?,?,?,?,?)',(p['id'],cat,float(amount),date.today().isoformat(),source,note))
        await update.message.reply_text(f'✅ Expense ${float(amount):,.2f} recorded for {code} from {source}.')
    except: await update.message.reply_text('Use:\n/expense PROJECT | CATEGORY | AMOUNT | SOURCE | NOTE')

async def payment(update, context):
    if not auth(update): return
    try:
        parts=[x.strip() for x in update.message.text.split(' ',1)[1].split('|')]; code,amount=parts[0],float(parts[1]); note=parts[2] if len(parts)>2 else ''
        with con() as c:
            p=c.execute('SELECT id FROM projects WHERE code=?',(code,)).fetchone(); remaining=amount
            rents=c.execute('SELECT * FROM rentals WHERE project_id=? AND paid<amount ORDER BY period_no',(p['id'],)).fetchall()
            for r in rents:
                if remaining<=0: break
                alloc=min(remaining,r['amount']-r['paid']); c.execute('UPDATE rentals SET paid=paid+? WHERE id=?',(alloc,r['id'])); c.execute('INSERT INTO receipts(project_id,rental_id,amount,received_on,note) VALUES(?,?,?,?,?)',(p['id'],r['id'],alloc,date.today().isoformat(),note)); remaining-=alloc
        await update.message.reply_text(f'✅ ${amount:,.2f} received for {code} and allocated to oldest unpaid rental first.')
    except: await update.message.reply_text('Use:\n/payment PROJECT | AMOUNT | NOTE')

async def stock(update, context, direction):
    if not auth(update): return
    try:
        code,item,qty=[x.strip() for x in update.message.text.split(' ',1)[1].split('|')]
        with con() as c:
            p=c.execute('SELECT id FROM projects WHERE code=?',(code,)).fetchone(); i=c.execute('SELECT id FROM inventory WHERE lower(item)=lower(?)',(item,)).fetchone(); c.execute('INSERT INTO stock_moves(project_id,item_id,direction,qty,moved_on) VALUES(?,?,?,?,?)',(p['id'],i['id'],direction,float(qty),date.today().isoformat()))
        await update.message.reply_text(f"✅ {qty} {item} {'dispatched to' if direction=='OUT' else 'returned from'} {code}.")
    except: await update.message.reply_text(f"Use:\n/{'dispatch' if direction=='OUT' else 'return'} PROJECT | ITEM | QTY")

async def dispatch(u,c): await stock(u,c,'OUT')
async def returned(u,c): await stock(u,c,'IN')

async def unknown(update, context):
    if auth(update): await update.message.reply_text('Use /menu to open the ASSACO control panel.')

def run():
    init_db()
    if not TOKEN: raise RuntimeError('Set TELEGRAM_BOT_TOKEN in .env')
    app=Application.builder().token(TOKEN).build()
    for cmd,fn in [('start',start),('menu',menu),('newproject',newproject),('expense',expense),('payment',payment),('dispatch',dispatch),('return',returned)]: app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(callbacks)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,unknown)); app.run_polling()
if __name__=='__main__': run()
