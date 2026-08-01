# -*- coding: utf-8 -*-
"""
بات شرط‌بندی کامل (الماس + شرط دو نفره + گردونه شانس Slot)
مناسب برای Render (Webhook)
"""

import sqlite3
import random
import re
import os
import time
from flask import Flask, request
import telebot
from telebot import types

# ================== تنظیمات ==================
BOT_TOKEN = "توکن_ربات_خودت"  # توکن خود را اینجا وارد کنید
ADMIN_IDS = [8904869158]       # آیدی ادمین‌ها
START_DIAMONDS = 10000
REFERRAL_BONUS = 50000
TAX_RATE = 0.10
TAX_RECEIVER_ID = ADMIN_IDS[0]

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
DB_PATH = "diamonds.db"


# ================== دیتابیس ==================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    # جدول کاربران
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            diamonds INTEGER DEFAULT 0,
            referred_by INTEGER,
            ref_count INTEGER DEFAULT 0
        )
    """)
    # جدول شرط‌های دو نفره
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
    # جدول اسلات (گردونه شانس) برای مدیریت تایمر ۶۰ ثانیه
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_games (
            user_id INTEGER PRIMARY KEY,
            bet_amount INTEGER,
            expire_time REAL,
            status TEXT DEFAULT 'active'
        )
    """)
    return conn


# ================== توابع پایه کاربران ==================
def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def create_user(user_id, username, referred_by=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (user_id, username, diamonds, referred_by) VALUES (?,?,?,?)",
        (user_id, username, START_DIAMONDS, referred_by),
    )
    conn.commit()
    conn.close()


def update_diamonds(user_id, amount):
    max_int = 9223372036854775807
    if amount > max_int:
        amount = max_int
    elif amount < -max_int:
        amount = -max_int
    conn = get_conn()
    conn.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def add_ref_count(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def get_balance(user_id):
    row = get_user(user_id)
    return row[2] if row else 0


def get_display_name(user):
    return user.username and f"@{user.username}" or user.first_name


# ================== توابع شرط‌های دو نفره (قدیمی) ==================
def create_bet(creator_id, creator_name, amount, chat_id, message_id):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO bets (creator_id, creator_name, amount, chat_id, message_id, status) VALUES (?,?,?,?,?,'pending')",
        (creator_id, creator_name, amount, chat_id, message_id),
    )
    conn.commit()
    bet_id = cur.lastrowid
    conn.close()
    return bet_id


def get_bet(bet_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
    conn.close()
    return row


def set_bet_status(bet_id, status):
    conn = get_conn()
    conn.execute("UPDATE bets SET status=? WHERE bet_id=?", (status, bet_id))
    conn.commit()
    conn.close()


def get_top_users(limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, username, diamonds FROM users ORDER BY diamonds DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows


# ================== هندلر استارت و منو ==================
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

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👤 حساب کاربری", callback_data=f"showaccount|{user_id}"))
    markup.add(types.InlineKeyboardButton("👥 زیرمجموعه‌گیری", callback_data=f"showreferral|{user_id}"))
    markup.add(types.InlineKeyboardButton("📖 راهنما", callback_data="showhelp"))

    caption = "به بات شرط‌بندی خوش اومدید🌹"
    bot.send_message(message.chat.id, caption, reply_markup=markup)


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
        bot.reply_to(message, text, reply_markup=markup)
    else:
        balance = get_balance(user_id)
        text = "💎 موجودی شما"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"💎 {balance}", callback_data="pending"))
        bot.reply_to(message, text, reply_markup=markup)


def build_account_view(user_id, display_name):
    user = get_user(user_id)
    _, username, diamonds, referred_by, ref_count = user
    text = (
        f"👤 حساب کاربری\n"
        f"نام: {display_name}\n"
        f"آیدی عددی: {user_id}\n"
        f"💎 موجودی الماس: {diamonds}\n"
        f"👥 تعداد زیرمجموعه (رفرال): {ref_count}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💸 انتقال الماس", callback_data=f"acctransfer|{user_id}"))
    return text, markup


# ================== رتبه‌بندی ==================
@bot.message_handler(commands=["rank", "رتبه‌بندی"])
def cmd_rank(message):
    top = get_top_users()
    if not top:
        bot.reply_to(message, "هنوز کاربری ثبت‌نام نکرده.")
        return

    text = "🏆 **رتبه‌بندی بر اساس الماس**\n\n"
    for idx, (user_id, username, diamonds) in enumerate(top, 1):
        name = f"@{username}" if username else f"کاربر {user_id}"
        text += f"{idx}. {name} — 💎 {diamonds}\n"

    bot.reply_to(message, text, parse_mode="Markdown")


# ================== گردونه شانس (جدید - شبیه تصویر شما) ==================

# منطق تولید ۳ کاراکتر و محاسبه ضریب
def slot_spin():
    items = ['🍒', '🍋', '🍇', '🔔', '7️⃣']
    result = [random.choice(items) for _ in range(3)]
    
    # محاسبه ضریب
    if result[0] == result[1] == result[2]:
        if result[0] == '7️⃣':
            multiplier = 5.0  # جکپات
        elif result[0] == '🍒':
            multiplier = 3.0
        else:
            multiplier = 2.0
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        multiplier = 1.0  # برگشت مبلغ
    else:
        multiplier = 0.0  # باخت کامل
    return result, multiplier


@bot.message_handler(commands=["slot", "اسلات"])
def cmd_slot(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not get_user(user_id):
        bot.reply_to(message, "اول باید /start رو بزنی.")
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "فرمت: /slot <مبلغ>\nمثال: /slot 10000")
        return
    
    amount = int(parts[1])
    if amount <= 0:
        bot.reply_to(message, "مبلغ باید بزرگتر از صفر باشه.")
        return
    
    balance = get_balance(user_id)
    if balance < amount:
        bot.reply_to(message, f"موجودی کافی ندارید! موجودی شما: {balance} 💎")
        return

    # چک کردن اینکه کاربر توی بازی دیگری نباشد
    conn = get_conn()
    active = conn.execute("SELECT status FROM slot_games WHERE user_id=?", (user_id,)).fetchone()
    if active and active[0] == 'active':
        conn.close()
        bot.reply_to(message, "شما هنوز بازی قبلی رو تموم نکردید! لطفاً ایموجی 🎰 رو بفرستید.")
        return

    # کسر الماس و ثبت در دیتابیس با زمان انقضا (۶۰ ثانیه)
    update_diamonds(user_id, -amount)
    expire_time = time.time() + 60
    
    conn.execute("REPLACE INTO slot_games (user_id, bet_amount, expire_time, status) VALUES (?,?,?,?)",
                 (user_id, amount, expire_time, 'active'))
    conn.commit()
    conn.close()

    # ارسال پنل اسلات (مثل تصویر اول شما)
    sent_msg = bot.send_message(
        chat_id,
        f"🎰 کازینو | گردونه شانس 🎰\n\n"
        f"💰 مبلغ ورودی: {amount}\n\n"
        f"👤 {get_display_name(message.from_user)}: امتیاز...\n\n"
        f"❗️ لطفاً ایموجی '🎰' را در پاسخ همین پیام ارسال کنید\n\n"
        f"⏳ فقط ۶۰ ثانیه فرصت دارید... (الماس شما کسر شد!)"
    )


@bot.message_handler(func=lambda message: message.text == '🎰')
def handle_slot_emoji(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    conn = get_conn()
    row = conn.execute("SELECT bet_amount, expire_time, status FROM slot_games WHERE user_id=?", (user_id,)).fetchone()
    
    if not row:
        conn.close()
        return # کاربر بازی‌ای شروع نکرده
    
    bet_amount, expire_time, status = row

    if status != 'active':
        bot.reply_to(message, "این بازی قبلاً تموم شده یا لغو شده.")
        conn.close()
        return

    # بررسی زمان (تایمر ۶۰ ثانیه‌ای)
    if time.time() > expire_time:
        conn.execute("UPDATE slot_games SET status='expired' WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        bot.reply_to(message, "⏰ متاسفانه زمان شما به پایان رسید! الماس شما سوخت.")
        return

    # ** انجام بازی اسلات **
    result, multiplier = slot_spin()
    winnings = int(bet_amount * multiplier)
    
    # به‌روزرسانی موجودی کاربر (بر اساس ضریب)
    if multiplier > 0:
        update_diamonds(user_id, winnings)
        
    # بستن بازی در دیتابیس
    conn.execute("UPDATE slot_games SET status='finished' WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

    # ساخت متن نتیجه (مثل تصویر دوم شما)
    if multiplier == 0.0:
        result_text = f"شما باختید! ({multiplier}x)\nمبلغ دریافتی: ۰"
    elif multiplier == 1.0:
        result_text = f"مساوی کردید! برگشت مبلغ ({multiplier}x)\nمبلغ دریافتی: {winnings}"
    else:
        result_text = f"🎉 برنده شدید! ({multiplier}x)\nمبلغ دریافتی: {winnings}"

    bot.send_message(
        chat_id,
        f"🎰 کازینو | گردونه شانس 🎰\n\n"
        f"💰 مبلغ ورودی: {bet_amount}\n"
        f"({multiplier}x) 🏆 مبلغ دریافتی: {winnings}\n\n"
        f"👤 {get_display_name(message.from_user)}: امتیاز...\n"
        f"{' '.join(result)} | {result_text}"
    )


# ================== سایر کالبک‌ها و توابع ==================
@bot.callback_query_handler(func=lambda call: call.data.startswith("showaccount|"))
def handle_show_account(call):
    owner_id = int(call.data.split("|")[1])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text, markup = build_account_view(owner_id, get_display_name(call.from_user))
    bot.send_message(call.message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("showreferral|"))
def handle_show_referral(call):
    owner_id = int(call.data.split("|")[1])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این بخش متعلق به تو نیست.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    user = get_user(owner_id)
    ref_count = user[4]
    link = f"https://t.me/{bot.get_me().username}?start={owner_id}"
    text = (
        f"👥 زیرمجموعه‌گیری\n"
        f"تعداد زیرمجموعه‌های شما: {ref_count}\n"
        f"پاداش هر زیرمجموعه: {REFERRAL_BONUS} 💎\n\n"
        f"لینک اختصاصی شما:\n{link}"
    )
    bot.send_message(call.message.chat.id, text)


@bot.callback_query_handler(func=lambda call: call.data == "showhelp")
def handle_show_help(call):
    bot.answer_callback_query(call.id)
    text = (
        "📖 راهنمای استفاده از بات\n\n"
        "💎 دیدن موجودی خودت:\n"
        "بنویس: موجودی\n\n"
        "💎 دیدن موجودی دیگران:\n"
        "روی پیام شخص مورد نظر ریپلای کن و بنویس: موجودی\n\n"
        "💸 انتقال الماس:\n"
        "روی پیام همون شخص ریپلای کن و بنویس:\n"
        "انتقال الماس <مقدار>\n"
        "مثال: انتقال الماس 200\n\n"
        "🎲 شرط‌بندی دو نفره:\n"
        "بنویس: /bet <مقدار>\n"
        "مثال: /bet 20\n\n"
        "🎰 گردونه شانس (اسلات):\n"
        "بنویس: /slot <مقدار>\n"
        "مثال: /slot 10000\n"
        "سپس در ۶۰ ثانیه ایموجی 🎰 رو بفرست.\n\n"
        "👤 حساب کاربری کامل:\n"
        "بزن /account\n\n"
        "👥 لینک رفرال:\n"
        "بزن /start و روی «زیرمجموعه‌گیری» بزن\n\n"
        "🏆 رتبه‌بندی:\n"
        "بزن /rank یا بنویس رنک"
    )
    bot.send_message(call.message.chat.id, text)


# ================== انتقال الماس و شرط‌بندی دو نفره ==================
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
        result_msg += ("\n\n⚠️ کاربر مقصد باید حتماً یه‌بار توی پیوی خودِ بات /start رو بزنه.")
    bot.reply_to(message, result_msg)

    if ok:
        try:
            bot.send_message(target_id, f"💎 {amount} الماس از طرف کاربر {owner_id} برات واریز شد.")
        except Exception:
            pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("acctransfer|"))
def handle_account_transfer_button(call):
    owner_id = int(call.data.split("|")[1])
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🔹 روش آسان:\nتوی گروه، روی پیام مقصد ریپلای کن و بنویس:\nانتقال الماس <مقدار>\n\n"
        "🔹 یا از همینجا:\n<آیدی عددی مقصد> <مقدار>\nمثال: 123456789 20"
    )
    bot.register_next_step_handler(msg, ask_transfer_target, owner_id)


@bot.message_handler(commands=["transfer"])
def cmd_transfer(message):
    sender_id = message.from_user.id
    if not get_user(sender_id):
        bot.reply_to(message, "اول /start بزن.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام مقصد ریپلای کن: /transfer <مقدار>")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "مثال: /transfer 20")
        return
    amount = int(parts[1])
    target_id = message.reply_to_message.from_user.id
    ok, msg = perform_transfer(sender_id, target_id, amount)
    bot.reply_to(message, msg)


@bot.message_handler(func=lambda m: m.text and re.match(r"^انتقال\s+الماس\s+\d+$", m.text.strip()))
def text_transfer(message):
    sender_id = message.from_user.id
    if not get_user(sender_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nانتقال الماس <مقدار>")
        return

    amount = int(re.search(r"\d+", message.text).group())
    target_id = message.reply_to_message.from_user.id
    ok, msg = perform_transfer(sender_id, target_id, amount)
    bot.reply_to(message, msg)


def perform_transfer(sender_id, target_id, amount):
    if amount <= 0:
        return False, "مقدار باید بزرگتر از صفر باشه."
    if target_id == sender_id:
        return False, "نمیشه به خودت انتقال بدی."
    if not get_user(target_id):
        return False, "کاربر مقصد هنوز /start نزده."
    if get_balance(sender_id) < amount:
        return False, "موجودی کافی نداری."
    update_diamonds(sender_id, -amount)
    update_diamonds(target_id, amount)
    return True, f"✅ {amount} 💎 به کاربر {target_id} منتقل شد.\nموجودی جدید تو: {get_balance(sender_id)} 💎"


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

    sent = bot.send_message(
        message.chat.id,
        f"🎲 شرط جدید شروع شد!\n"
        f"👤 سازنده: {creator_name}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"یه نفر باید بپیونده تا شرط اجرا بشه.",
    )

    bet_id = create_bet(user_id, creator_name, amount, message.chat.id, sent.message_id)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو شرط", callback_data=f"cancel|{bet_id}"),
        types.InlineKeyboardButton("✅ پیوستن به شرط", callback_data=f"join|{bet_id}"),
    )
    markup.row(types.InlineKeyboardButton("🤖 شرط با ربات", callback_data=f"bot|{bet_id}"))
    bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=sent.message_id, reply_markup=markup)


@bot.message_handler(commands=["bet", "شرط"])
def cmd_bet(message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "استفاده درست: /bet <مقدار>\nمثال: /bet 20")
        return
    start_bet_flow(message, int(parts[1]))


@bot.message_handler(func=lambda m: m.text and re.match(r"^شرط\s*بندی?\s+\d+$", m.text.strip()))
def text_bet(message):
    amount = int(re.search(r"\d+", message.text).group())
    start_bet_flow(message, amount)


def resolve_bet(bet_id, opponent_id, opponent_name, is_bot=False):
    bet = get_bet(bet_id)
    _, creator_id, creator_name, amount, chat_id, message_id, status = bet

    winner_is_creator = random.random() < 0.5
    pool = 2 * amount
    tax = int(pool * TAX_RATE)
    payout = pool - tax

    if winner_is_creator:
        update_diamonds(creator_id, payout)
        winner_name, winner_id = creator_name, creator_id
        loser_name, loser_id = opponent_name, opponent_id
    else:
        if not is_bot:
            update_diamonds(opponent_id, payout)
        winner_name, winner_id = opponent_name, opponent_id
        loser_name, loser_id = creator_name, creator_id

    if get_user(TAX_RECEIVER_ID):
        update_diamonds(TAX_RECEIVER_ID, tax)

    set_bet_status(bet_id, "finished")

    winner_id_str = str(winner_id) if winner_id is not None else "—"
    loser_id_str = str(loser_id) if loser_id is not None else "—"

    text = (
        f"🎲 شرط تموم شد!\n"
        f"💎 مبلغ: {amount}\n"
        f"⚔️ {creator_name} در برابر {opponent_name}\n\n"
        f"🏆 برنده: {winner_name} (آیدی: {winner_id_str})\n"
        f"😢 بازنده: {loser_name} (آیدی: {loser_id_str})\n\n"
        f"💰 مبلغ برد: {pool}\n"
        f"🏛 مالیات ({int(TAX_RATE*100)}٪): {tax}\n"
        f"✅ مبلغ نهایی برنده: {payout}"
    )
    bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=None)


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
        bot.edit_message_text(
            f"❌ شرط توسط {creator_name} لغو شد.\nمبلغ ({amount} 💎) برگردونده شد.",
            chat_id=chat_id, message_id=message_id, reply_markup=None,
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


# ================== ادمین‌ها ==================
@bot.message_handler(func=lambda m: m.text and re.match(r"^افزودن\s+الماس\s+\d+$", m.text.strip()))
def text_add_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس اضافه کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nافزودن الماس <مقدار>")
        return

    amount = int(re.search(r"\d+", message.text).group())
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    update_diamonds(target_id, amount)
    bot.reply_to(message, f"✅ {amount} 💎 به کاربر {target_id} اضافه شد.\nموجودی فعلی: {get_balance(target_id)} 💎")


@bot.message_handler(func=lambda m: m.text and re.match(r"^کم\s*کردن\s+الماس\s+\d+$", m.text.strip()))
def text_remove_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس کم کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nکم کردن الماس <مقدار>")
        return

    amount = int(re.search(r"\d+", message.text).group())
    if amount <= 0:
        bot.reply_to(message, "مقدار باید بزرگتر از صفر باشه.")
        return

    target_id = message.reply_to_message.from_user.id
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return

    deduct = min(amount, get_balance(target_id))
    update_diamonds(target_id, -deduct)
    bot.reply_to(message, f"✅ {deduct} 💎 از کاربر {target_id} کم شد.\nموجودی فعلی: {get_balance(target_id)} 💎")


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
    WEBHOOK_URL = "https://bet-bot-e1c2.onrender.com/webhook"
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)