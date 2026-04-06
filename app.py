import logging
import sqlite3
import asyncio
import os
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get 8646214027:AAGIL9oAld6OEaUKH4m1x6pSyKDQlHo-IB4
ADMIN_IDS = [7256797875]

PRICE_DELETE = 100
PRICE_COMPLAINT = 0.01
MIN_COMPLAINT = 1000
MIN_TOPUP = 10

ADDRESSES = {
    "TRC20": "TF57E5NGfFAijin7WBKnQtVuneQJ7xsaCb",
    "BEP20": "0x66E15cB1FbF7424D4d4D1bAC730ca8A7e8C7dbA9"
}
# ==================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask приложение для Render
flask_app = Flask(__name__)

# База данных
db = sqlite3.connect("guarant_bot.db", check_same_thread=False)
cursor = db.cursor()

# Создаем таблицы
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
        tx_hash TEXT,
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

# Остальные функции базы данных (те же, что были в предыдущем коде)
def register_user(user_id, username, full_name):
    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name, registered_at) VALUES (?, ?, ?, ?)",
                   (user_id, username, full_name, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()

def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row: return row[0]
    cursor.execute("INSERT INTO users (user_id, balance, registered_at) VALUES (?, 0, ?)",
                   (user_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    return 0

def update_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
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

def get_deal(deal_id):
    cursor.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))
    return cursor.fetchone()

def update_deal(deal_id, **kwargs):
    for key, value in kwargs.items():
        cursor.execute(f"UPDATE deals SET {key} = ? WHERE id = ?", (value, deal_id))
    db.commit()

def get_pending_deals_for_user(user_id):
    cursor.execute("SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'pending' ORDER BY id DESC", (user_id, user_id))
    return cursor.fetchall()

def get_active_deals_for_user(user_id):
    cursor.execute("SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status IN ('pending', 'processing') ORDER BY id DESC", (user_id, user_id))
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

def confirm_payment(payment_id, tx_hash):
    cursor.execute("UPDATE payments SET status = 'completed', tx_hash = ? WHERE id = ?", (tx_hash, payment_id))
    db.commit()

def get_all_deals():
    cursor.execute("SELECT * FROM deals ORDER BY id DESC LIMIT 50")
    return cursor.fetchall()

def get_deals_stats():
    cursor.execute("SELECT COUNT(*) FROM deals")
    total_deals = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM deals WHERE status = 'pending'")
    pending_deals = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM deals WHERE status = 'completed'")
    completed_deals = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(amount) FROM deals WHERE status = 'completed'")
    total_volume = cursor.fetchone()[0] or 0
    return {"total_deals": total_deals, "pending_deals": pending_deals, "completed_deals": completed_deals, "total_volume": total_volume}

# ========== ФУНКЦИИ ОТПРАВКИ УВЕДОМЛЕНИЙ ==========
async def send_message(user_id, text, reply_markup=None):
    try:
        from telegram import Bot
        bot = Bot(token=TOKEN)
        await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    kb = [
        [InlineKeyboardButton("🛡️ Заключить сделку", callback_data="new_deal")],
        [InlineKeyboardButton("📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton("✅ Активные сделки", callback_data="active_deals")],
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

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.full_name)
    await update.message.reply_text(
        "🛡️ *Добро пожаловать в Guarant Бот!*\n\n"
        "Я помогаю безопасно проводить сделки между покупателем и продавцом.\n\n"
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
        text = "❓ *Помощь*\n\n1. Нажмите 'Заключить сделку' и заполните форму\n2. Укажите название, сумму, сеть, условия и вашу роль\n3. Покупатель оплачивает на указанный кошелек\n4. Администратор подтверждает оплату\n\nПо всем вопросам: @support"
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
        for deal in deals:
            text += f"┌ 🆔 Сделка #{deal[0]}\n├ 📝 {deal[1]}\n├ 💰 {deal[2]} USDT\n├ 📊 {deal[10]}\n└ 📅 {deal[13]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())
        return

    if data == "active_deals":
        deals = get_active_deals_for_user(user_id)
        if not deals:
            await query.edit_message_text("🔄 У вас нет активных сделок в процессе.", reply_markup=main_menu())
            return
        text = "🔄 *АКТИВНЫЕ СДЕЛКИ:*\n\n"
        for deal in deals:
            text += f"┌ 🆔 #{deal[0]}\n├ 📝 {deal[1]}\n├ 💰 {deal[2]} USDT\n├ 📊 {deal[10]}\n└ 📅 {deal[13]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu())
        return

    if data == "completed_deals":
        deals = get_completed_deals_for_user(user_id)
        if not deals:
            await query.edit_message_text("✅ У вас нет завершённых сделок.", reply_markup=main_menu())
            return
        text = "✅ *ЗАВЕРШЁННЫЕ СДЕЛКИ:*\n\n"
        for deal in deals:
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
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа!")
        return
    await update.message.reply_text("🔐 *АДМИН-ПАНЕЛЬ*", reply_markup=admin_keyboard(), parse_mode="Markdown")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("⛔ Нет доступа!")
        return

    if data == "admin_stats":
        stats = get_deals_stats()
        text = f"📊 *СТАТИСТИКА*\n\n📋 Всего сделок: {stats['total_deals']}\n⏳ Ожидают: {stats['pending_deals']}\n✅ Завершено: {stats['completed_deals']}\n💰 Общий объём: {stats['total_volume']} USDT"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_payments":
        payments = get_pending_payments()
        if not payments:
            await query.edit_message_text("💳 Нет ожидающих платежей.", reply_markup=admin_keyboard())
            return
        text = "💳 *ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ:*\n\n"
        kb = []
        for p in payments:
            pid, deal_id, user_id, amount, network, deal_name, buyer_id, seller_id = p
            text += f"📌 Платеж #{pid}\n├ Сделка: {deal_name} (#{deal_id})\n├ Сумма: {amount} {network}\n├ Покупатель: {buyer_id}\n└ Продавец: {seller_id}\n\n"
            kb.append([InlineKeyboardButton(f"✅ Подтвердить платеж #{pid}", callback_data=f"admin_confirm_payment_{pid}")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin_confirm_payment_"):
        payment_id = int(data.split("_")[3])
        cursor.execute("""
            SELECT p.deal_id, p.user_id, p.amount, d.buyer_id, d.seller_id, d.deal_name
            FROM payments p
            JOIN deals d ON p.deal_id = d.id
            WHERE p.id = ?
        """, (payment_id,))
        payment = cursor.fetchone()
        if payment:
            deal_id, payer_id, amount, buyer_id, seller_id, deal_name = payment
            confirm_payment(payment_id, f"TX_{payment_id}")
            update_deal(deal_id, payment_status="completed", status="completed", completed_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
            await send_message(seller_id, f"✅ *ПЛАТЕЖ ПОДТВЕРЖДЁН!*\n\n📋 Сделка: {deal_name} (#{deal_id})\n💰 Сумма: {amount} USDT\n\n🔄 Сделка завершена!", main_menu())
            await send_message(payer_id, f"✅ *ВАШ ПЛАТЕЖ ПОДТВЕРЖДЁН!*\n\n📋 Сделка: {deal_name} (#{deal_id})\n💰 Сумма: {amount} USDT\n\n✅ Сделка успешно завершена!", main_menu())
            await query.edit_message_text(f"✅ Платеж #{payment_id} подтверждён!\n📢 Уведомления отправлены.", reply_markup=admin_keyboard())
        else:
            await query.edit_message_text("❌ Платёж не найден!", reply_markup=admin_keyboard())
        return

    if data == "admin_deals":
        deals = get_all_deals()
        if not deals:
            await query.edit_message_text("📋 Нет сделок.", reply_markup=admin_keyboard())
            return
        text = "📋 *ВСЕ СДЕЛКИ:*\n\n"
        for deal in deals[:20]:
            text += f"┌ 🆔 #{deal[0]}\n├ 📝 {deal[1]}\n├ 💰 {deal[2]} USDT\n├ 📊 {deal[10]}\n└ 📅 {deal[13]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_users":
        cursor.execute("SELECT user_id, username, registered_at FROM users ORDER BY registered_at DESC LIMIT 20")
        users = cursor.fetchall()
        if not users:
            await query.edit_message_text("👥 Нет пользователей.", reply_markup=admin_keyboard())
            return
        text = "👥 *ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ:*\n\n"
        for u in users:
            text += f"┌ 🆔 {u[0]}\n├ 📝 @{u[1] or 'нет'}\n└ 📅 {u[2]}\n\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_back":
        await query.edit_message_text("🔐 *АДМИН-ПАНЕЛЬ*", reply_markup=admin_keyboard(), parse_mode="Markdown")
        return

# ========== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ==========
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_username = update.effective_user.username or "нет"
    text = update.message.text.strip()
    step = context.user_data.get('deal_step')

    if step == 'deal_name':
        context.user_data['deal_name'] = text
        context.user_data['deal_step'] = 'deal_amount'
        await update.message.reply_text("💰 *Шаг 2/5: Сумма сделки*\n\nВведите сумму в USDT (минимальная сумма 10 USDT):", parse_mode="Markdown")
        return

    if step == 'deal_amount':
        try:
            amount = float(text)
            if amount < 10:
                await update.message.reply_text("❌ Минимальная сумма 10 USDT. Введите снова:")
                return
            context.user_data['deal_amount'] = amount
            context.user_data['deal_step'] = 'deal_role'
            await update.message.reply_text("👥 *Шаг 3/5: Выберите вашу роль в сделке*", reply_markup=role_keyboard(), parse_mode="Markdown")
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
            f"⏳ Ожидайте подтверждения от администратора.",
            reply_markup=main_menu(), parse_mode="Markdown"
        )
        return

    await update.message.reply_text("Используйте кнопки меню!", reply_markup=main_menu())

# ========== ЗАПУСК БОТА (В ПОТОКЕ) ==========
def run_bot():
    """Запуск Telegram бота в отдельном потоке"""
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(?!admin_).*"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_.*"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    print("✅ Бот запущен и работает!")
    app.run_polling()

# ========== FLASK ДЛЯ RENDER ==========
@flask_app.route('/')
def health_check():
    """Health check endpoint для Render"""
    return jsonify({"status": "ok", "message": "Bot is running"}), 200

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

# ========== ГЛАВНЫЙ ЗАПУСК ==========
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    
    # Запускаем Flask сервер для Render
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
