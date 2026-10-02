# ASSACO Doka Telegram Bot — MVP

A working local-first Telegram control bot for Doka projects, rental periods, receivables, expenses, inventory movements and project P&L.

## Included
- Doka project master records
- First-month + additional-month rental logic
- Rental due dates
- Partial payments allocated to oldest unpaid rental
- Expenses with Money Source: Project Cash / Own Pocket / Other Project
- Inventory for High Props, Regular Props, Doka Props, tubes, H20, braces, U-heads, joints and bolts
- Dispatch / return stock movements
- Project P&L and dashboard
- Admin Telegram ID restriction

## Setup
1. Create a Telegram bot with @BotFather using `/newbot`.
2. Keep the token private.
3. Install Python 3.11+.
4. In this folder: `pip install -r requirements.txt`
5. Copy `.env.example` to `.env`.
6. Put your token in `TELEGRAM_BOT_TOKEN` and your Telegram numeric user ID in `ADMIN_TELEGRAM_IDS`.
7. Run: `python bot.py`
8. Open your bot and send `/start`.

## Main commands
`/newproject CODE | CLIENT | LOCATION | YYYY-MM-DD | QTY | FIRST_RATE | EXTRA_RATE`

`/payment PROJECT | AMOUNT | NOTE`

`/expense PROJECT | CATEGORY | AMOUNT | SOURCE | NOTE`

`/dispatch PROJECT | ITEM | QTY`

`/return PROJECT | ITEM | QTY`

Use `/menu` for the button-based control panel.

## Rental logic
If installation is 2026-09-18, Month 1 covers 2026-09-18 → 2026-10-18 and is due immediately. Month 2 covers 2026-10-18 → 2026-11-18 and is due on 2026-10-18. Partial receipts reduce the oldest unpaid rental first.

## Production upgrade
For a full multi-user deployment, move SQLite to PostgreSQL, host the backend continuously, use Telegram webhooks over HTTPS, add role permissions, documents/photos, scheduled due-date alerts, audit logs, and a Telegram Mini App/web dashboard.
