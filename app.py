import logging
import sqlite3
import asyncio
import os
from datetime import datetime
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = "8699555902:AAFMB9N0OFFVGX1Bf8mIHQbYOhvqDtHY"
ADMIN_IDS = [7256797875]

ADDRESSES = {
    "TRC20": "TF57E5NGfFAijin7WBKnQtVuneQJ7xsaCb",
    "BEP20": "0x66E15cB1FbF7424D4d4D1bAC730ca8A7e8C7dbA9"
}
# ==================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

# База данных
db = sqlite3.connect("guarant_bot.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        registered_at TEXT
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_name TEXT,
        amount REAL,
        network TEXT,
        conditions TEXT,
        buyer_id INTEGER,
        buyer_username TEXT,
        seller_id INTEGER,
        seller_username TEXT,
        role TEXT,
        status TEXT DEFAULT 'pending',
        payment_status TEXT DEFAULT 'waiting',
        created_at TEXT,
        completed_at TEXT
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id INTEGER,
        user_id INTEGER,
        amount REAL,
        network TEXT,
        tx_hash TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
""")

db.commit()

def register_user(user_id, username, full_name):
    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name, registered_at) VALUES (?, ?, ?, ?)",
                   (user_id, username, full_name, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()

def add_deal(deal_name, amount, network, conditions, user_id, user_username, role):
    cursor.execute("""
        INSERT INTO deals (deal_name, amount, network, conditions, buyer_id, buyer_username, seller_id, seller_username, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (deal_name, amount, network, conditions,
          user_id if role == "buyer" else None,
          user_username if role == "buyer" else None,
          user_id if role == "seller" else None,
          user_username if role == "seller" else None,
          role, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    return cursor.lastrowid

def get_pending_deals_for_user(user_id):
    cursor.execute("SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'pending' ORDER BY id DESC", (user_id, user_id))
    return cursor.fetchall()

def get_completed_deals_for_user(user_id):
    cursor.execute("SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'completed' ORDER BY id DESC LIMIT 10", (user_id, user_id))
    return cursor.fetchall()

def add_payment(deal_id, user_id, amount, network):
    cursor.execute("INSERT INTO payments (deal_id, user_id, amount, network, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                   (deal_id, user_id, amount, network, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    return cursor.lastrowid

def get_pending_payments():
    cursor.execute("""
        SELECT p.id, p.deal_id, p.user_id, p.amount, p.network, d.deal_name, d.buyer_id, d.seller_id
        FROM payments p
        JOIN deals d ON p.deal_id = d.id
        WHERE p.status = 'pending'
        ORDER BY p.id DESC
    """)
    return cursor.fetchall()

def confirm_payment(payment_id):
    cursor.execute("UPDATE payments SET status = 'completed' WHERE id = ?", (payment_id,))
    db.commit()

def update_deal(deal_id, **kwargs):
    for key, value in kwargs.items():
        cursor.execute(f"UPDATE deals SET {key} = ? WHERE id = ?", (value, deal_id))
    db.commit()

def get_all_deals():
    cursor.execute("SELECT * FROM deals ORDER BY id DESC LIMIT 50")
    return cursor.fetchall()

def get_all_users():
    cursor.execute("SELECT user_id, username, registered_at FROM users ORDER BY registered_at DESC LIMIT 20")
    return cursor.fetchall()

def get_stats():
    cursor.execute("SELECT COUNT(*) FROM deals")
    total_deals = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM deals WHERE status = 'completed'")
    completed_deals = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(amount) FROM deals WHERE status = 'completed'")
    total_volume = cursor.fetchone()[0] or 0
    return total_deals, completed_deals, total_volume

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    kb = [
        [InlineKeyboardButton("🛡️ Заключить сделку", callback_data="new_deal")],
        [InlineKeyboardButton("📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton("📊 Завершённые", callback_data="completed_deals")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(kb)

def role_keyboard():
    kb = [[InlineKeyboardButton("🛒 Я покупатель", callback_data="role_buyer")], [InlineKeyboardButton("📦 Я продавец", callback_data="role_seller")], [InlineKeyboardButton("🔙 Назад", callback_data="back")]]
    return InlineKeyboardMarkup(kb)

def network_keyboard():
    kb = [[InlineKeyboardButton("💎 USDT (TRC20)", callback_data="network_trc20")], [InlineKeyboardButton("💎 USDT (BEP20)", callback_data="network_bep20")], [InlineKeyboardButton("🔙 Назад", callback_data="back")]]
    return InlineKeyboardMarkup(kb)

def admin_keyboard():
    kb = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("💳 Подтвердить платежи", callback_data="admin_payments")],
        [InlineKeyboardButton("📋 Все сделки", callback_data="admin_deals")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🔙 Выход", callback_data="back")]
    ]
    return InlineKeyboardMarkup(kb)

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.full_name)
    await update.message.reply_text(
        "🛡️ *Добро пожаловать в Guarant Бот!*\n\n"
        "Я помогаю безопасно проводить сделки.\n\n"
        "Выберите действие:",
        reply_markup=main_menu(), parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    user_username = query.from_user.username or "нет"

    if data == "back":
        await query.edit_message_text("Главное меню:", reply_markup=main_menu())
        return

    if data == "help":
        text = "❓ *Помощь*\n\n1. Нажмите 'Заключить сделку' и заполните форму\n2. Покупатель оплачивает на указанный кошелек\n3. Администратор подтверждает оплату\n\nПо всем вопросам: @support"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())
        return

    if data == "new_deal":
        context.user_data['deal_step'] = 'deal_name'
        await query.edit_message_text("📝 *Шаг 1/5: Название сделки*\n\nВведите название сделки:", parse_mode="Markdown")
        return

    if data == "my_deals":
        deals = get_pending_deals_for_user(user_id)
        if not deals:
            await query.edit_message_text("📋 У вас нет активных сделок.", reply_markup=main_menu())
            return
        text = "📋 *ВАШИ СДЕЛКИ:*\n\n"
        for deal in deals[:10]:
            text += f"┌ 🆔 Сделка #{deal[0]}\n├ 📝 {deal[1]}\n├ 💰 {deal[2]} USDT\n├ 📊 {deal[10]}\n└ 📅 {deal[13]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())
        return

    if data == "completed_deals":
        deals = get_completed_deals_for_user(user_id)
        if not deals:
            await query.edit_message_text("✅ У вас нет завершённых сделок.", reply_markup=main_menu())
            return
        text = "✅ *ЗАВЕРШЁННЫЕ СДЕЛКИ:*\n\n"
        for deal in deals[:10]:
            text += f"┌ 🆔 #{deal[0]}\n├ 📝 {deal[1]}\n├ 💰 {deal[2]} USDT\n└ ✅ Завершена\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())
        return

    if data == "role_buyer":
        context.user_data['deal_role'] = 'buyer'
        await query.edit_message_text("✅ Вы выбрали роль *Покупатель*", parse_mode="Markdown")
        await query.edit_message_text("🌐 *Шаг 4/5: Выберите сеть для оплаты*", reply_markup=network_keyboard(), parse_mode="Markdown")
        return

    if data == "role_seller":
        context.user_data['deal_role'] = 'seller'
        await query.edit_message_text("✅ Вы выбрали роль *Продавец*", parse_mode="Markdown")
        await query.edit_message_text("🌐 *Шаг 4/5: Выберите сеть для оплаты*", reply_markup=network_keyboard(), parse_mode="Markdown")
        return

    if data == "network_trc20":
        context.user_data['deal_network'] = "TRC20"
        await query.edit_message_text(f"✅ Вы выбрали сеть *TRC20*\n\n📤 Адрес для оплаты:\n`{ADDRESSES['TRC20']}`\n\n⚠️ Сохраните этот адрес, он понадобится для оплаты.", parse_mode="Markdown")
        await query.message.reply_text("📝 *Шаг 5/5: Условия сделки*\n\nОпишите условия сделки:", parse_mode="Markdown")
        return

    if data == "network_bep20":
        context.user_data['deal_network'] = "BEP20"
        await query.edit_message_text(f"✅ Вы выбрали сеть *BEP20*\n\n📤 Адрес для оплаты:\n`{ADDRESSES['BEP20']}`\n\n⚠️ Сохраните этот адрес, он понадобится для оплаты.", parse_mode="Markdown")
        await query.message.reply_text("📝 *Шаг 5/5: Условия сделки*\n\nОпишите условия сделки:", parse_mode="Markdown")
        return

    if data.startswith("copy_"):
        address = data.replace("copy_", "")
        await query.answer(f"✅ Адрес скопирован!", show_alert=True)
        return

    # ========== АДМИН-ПАНЕЛЬ ==========
    if data == "admin_stats":
        total, completed, volume = get_stats()
        text = f"📊 *СТАТИСТИКА*\n\n📋 Всего сделок: {total}\n✅ Завершено: {completed}\n💰 Объём: {volume} USDT"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_payments":
        payments = get_pending_payments()
        if not payments:
            await query.edit_message_text("💳 Нет платежей.", reply_markup=admin_keyboard())
            return
        text = "💳 *ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ:*\n\n"
        kb = []
        for p in payments:
            pid, deal_id, uid, amt, net, dname, buyer, seller = p
            text += f"📌 Платеж #{pid}\n├ Сделка: {dname}\n├ Сумма: {amt} {net}\n├ Покупатель: {buyer}\n└ Продавец: {seller}\n\n"
            kb.append([InlineKeyboardButton(f"✅ Подтвердить #{pid}", callback_data=f"confirm_payment_{pid}")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("confirm_payment_"):
        payment_id = int(data.split("_")[2])
        cursor.execute("SELECT deal_id, user_id, amount, buyer_id, seller_id, deal_name FROM payments p JOIN deals d ON p.deal_id = d.id WHERE p.id=?", (payment_id,))
        pay = cursor.fetchone()
        if pay:
            deal_id, payer_id, amount, buyer_id, seller_id, deal_name = pay
            confirm_payment(payment_id)
            update_deal(deal_id, status="completed", completed_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
            await query.edit_message_text(f"✅ Платеж #{payment_id} подтверждён!", reply_markup=admin_keyboard())
        else:
            await query.edit_message_text("❌ Платёж не найден.", reply_markup=admin_keyboard())
        return

    if data == "admin_deals":
        deals = get_all_deals()
        if not deals:
            await query.edit_message_text("📋 Нет сделок.", reply_markup=admin_keyboard())
            return
        text = "📋 *ВСЕ СДЕЛКИ:*\n\n"
        for d in deals[:20]:
            text += f"┌ #{d[0]} | {d[1]}\n├ 💰 {d[2]} USDT\n├ 📊 {d[10]}\n└ 📅 {d[13]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_users":
        users = get_all_users()
        if not users:
            await query.edit_message_text("👥 Нет пользователей.", reply_markup=admin_keyboard())
            return
        text = "👥 *ПОЛЬЗОВАТЕЛИ:*\n\n"
        for u in users:
            text += f"┌ 🆔 {u[0]}\n├ 📝 @{u[1] or 'нет'}\n└ 📅 {u[2]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_back":
        await query.edit_message_text("🔐 *АДМИН-ПАНЕЛЬ*", reply_markup=admin_keyboard(), parse_mode="Markdown")
        return

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_username = update.effective_user.username or "нет"
    text = update.message.text.strip()
    step = context.user_data.get('deal_step')

    if step == 'deal_name':
        context.user_data['deal_name'] = text
        context.user_data['deal_step'] = 'deal_amount'
        await update.message.reply_text("💰 *Шаг 2/5: Сумма сделки*\n\nВведите сумму в USDT (мин. 10 USDT):", parse_mode="Markdown")
        return

    if step == 'deal_amount':
        try:
            amount = float(text)
            if amount < 10:
                await update.message.reply_text("❌ Минимум 10 USDT. Введите снова:")
                return
            context.user_data['deal_amount'] = amount
            context.user_data['deal_step'] = 'deal_role'
            await update.message.reply_text("👥 *Шаг 3/5: Выберите вашу роль*", reply_markup=role_keyboard(), parse_mode="Markdown")
        except:
            await update.message.reply_text("❌ Введите число!")
        return

    if step == 'deal_conditions':
        context.user_data['deal_conditions'] = text
        deal_id = add_deal(
            context.user_data.get('deal_name'),
            context.user_data.get('deal_amount'),
            context.user_data.get('deal_network'),
            text,
            user_id,
            user_username,
            context.user_data.get('deal_role')
        )
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *СДЕЛКА #{deal_id} СОЗДАНА!*\n\n"
            f"📝 Название: {context.user_data.get('deal_name')}\n"
            f"💰 Сумма: {context.user_data.get('deal_amount')} USDT\n"
            f"🌐 Сеть: {context.user_data.get('deal_network')}\n\n"
            f"⏳ Ожидайте оплаты и подтверждения.",
            reply_markup=main_menu(), parse_mode="Markdown"
        )
        return

    await update.message.reply_text("Используйте кнопки меню!", reply_markup=main_menu())

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа!")
        return
    await update.message.reply_text("🔐 *АДМИН-ПАНЕЛЬ*", reply_markup=admin_keyboard(), parse_mode="Markdown")

# ========== ЗАПУСК ==========
def run_bot():
    """Запуск бота в отдельном потоке с правильным event loop"""
    try:
        # Создаём новый event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Создаём приложение
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("admin", admin_panel))
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        
        print("✅ Бот запущен и работает!")
        
        # Запускаем бота
        loop.run_until_complete(app.initialize())
        loop.run_until_complete(app.start())
        loop.run_until_complete(app.updater.start_polling())
        
        # Держим бота запущенным
        loop.run_forever()
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

@flask_app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Bot is running"}), 200

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    import threading
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask сервер
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
