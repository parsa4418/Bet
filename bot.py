# -*- coding: utf-8 -*-
"""
بات شرط‌بندی الماس با Webhook - نسخه نهایی با تنظیمات دقیق
"""

import sqlite3
import random
import re
import os
import time
import threading
import logging
from flask import Flask, request
import telebot
from telebot import types

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "توکن-خودت-رو-اینجا-بذار")
ADMIN_IDS = [8904869158]  # آیدی عددی خود را اینجا قرار دهید
START_DIAMONDS = 10000
REFERRAL_BONUS = 250000
TAX_RATE = 0.10
TAX_RECEIVER_ID = ADMIN_IDS[0]
JOIN_TIMEOUT_SECONDS = 60

SPIN_MIN = 5000
SPIN_MAX = 250000
SPIN_COOLDOWN_HOURS = 24

LOAN_MAX = 500000
LOAN_TAX_RATE = 0.10

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
DB_PATH = "diamonds.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== قفل‌ها ==================
db_lock = threading.Lock()
casino_lock = threading.Lock()
active_casino_games = {}
casino_timers = {}

# ================== دیتابیس ==================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            diamonds INTEGER DEFAULT 0,
            referred_by INTEGER,
            ref_count INTEGER DEFAULT 0,
            loan_balance INTEGER DEFAULT 0,
            last_spin INTEGER DEFAULT 0
        )
    """)
    for col_def in ("loan_balance INTEGER DEFAULT 0", "last_spin INTEGER DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            creator_name TEXT,
            amount INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            status TEXT DEFAULT 'pending'
        )
    """)
    return conn

def get_user(user_id):
    with db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return row
        finally:
            conn.close()

def create_user(user_id, username, referred_by=None):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO users (user_id, username, diamonds, referred_by) VALUES (?,?,?,?)",
                (user_id, username, START_DIAMONDS, referred_by),
            )
            conn.commit()
        finally:
            conn.close()

def update_diamonds(user_id, amount):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (amount, user_id))
            conn.commit()
        finally:
            conn.close()

def add_ref_count(user_id):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()

def get_balance(user_id):
    row = get_user(user_id)
    return row[2] if row else 0

def get_display_name(user):
    return user.username and f"@{user.username}" or user.first_name

def create_bet(creator_id, creator_name, amount, chat_id, message_id):
    with db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO bets (creator_id, creator_name, amount, chat_id, message_id, status) VALUES (?,?,?,?,?,'pending')",
                (creator_id, creator_name, amount, chat_id, message_id),
            )
            conn.commit()
            bet_id = cur.lastrowid
            return bet_id
        finally:
            conn.close()

def get_bet(bet_id):
    with db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
            return row
        finally:
            conn.close()

def set_bet_status(bet_id, status):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE bets SET status=? WHERE bet_id=?", (status, bet_id))
            conn.commit()
        finally:
            conn.close()

def get_top_users(limit=10):
    with db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT user_id, username, diamonds FROM users ORDER BY diamonds DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return rows
        finally:
            conn.close()

def get_loan_balance(user_id):
    row = get_user(user_id)
    return row[5] if row else 0

def change_loan_balance(user_id, delta):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET loan_balance = MAX(0, loan_balance + ?) WHERE user_id=?",
                (delta, user_id),
            )
            conn.commit()
        finally:
            conn.close()

def get_last_spin(user_id):
    row = get_user(user_id)
    return row[6] if row else 0

def set_last_spin(user_id, ts):
    with db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET last_spin=? WHERE user_id=?", (ts, user_id))
            conn.commit()
        finally:
            conn.close()

def calculate_payout(winner_id, pool):
    admin_tax = int(pool * TAX_RATE)
    loan_repay = 0
    loan_balance = get_loan_balance(winner_id)
    if loan_balance > 0:
        loan_cut = int(pool * LOAN_TAX_RATE)
        loan_repay = min(loan_cut, loan_balance)
        if loan_repay > 0:
            change_loan_balance(winner_id, -loan_repay)
    final_payout = pool - admin_tax - loan_repay
    if final_payout < 0:
        final_payout = 0
    return final_payout, admin_tax, loan_repay

# ================== توابع دکمه‌ها ==================
def main_menu_markup():
    """دکمه‌های منوی اصلی"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👤 حساب کاربری", callback_data="showaccount"))
    markup.add(types.InlineKeyboardButton("👥 زیرمجموعه‌گیری", callback_data="showreferral"))
    markup.add(types.InlineKeyboardButton("💰 وام الماس", callback_data="loanmenu"))
    markup.add(types.InlineKeyboardButton("🎡 گردونه الماس", callback_data="spinwheel"))
    markup.add(types.InlineKeyboardButton("🎰 کازینو", callback_data="casinomenu"))  # کازینو قبل از راهنما
    markup.add(types.InlineKeyboardButton("📖 راهنما", callback_data="showhelp"))
    return markup

def back_to_main_menu_markup():
    """دکمه بازگشت به منوی اصلی (برای بخش‌های فرعی غیر از کازینو)"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
    return markup

# ================== توابع ویرایش امن ==================
def safe_edit_message(text, chat_id, message_id, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except Exception as e:
        logging.error(f"خطا در ویرایش پیام {message_id}: {e}")
        return False

def safe_send_message(chat_id, text, reply_markup=None):
    try:
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"خطا در ارسال پیام: {e}")
        return None

# ================== هندلر start ==================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    username = get_display_name(message.from_user)
    is_new = not get_user(user_id)

    if is_new:
        args = message.text.split()
        referrer_id = None
        if len(args) > 1 and args[1].isdigit():
            cand = int(args[1])
            if cand != user_id and get_user(cand):
                referrer_id = cand

        create_user(user_id, username, referred_by=referrer_id)

        if referrer_id:
            update_diamonds(referrer_id, REFERRAL_BONUS)
            add_ref_count(referrer_id)
            try:
                bot.send_message(referrer_id, f"🎉 یک نفر با لینک رفرال شما وارد شد! {REFERRAL_BONUS} الماس گرفتید.")
            except Exception:
                pass

    caption = "به بات شرط‌بندی خوش اومدید🌹\nاز دکمه‌های زیر استفاده کنید:"
    try:
        photo_url = "https://example.com/start_photo.jpg"  # آدرس عکس را جایگزین کنید
        bot.send_photo(
            message.chat.id,
            photo=photo_url,
            caption=caption,
            reply_markup=main_menu_markup()
        )
    except Exception:
        safe_send_message(message.chat.id, caption, reply_markup=main_menu_markup())

# ================== کالبک منوی اصلی ==================
@bot.callback_query_handler(func=lambda call: call.data == "mainmenu")
def main_menu(call):
    bot.answer_callback_query(call.id)
    caption = "به بات شرط‌بندی خوش اومدید🌹\nاز دکمه‌های زیر استفاده کنید:"
    try:
        photo_url = "https://example.com/start_photo.jpg"
        bot.send_photo(
            call.message.chat.id,
            photo=photo_url,
            caption=caption,
            reply_markup=main_menu_markup()
        )
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        safe_edit_message(
            caption,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu_markup()
        )

# ================== بخش حساب کاربری ==================
@bot.callback_query_handler(func=lambda call: call.data == "showaccount")
def handle_show_account(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    _, username, diamonds, referred_by, ref_count, loan_balance, last_spin = user
    text = (
        f"👤 حساب کاربری\n"
        f"نام: {get_display_name(call.from_user)}\n"
        f"آیدی عددی: {user_id}\n"
        f"💎 موجودی الماس: {diamonds}\n"
        f"👥 تعداد زیرمجموعه (رفرال): {ref_count}\n"
        f"💳 وام فعلی: {loan_balance} 💎 (از سقف {LOAN_MAX})"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💸 انتقال الماس", callback_data=f"acctransfer|{user_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================== بخش زیرمجموعه ==================
@bot.callback_query_handler(func=lambda call: call.data == "showreferral")
def handle_show_referral(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    ref_count = user[4]
    link = f"https://t.me/{bot.get_me().username}?start={user_id}"
    text = (
        f"👥 زیرمجموعه‌گیری\n"
        f"تعداد زیرمجموعه‌های شما: {ref_count}\n"
        f"پاداش هر زیرمجموعه: {REFERRAL_BONUS} 💎\n\n"
        f"لینک اختصاصی شما:\n{link}"
    )
    markup = back_to_main_menu_markup()
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================== بخش وام ==================
@bot.callback_query_handler(func=lambda call: call.data == "loanmenu")
def loan_menu(call):
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return

    current = get_loan_balance(user_id)
    remaining = LOAN_MAX - current

    if remaining <= 0:
        markup = back_to_main_menu_markup()
        safe_edit_message(
            f"💰 وام الماس\n\n"
            f"💳 وام فعلی شما: {current} 💎 (از سقف {LOAN_MAX})\n"
            f"⛔ به سقف مجاز رسیدی.\n"
            f"با بردن شرط یا کازینو، ۱۰٪ اضافه از هر برد کسر و بدهیت کم میشه؛ بعدش دوباره می‌تونی وام بگیری.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup
        )
        return

    # اضافه کردن دکمه منوی اصلی زیر پیام درخواست مبلغ
    safe_edit_message(
        f"💰 وام الماس\n\n"
        f"💳 وام فعلی شما: {current} 💎 (از سقف {LOAN_MAX})\n"
        f"✅ می‌تونی تا {remaining} 💎 دیگه وام بگیری.\n\n"
        f"⚠️ نکته: تا وقتی وامت صفر نشه، از هر برد شرط یا کازینو ۱۰٪ اضافه "
        f"(در کنار ۱۰٪ مالیات همیشگی) بابت بازپرداخت وام کسر میشه.\n\n"
        f"مبلغی که می‌خوای وام بگیری رو به عدد بفرست:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_to_main_menu_markup()  # تغییر: اضافه کردن دکمه منو
    )
    bot.register_next_step_handler(call.message, loan_amount_step, user_id, remaining)

def loan_amount_step(message, expected_user_id, remaining):
    if message.from_user.id != expected_user_id:
        return

    if not message.text or not message.text.strip().isdigit():
        msg = bot.reply_to(message, "لطفاً فقط عدد بفرست. مثال: 100000")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return

    amount = int(message.text.strip())
    if amount <= 0:
        msg = bot.reply_to(message, "مبلغ باید بزرگتر از صفر باشه. دوباره بفرست:")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return
    if amount > remaining:
        msg = bot.reply_to(message, f"حداکثر می‌تونی {remaining} 💎 وام بگیری. یه عدد کمتر یا مساوی بفرست:")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return

    update_diamonds(expected_user_id, amount)
    change_loan_balance(expected_user_id, amount)
    markup = back_to_main_menu_markup()
    bot.reply_to(
        message,
        f"✅ {amount} 💎 وام گرفتی و به موجودیت اضافه شد.\n"
        f"💳 مجموع وام فعلی: {get_loan_balance(expected_user_id)} 💎\n"
        f"💰 موجودی جدید: {get_balance(expected_user_id)} 💎",
        reply_markup=markup
    )

# ================== بخش گردونه ==================
@bot.callback_query_handler(func=lambda call: call.data == "spinwheel")
def spin_wheel(call):
    user_id = call.from_user.id
    if not get_user(user_id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return

    now = int(time.time())
    last_spin = get_last_spin(user_id)
    cooldown = SPIN_COOLDOWN_HOURS * 3600
    elapsed = now - last_spin

    if elapsed < cooldown:
        remaining = cooldown - elapsed
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        bot.answer_callback_query(
            call.id,
            f"⏳ گردونه هر {SPIN_COOLDOWN_HOURS} ساعت یه‌بار قابل چرخوندنه.\n"
            f"تا نوبت بعدی: {hours} ساعت و {minutes} دقیقه مونده.",
            show_alert=True
        )
        return

    won = random.randint(SPIN_MIN, SPIN_MAX)
    update_diamonds(user_id, won)
    set_last_spin(user_id, now)

    bot.answer_callback_query(call.id)
    markup = back_to_main_menu_markup()
    safe_edit_message(
        f"🎡 گردونه چرخید!\n"
        f"💎 تبریک، {won} الماس بردی!\n"
        f"💰 موجودی جدید: {get_balance(user_id)} 💎\n\n"
        f"⏱ نوبت بعدی: {SPIN_COOLDOWN_HOURS} ساعت دیگه",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup
    )

# ================== بخش راهنما ==================
@bot.callback_query_handler(func=lambda call: call.data == "showhelp")
def handle_show_help(call):
    bot.answer_callback_query(call.id)
    text = (
        "📖 راهنمای استفاده از بات\n\n"
        "💎 دیدن موجودی خودت:\n"
        "بنویس: موجودی\n\n"
        "💎 دیدن موجودی دیگران:\n"
        "روی پیام شخص مورد نظر ریپلای کن و بنویس: موجودی\n\n"
        "💸 انتقال الماس به یه نفر دیگه:\n"
        "روی پیام همون شخص توی گروه ریپلای کن و بنویس:\n"
        "انتقال الماس <مقدار>\n"
        "مثال: انتقال الماس 200\n\n"
        "🎲 شرط‌بندی با یه نفر دیگه:\n"
        "بنویس: شرط بندی <مقدار>\n"
        "مثال: شرط بندی 20\n"
        "بعد از زیر پیام دکمه‌ها استفاده کن (لغو شرط / پیوستن / شرط با ربات)\n"
        "⏱ اگه ۶۰ ثانیه کسی نپیونده، شرط خودکار لغو و پول برمی‌گرده.\n\n"
        "🎰 کازینو (تاس/دارت/بولینگ/بسکتبال/فوتبال/اسلات):\n"
        "بنویس: کازینو\n"
        "بازی و مبلغ رو انتخاب کن، یه نفر دیگه پیوستن بزنه.\n"
        "⏱ اینجا هم ۶۰ ثانیه فرصت پیوستن هست.\n\n"
        "👤 حساب کاربری کامل + دکمه انتقال:\n"
        "بزن /account\n\n"
        "👥 لینک رفرال برای دعوت دوستات:\n"
        f"بزن /start و روی «زیرمجموعه‌گیری» بزن (پاداش هر زیرمجموعه: {REFERRAL_BONUS} 💎)\n\n"
        "💰 وام الماس:\n"
        f"از /start روی «وام الماس» بزن، تا سقف {LOAN_MAX} 💎 می‌تونی قرض بگیری.\n"
        "تا وقتی وامت صفر نشه، از هر برد شرط یا کازینو ۱۰٪ اضافه (در کنار ۱۰٪ مالیات همیشگی) بابت بازپرداخت کسر میشه.\n\n"
        "🎡 گردونه الماس:\n"
        f"از /start روی «گردونه الماس» بزن و بین {SPIN_MIN} تا {SPIN_MAX} 💎 شانسی ببر (هر {SPIN_COOLDOWN_HOURS} ساعت یه‌بار).\n\n"
        "🤖 بازی با ربات تو کازینو:\n"
        "بعد از انتخاب مبلغ، به‌جای «پیوستن»، «بازی با ربات» رو بزن؛ خودت ایموجی رو بنداز، ربات هم می‌ندازه و نتیجه اعلام میشه.\n\n"
        "🏆 رتبه‌بندی برترین‌ها:\n"
        "بزن /rank یا بنویس رنک"
    )
    markup = back_to_main_menu_markup()
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================== بخش انتقال الماس (با دکمه برگشت) ==================
@bot.callback_query_handler(func=lambda call: call.data.startswith("acctransfer|"))
def handle_account_transfer_button(call):
    owner_id = int(call.data.split("|")[1])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    
    text = (
        "🔹 روش ساده‌تر (توصیه می‌شه):\n"
        "توی گروه، روی پیام کاربر مقصد ریپلای کن و بنویس:\n"
        "انتقال الماس <مقدار>\n"
        "(مثال: انتقال الماس 200)\n\n"
        "🔹 یا از همینجا:\n"
        "آیدی عددی مقصد و مقدار الماس رو اینطوری بفرست:\n"
        "<آیدی عددی> <مقدار>\n"
        "مثال: 123456789 20\n\n"
        "⚠️ نکته: کاربر مقصد باید حتماً یه‌بار خودش توی پیوی بات /start زده باشه، "
        "وگرنه بات نمی‌تونه بشناستش و همیشه خطای «استارت نزده» می‌ده."
    )
    markup = back_to_main_menu_markup()
    safe_edit_message(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup
    )
    bot.register_next_step_handler(call.message, ask_transfer_target, owner_id)

def ask_transfer_target(message, owner_id):
    if message.from_user.id != owner_id:
        bot.register_next_step_handler(message, ask_transfer_target, owner_id)
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        msg = bot.reply_to(message, "فرمت اشتباهه. دوباره اینطوری بفرست:\n<آیدی عددی مقصد> <مقدار>\nمثال: 123456789 20")
        bot.register_next_step_handler(msg, ask_transfer_target, owner_id)
        return

    target_id, amount = int(parts[0]), int(parts[1])
    ok, result_msg = perform_transfer(owner_id, target_id, amount)

    if not ok and "نزده" in result_msg:
        result_msg += (
            "\n\n⚠️ برای اینکه بات بتونه کاربری رو بشناسه، اون شخص باید حتماً یه‌بار "
            "توی پیوی خودِ بات دستور /start رو بزنه. فقط عضو گروه بودن کافی نیست."
        )
    markup = back_to_main_menu_markup()
    bot.reply_to(message, result_msg, reply_markup=markup)

    if ok:
        try:
            bot.send_message(target_id, f"💎 {amount} الماس از طرف کاربر {owner_id} برات واریز شد.")
        except Exception:
            pass

# ================== دستورات ادمین ==================
@bot.message_handler(func=lambda m: m.text and re.search(r"افزودن\s*الماس\s*(\d+)", m.text))
def text_add_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس اضافه کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nافزودن الماس <مقدار>\nمثال: افزودن الماس 50")
        return

    match = re.search(r"افزودن\s*الماس\s*(\d+)", message.text)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = int(match.group(1))
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    update_diamonds(target_id, amount)
    markup = back_to_main_menu_markup()
    bot.reply_to(message, f"✅ {amount} 💎 به کاربر {target_id} اضافه شد.\nموجودی فعلی: {get_balance(target_id)} 💎", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and re.search(r"کم\s*کردن\s*الماس\s*(\d+)", m.text))
def text_remove_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس کم کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nکم کردن الماس <مقدار>\nمثال: کم کردن الماس 50")
        return

    match = re.search(r"کم\s*کردن\s*الماس\s*(\d+)", message.text)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = int(match.group(1))
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    deduct = min(amount, get_balance(target_id))
    update_diamonds(target_id, -deduct)
    markup = back_to_main_menu_markup()
    bot.reply_to(message, f"✅ {deduct} 💎 از کاربر {target_id} کم شد.\nموجودی فعلی: {get_balance(target_id)} 💎", reply_markup=markup)

# ================== شرط متنی ==================
def check_bet_timeout(bet_id):
    bet = get_bet(bet_id)
    if not bet:
        return
    _, creator_id, creator_name, amount, chat_id, message_id, status = bet
    if status != "pending":
        return

    update_diamonds(creator_id, amount)
    set_bet_status(bet_id, "timeout")
    markup = back_to_main_menu_markup()
    safe_edit_message(
        f"⏱ زمان تموم شد!\n"
        f"هیچ‌کس ظرف {JOIN_TIMEOUT_SECONDS} ثانیه به شرط {creator_name} نپیوست.\n"
        f"💎 مبلغ ({amount}) به سازنده برگردونده شد.",
        chat_id=chat_id, message_id=message_id, reply_markup=markup,
    )

def start_bet_flow(message, amount):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول /start بزن.")
        return

    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return
    if get_balance(user_id) < amount:
        bot.reply_to(message, "موجودی الماس کافی نداری.")
        return

    creator_name = get_display_name(message.from_user)
    update_diamonds(user_id, -amount)

    sent = safe_send_message(
        message.chat.id,
        f"🎲 شرط جدید شروع شد!\n"
        f"👤 سازنده: {creator_name}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"یه نفر باید ظرف {JOIN_TIMEOUT_SECONDS} ثانیه بپیونده تا شرط اجرا بشه.",
    )
    if not sent:
        return

    bet_id = create_bet(user_id, creator_name, amount, message.chat.id, sent.message_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو شرط", callback_data=f"cancel|{bet_id}"),
        types.InlineKeyboardButton("✅ پیوستن به شرط", callback_data=f"join|{bet_id}"),
    )
    markup.row(types.InlineKeyboardButton("🤖 شرط با ربات", callback_data=f"bot|{bet_id}"))
    markup.row(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
    safe_edit_message(sent.text, chat_id=message.chat.id, message_id=sent.message_id, reply_markup=markup)

    threading.Timer(JOIN_TIMEOUT_SECONDS, check_bet_timeout, args=[bet_id]).start()

@bot.message_handler(commands=["bet", "شرط"])
def cmd_bet(message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "استفاده درست: /bet <مقدار>\nمثال: /bet 20")
        return
    start_bet_flow(message, int(parts[1]))

@bot.message_handler(func=lambda m: m.text and re.search(r"شرط\s*بندی?\s+(\d+)", m.text))
def text_bet(message):
    match = re.search(r"شرط\s*بندی?\s+(\d+)", message.text)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است. مثال: شرط بندی 20")
        return
    amount = int(match.group(1))
    start_bet_flow(message, amount)

def resolve_bet(bet_id, opponent_id, opponent_name, is_bot=False):
    bet = get_bet(bet_id)
    _, creator_id, creator_name, amount, chat_id, message_id, status = bet

    winner_is_creator = random.random() < 0.5
    pool = 2 * amount

    if winner_is_creator:
        winner_name, winner_id = creator_name, creator_id
        loser_name, loser_id = opponent_name, opponent_id
    else:
        winner_name, winner_id = opponent_name, opponent_id
        loser_name, loser_id = creator_name, creator_id

    real_winner_id = None if (winner_id is None) else winner_id
    if real_winner_id is not None:
        payout, tax, loan_repay = calculate_payout(real_winner_id, pool)
        update_diamonds(real_winner_id, payout)
        if get_user(TAX_RECEIVER_ID):
            update_diamonds(TAX_RECEIVER_ID, tax)
    else:
        payout = 0
        tax = int(pool * TAX_RATE)
        loan_repay = 0

    set_bet_status(bet_id, "finished")

    winner_id_str = str(winner_id) if winner_id is not None else "—"
    loser_id_str = str(loser_id) if loser_id is not None else "—"

    extra_line = f"💳 کسر بابت وام (۱۰٪): {loan_repay}\n" if loan_repay > 0 else ""
    text = (
        f"🎲 شرط تموم شد!\n"
        f"💎 مبلغ: {amount}\n"
        f"⚔️ {creator_name} در برابر {opponent_name}\n\n"
        f"🏆 برنده: {winner_name} (آیدی: {winner_id_str})\n"
        f"😢 بازنده: {loser_name} (آیدی: {loser_id_str})\n\n"
        f"💰 مبلغ برد: {pool}\n"
        f"🏛 مالیات ({int(TAX_RATE*100)}٪): {tax}\n"
        f"{extra_line}"
        f"✅ مبلغ نهایی برنده: {payout if real_winner_id is not None else '—'}"
    )
    markup = back_to_main_menu_markup()
    safe_edit_message(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)

@bot.callback_query_handler(
    func=lambda call: call.data == "pending" or call.data.split("|")[0] in ("cancel", "join", "bot")
)
def handle_callback(call):
    if call.data == "pending":
        bot.answer_callback_query(call.id, "لطفاً چند لحظه صبر کن...")
        return

    action, bet_id_str = call.data.split("|")
    bet_id = int(bet_id_str)
    bet = get_bet(bet_id)

    if not bet:
        bot.answer_callback_query(call.id, "این شرط پیدا نشد.", show_alert=True)
        return

    _, creator_id, creator_name, amount, chat_id, message_id, status = bet

    if status != "pending":
        bot.answer_callback_query(call.id, "این شرط قبلاً تموم شده.", show_alert=True)
        return

    clicker_id = call.from_user.id
    clicker_name = get_display_name(call.from_user)

    if action == "cancel":
        if clicker_id != creator_id:
            bot.answer_callback_query(call.id, "فقط سازنده شرط می‌تونه لغو کنه.", show_alert=True)
            return
        update_diamonds(creator_id, amount)
        set_bet_status(bet_id, "cancelled")
        markup = back_to_main_menu_markup()
        safe_edit_message(
            f"❌ شرط توسط {creator_name} لغو شد.\nمبلغ ({amount} 💎) برگردونده شد.",
            chat_id=chat_id, message_id=message_id, reply_markup=markup,
        )
        bot.answer_callback_query(call.id, "شرط لغو شد.")
        return

    if action == "join":
        if clicker_id == creator_id:
            bot.answer_callback_query(call.id, "سازنده حق شرکت در شرط خودش رو نداره!", show_alert=True)
            return
        if not get_user(clicker_id):
            bot.answer_callback_query(call.id, "شما باید ابتدا در بات /start را بزنید.", show_alert=True)
            return
        if get_balance(clicker_id) < amount:
            bot.answer_callback_query(call.id, "موجودی کافی برای پیوستن به این شرط ندارید.", show_alert=True)
            return

        update_diamonds(clicker_id, -amount)
        resolve_bet(bet_id, clicker_id, clicker_name, is_bot=False)
        bot.answer_callback_query(call.id, "شما به شرط پیوستید. نتیجه اعلام شد.")
        return

    if action == "bot":
        if clicker_id != creator_id:
            bot.answer_callback_query(call.id, "فقط سازنده می‌تونه با ربات شرط ببنده.", show_alert=True)
            return
        resolve_bet(bet_id, None, "ربات", is_bot=True)
        bot.answer_callback_query(call.id, "شرط با ربات شروع شد. نتیجه اعلام شد.")
        return

# ================== بخش کازینو (با تنظیمات دقیق) ==================
CASINO_GAMES = {
    "dice": "🎲",
    "dart": "🎯",
    "basket": "🏀",
    "football": "⚽",
    "bowling": "🎳",
    "slot": "🎰",
}
CASINO_GAME_NAMES = {
    "dice": "تاس",
    "dart": "دارت",
    "basket": "بسکتبال",
    "football": "فوتبال",
    "bowling": "بولینگ",
    "slot": "اسلات",
}
CASINO_BET_PRESETS = [10, 50, 100, 500, 1000]

def casino_games_keyboard():
    """صفحه اول کازینو - لیست بازی‌ها + دکمه بازگشت به خانه"""
    markup = types.InlineKeyboardMarkup()
    for key, emoji in CASINO_GAMES.items():
        markup.add(types.InlineKeyboardButton(f"{emoji} {CASINO_GAME_NAMES[key]}", callback_data=f"cgame|{key}"))
    # اضافه کردن دکمه بازگشت به منوی اصلی
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "casinomenu")
def casino_from_main_menu(call):
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard()
    )

@bot.message_handler(func=lambda m: m.text and m.text.strip() == "کازینو")
def casino_panel(message):
    if not get_user(message.from_user.id):
        bot.reply_to(message, "اول /start بزن.")
        return
    safe_send_message(
        message.chat.id,
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        reply_markup=casino_games_keyboard()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cgame|"))
def casino_game_select(call):
    bot.answer_callback_query(call.id)
    game_key = call.data.split("|")[1]

    markup = types.InlineKeyboardMarkup()
    markup.row(*[
        types.InlineKeyboardButton(f"💎 {amt}", callback_data=f"cbet|{game_key}|{amt}")
        for amt in CASINO_BET_PRESETS[:3]
    ])
    markup.row(*[
        types.InlineKeyboardButton(f"💎 {amt}", callback_data=f"cbet|{game_key}|{amt}")
        for amt in CASINO_BET_PRESETS[3:]
    ])
    markup.row(types.InlineKeyboardButton("✏️ مبلغ دلخواه", callback_data=f"ccustom|{game_key}"))
    markup.row(types.InlineKeyboardButton("🔙 بازگشت", callback_data="cback"))

    safe_edit_message(
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]} انتخاب شد.\n💎 مبلغ شرط رو انتخاب کن:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "cback")
def casino_back(call):
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=casino_games_keyboard()
    )

def cancel_casino_timer(msg_id):
    with casino_lock:
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

def check_casino_timeout(msg_id):
    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game or game["player2"] is not None:
            return
        update_diamonds(game["player1"]["id"], game["bet"])
        del active_casino_games[msg_id]
        if msg_id in casino_timers:
            del casino_timers[msg_id]

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
    safe_edit_message(
        f"⏱ زمان تموم شد!\n"
        f"هیچ‌کس ظرف {JOIN_TIMEOUT_SECONDS} ثانیه به بازی {game['player1']['name']} نپیوست.\n"
        f"💎 مبلغ ({game['bet']}) به سازنده برگردونده شد.",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cbet|"))
def casino_bet_select(call):
    _, game_key, amount = call.data.split("|")
    amount = int(amount)
    user = call.from_user

    if not get_user(user.id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    if get_balance(user.id) < amount:
        bot.answer_callback_query(call.id, "💎 موجودی الماس شما کافی نیست!", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    update_diamonds(user.id, -amount)

    # صفحه منتظر حریف با سه دکمه (بدون برگشت)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو بازی", callback_data=f"ccancel|{call.message.message_id}"),
        types.InlineKeyboardButton("✅ پیوستن به بازی", callback_data="cjoin"),
    )
    markup.row(types.InlineKeyboardButton("🤖 بازی با ربات", callback_data="cbotplay"))

    text = (
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"👤 بازیکن اول: {get_display_name(user)}\n"
        f"⏳ منتظر بازیکن دوم یا شروع بازی با ربات... (فرصت پیوستن: {JOIN_TIMEOUT_SECONDS} ثانیه)"
    )
    safe_edit_message(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    msg_id = call.message.message_id

    with casino_lock:
        active_casino_games[msg_id] = {
            "game": game_key,
            "bet": amount,
            "chat_id": call.message.chat.id,
            "player1": {"id": user.id, "name": get_display_name(user)},
            "player2": None,
            "score1": None,
            "score2": None,
            "vs_bot": False,
        }
        timer = threading.Timer(JOIN_TIMEOUT_SECONDS, check_casino_timeout, args=[msg_id])
        casino_timers[msg_id] = timer
        timer.start()

# ================== لغو بازی کازینو ==================
@bot.callback_query_handler(func=lambda call: call.data.startswith("ccancel|"))
def casino_cancel(call):
    msg_id = int(call.data.split("|")[1])
    user_id = call.from_user.id

    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game:
            bot.answer_callback_query(call.id, "این بازی دیگه معتبر نیست.", show_alert=True)
            return
        if game["player2"] is not None:
            bot.answer_callback_query(call.id, "بازی شروع شده، نمی‌توان لغو کرد.", show_alert=True)
            return
        if user_id != game["player1"]["id"]:
            bot.answer_callback_query(call.id, "فقط سازنده می‌تونه بازی رو لغو کنه.", show_alert=True)
            return

        update_diamonds(game["player1"]["id"], game["bet"])
        del active_casino_games[msg_id]
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

    bot.answer_callback_query(call.id, "بازی لغو شد.")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
    safe_edit_message(
        f"❌ بازی لغو شد.\n💎 مبلغ ({game['bet']}) به شما برگردانده شد.",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

# ================== بازگشت به لیست کازینو ==================
@bot.callback_query_handler(func=lambda call: call.data == "casinoback")
def casino_back_to_list(call):
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard()
    )

# ================== مبلغ دلخواه کازینو ==================
@bot.callback_query_handler(func=lambda call: call.data.startswith("ccustom|"))
def casino_custom_amount_prompt(call):
    game_key = call.data.split("|")[1]
    if not get_user(call.from_user.id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "✏️ لطفاً مبلغ شرط رو به عدد بفرست (مثلاً 250):",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None
    )
    bot.register_next_step_handler(call.message, casino_custom_amount_step, game_key, call.from_user.id, call.message.message_id)

def casino_custom_amount_step(message, game_key, expected_user_id, panel_msg_id):
    if message.from_user.id != expected_user_id:
        return

    if not message.text or not message.text.strip().isdigit():
        msg = bot.reply_to(message, "لطفاً فقط عدد بفرست. مثال: 250")
        bot.register_next_step_handler(msg, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id)
        return

    amount = int(message.text.strip())
    if amount <= 0:
        msg = bot.reply_to(message, "مبلغ باید بزرگتر از صفر باشه. دوباره بفرست:")
        bot.register_next_step_handler(msg, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id)
        return

    user = message.from_user
    if get_balance(user.id) < amount:
        bot.reply_to(message, "💎 موجودی الماس شما کافی نیست!")
        return

    update_diamonds(user.id, -amount)

    # صفحه منتظر حریف با سه دکمه (بدون برگشت)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو بازی", callback_data=f"ccancel|{panel_msg_id}"),
        types.InlineKeyboardButton("✅ پیوستن به بازی", callback_data="cjoin"),
    )
    markup.row(types.InlineKeyboardButton("🤖 بازی با ربات", callback_data="cbotplay"))

    text = (
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"👤 بازیکن اول: {get_display_name(user)}\n"
        f"⏳ منتظر بازیکن دوم یا شروع بازی با ربات... (فرصت پیوستن: {JOIN_TIMEOUT_SECONDS} ثانیه)"
    )
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    safe_edit_message(text, chat_id=message.chat.id, message_id=panel_msg_id, reply_markup=markup)
    msg_id = panel_msg_id

    with casino_lock:
        active_casino_games[msg_id] = {
            "game": game_key,
            "bet": amount,
            "chat_id": message.chat.id,
            "player1": {"id": user.id, "name": get_display_name(user)},
            "player2": None,
            "score1": None,
            "score2": None,
            "vs_bot": False,
        }
        timer = threading.Timer(JOIN_TIMEOUT_SECONDS, check_casino_timeout, args=[msg_id])
        casino_timers[msg_id] = timer
        timer.start()

# ================== پیوستن به بازی کازینو ==================
@bot.callback_query_handler(func=lambda call: call.data == "cjoin")
def casino_join(call):
    msg_id = call.message.message_id
    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game:
            bot.answer_callback_query(call.id, "این بازی دیگه معتبر نیست.", show_alert=True)
            return
        if game["player2"] is not None:
            bot.answer_callback_query(call.id, "بازی قبلاً پر شده.", show_alert=True)
            return

        user = call.from_user
        if user.id == game["player1"]["id"]:
            bot.answer_callback_query(call.id, "نمی‌تونی با خودت بازی کنی!", show_alert=True)
            return
        if not get_user(user.id):
            bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
            return
        if get_balance(user.id) < game["bet"]:
            bot.answer_callback_query(call.id, "💎 موجودی الماس شما کافی نیست!", show_alert=True)
            return

        game["player2"] = {"id": user.id, "name": get_display_name(user)}
        update_diamonds(user.id, -game["bet"])
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

    bot.answer_callback_query(call.id)
    emoji = CASINO_GAMES[game["game"]]
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
    safe_edit_message(
        f"{emoji} بازی شروع شد!\n"
        f"⚔️ {game['player1']['name']} در برابر {game['player2']['name']}\n"
        f"💎 مبلغ: {game['bet']}\n\n"
        f"🎲 حالا هر دو بازیکن باید خودشون {emoji} رو همینجا تو چت بفرستن!",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

# ================== بازی با ربات کازینو ==================
@bot.callback_query_handler(func=lambda call: call.data == "cbotplay")
def casino_play_vs_bot(call):
    msg_id = call.message.message_id
    with casino_lock:
        game = active_casino_games.get(msg_id)
        if not game:
            bot.answer_callback_query(call.id, "این بازی دیگه معتبر نیست.", show_alert=True)
            return
        if game["player2"] is not None:
            bot.answer_callback_query(call.id, "این بازی قبلاً شروع شده.", show_alert=True)
            return
        if call.from_user.id != game["player1"]["id"]:
            bot.answer_callback_query(call.id, "فقط سازنده می‌تونه با ربات بازی کنه.", show_alert=True)
            return

        game["player2"] = {"id": None, "name": "🤖 ربات"}
        game["vs_bot"] = True
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]

    bot.answer_callback_query(call.id)
    emoji = CASINO_GAMES[game["game"]]
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
    safe_edit_message(
        f"{emoji} بازی با ربات شروع شد!\n"
        f"⚔️ {game['player1']['name']} در برابر 🤖 ربات\n"
        f"💎 مبلغ: {game['bet']}\n\n"
        f"🎲 حالا خودت {emoji} رو همینجا تو چت بفرست، بعدش ربات هم می‌ندازه!",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

# ================== دریافت دایس و نتیجه نهایی کازینو ==================
@bot.message_handler(content_types=["dice"])
def handle_dice_throw(message):
    to_finalize = None
    bot_throw_needed = None

    with casino_lock:
        for msg_id, game in active_casino_games.items():
            if game["chat_id"] != message.chat.id or game["player2"] is None:
                continue
            emoji = CASINO_GAMES[game["game"]]
            if message.dice.emoji != emoji:
                continue

            user_id = message.from_user.id
            vs_bot = game.get("vs_bot", False)

            if user_id == game["player1"]["id"] and game["score1"] is None:
                game["score1"] = message.dice.value
            elif (not vs_bot) and user_id == game["player2"]["id"] and game["score2"] is None:
                game["score2"] = message.dice.value
            else:
                continue

            if vs_bot and game["score1"] is not None and game["score2"] is None:
                bot_throw_needed = (msg_id, game["chat_id"], emoji)
            elif game["score1"] is not None and game["score2"] is not None:
                to_finalize = msg_id
            break

    if bot_throw_needed:
        b_msg_id, b_chat_id, b_emoji = bot_throw_needed
        bot_dice = bot.send_dice(b_chat_id, emoji=b_emoji)
        with casino_lock:
            game = active_casino_games.get(b_msg_id)
            if game and game["score2"] is None:
                game["score2"] = bot_dice.dice.value
                if game["score1"] is not None and game["score2"] is not None:
                    to_finalize = b_msg_id

    if to_finalize:
        finalize_casino_game(to_finalize)

def finalize_casino_game(msg_id):
    with casino_lock:
        game = active_casino_games.pop(msg_id, None)
        if msg_id in casino_timers:
            casino_timers[msg_id].cancel()
            del casino_timers[msg_id]
    if not game:
        return

    emoji = CASINO_GAMES[game["game"]]
    chat_id = game["chat_id"]
    player1, player2 = game["player1"], game["player2"]
    score1, score2 = game["score1"], game["score2"]
    bet = game["bet"]
    vs_bot = game.get("vs_bot", False)

    score1 = score1 if score1 is not None else 0
    score2 = score2 if score2 is not None else 0

    if score1 == score2:
        update_diamonds(player1["id"], bet)
        if not vs_bot:
            update_diamonds(player2["id"], bet)
        result_text = (
            f"{emoji} شرط تموم شد!\n"
            f"💎 مبلغ: {bet}\n"
            f"⚔️ {player1['name']} در برابر {player2['name']}\n\n"
            f"🤝 مساوی شد! (امتیاز: {score1} - {score2})\n"
            f"💰 مبلغ به {'سازنده' if vs_bot else 'هر دو نفر'} برگشت داده شد."
        )
    else:
        if score1 > score2:
            winner, loser, w_score, l_score = player1, player2, score1, score2
        else:
            winner, loser, w_score, l_score = player2, player1, score2, score1

        total_pot = bet * 2
        winner_id = winner["id"]

        if winner_id is not None:
            final_amount, tax, loan_repay = calculate_payout(winner_id, total_pot)
            update_diamonds(winner_id, final_amount)
            if get_user(TAX_RECEIVER_ID):
                update_diamonds(TAX_RECEIVER_ID, tax)
        else:
            final_amount = None
            loan_repay = 0
            tax = int(total_pot * TAX_RATE)

        extra_line = f"💳 کسر بابت وام (۱۰٪): {loan_repay}\n" if loan_repay > 0 else ""
        result_text = (
            f"{emoji} شرط تموم شد!\n"
            f"💎 مبلغ: {bet}\n"
            f"⚔️ {player1['name']} در برابر {player2['name']}\n\n"
            f"🏆 برنده: {winner['name']} (امتیاز: {w_score})\n"
            f"😢 بازنده: {loser['name']} (امتیاز: {l_score})\n\n"
            f"💰 مبلغ برد: {total_pot}\n"
            f"🏛 مالیات ({int(TAX_RATE*100)}٪): {tax}\n"
            f"{extra_line}"
            f"✅ مبلغ نهایی برنده: {final_amount if final_amount is not None else '—'}"
        )

    # **صفحه نتیجه: بدون هیچ دکمه‌ای**
    safe_edit_message(result_text, chat_id=chat_id, message_id=msg_id, reply_markup=None)

# ================== رتبه‌بندی ==================
@bot.message_handler(commands=["rank", "رتبه‌بندی"])
def cmd_rank(message):
    top = get_top_users()
    if not top:
        bot.reply_to(message, "هنوز کاربری ثبت‌نام نکرده.")
        return

    text = "🏆 رتبه‌بندی بر اساس الماس\n\n"
    for idx, (user_id, username, diamonds) in enumerate(top, 1):
        name = f"{username}" if username else f"کاربر {user_id}"
        text += f"{idx}. {name} — 💎 {diamonds}\n"

    markup = back_to_main_menu_markup()
    bot.reply_to(message, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["رنک", "رتبه بندی"])
def text_rank(message):
    cmd_rank(message)

# ================== موجودی ==================
@bot.message_handler(commands=["balance", "موجودی"])
def cmd_balance(message):
    show_balance(message)

@bot.message_handler(func=lambda m: m.text and m.text.strip() == "موجودی")
def text_balance(message):
    show_balance(message)

def show_balance(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        if not get_user(target_id):
            bot.reply_to(message, "این کاربر هنوز /start نزده، نمی‌تونم موجودیش رو ببینم.")
            return
        target_name = get_display_name(message.reply_to_message.from_user)
        balance = get_balance(target_id)
        text = f"💎 موجودی الماس {target_name}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"💎 {balance}", callback_data="pending"))
        markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
        bot.reply_to(message, text, reply_markup=markup)
    else:
        balance = get_balance(user_id)
        text = "💎 موجودی شما"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"💎 {balance}", callback_data="pending"))
        markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
        bot.reply_to(message, text, reply_markup=markup)

# ================== Webhook ==================
@app.route("/", methods=["GET"])
def health_check():
    return "Bot is running!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    return "", 403

# ================== اجرا ==================
if __name__ == "__main__":
    WEBHOOK_URL = "https://bet-bot-e1c2.onrender.com/webhook"  # آدرس خود را جایگزین کنید
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)