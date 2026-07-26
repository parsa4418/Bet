# -*- coding: utf-8 -*-
"""
بات شرط‌بندی الماس برای تلگرام (نسخه PvP با دکمه شیشه‌ای)
نیازمندی: pip install pyTelegramBotAPI
اجرا: python bot.py
"""

import sqlite3
import random
import re
import telebot
from telebot import types

# ================== تنظیمات ==================
BOT_TOKEN = "8666764154:AAF8_1aoveWwJ_trYmPsTdKSNgWaF2orh3U"  
ADMIN_IDS = [8904869158]                     # آیدی عددی ادمین‌ها
START_DIAMONDS = 50
REFERRAL_BONUS = 25
TAX_RATE = 0.10          # مالیات ۱۰٪ از کل مبلغ برد (هر ۴۰ تا، ۴ تا)
TAX_RECEIVER_ID = ADMIN_IDS[0]   # الماس مالیات به این آیدی واریز میشه (خزانه بات)
WELCOME_IMAGE = "welcome.jpg"    # عکس خوش‌آمدگویی - باید کنار bot.py توی همون پوشه باشه

bot = telebot.TeleBot(BOT_TOKEN)
DB_PATH = "diamonds.db"


# ================== دیتابیس ==================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            diamonds INTEGER DEFAULT 0,
            referred_by INTEGER,
            ref_count INTEGER DEFAULT 0
        )
    """)
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


# ---------- توابع مربوط به جدول bets ----------
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
    return row  # (bet_id, creator_id, creator_name, amount, chat_id, message_id, status)


def set_bet_status(bet_id, status):
    conn = get_conn()
    conn.execute("UPDATE bets SET status=? WHERE bet_id=?", (status, bet_id))
    conn.commit()
    conn.close()


# ================== استارت + رفرال ==================
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

    caption = "به بات شرط‌بندی خوش اومدید🌹"

    try:
        with open(WELCOME_IMAGE, "rb") as photo:
            bot.send_photo(message.chat.id, photo, caption=caption, reply_markup=markup)
    except FileNotFoundError:
        # اگه فایل عکس پیدا نشه، فقط پیام متنی می‌فرسته تا بات کرش نکنه
        bot.send_message(message.chat.id, caption, reply_markup=markup)


@bot.message_handler(commands=["balance", "موجودی"])
def cmd_balance(message):
    bot.reply_to(message, f"موجودی الماس شما: {get_balance(message.from_user.id)} 💎")


# ---------- پیام متنی ساده «موجودی» (بدون /) داخل گروه ----------
@bot.message_handler(func=lambda m: m.text and m.text.strip() == "موجودی")
def text_balance(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return

    balance = get_balance(user_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"💎 {balance}", callback_data="pending"))
    bot.reply_to(message, "موجودی شما:", reply_markup=markup)


# ================== حساب کاربری ==================
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


@bot.message_handler(commands=["account", "حساب"])
def cmd_account(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول /start بزن.")
        return
    text, markup = build_account_view(user_id, get_display_name(message.from_user))
    bot.reply_to(message, text, reply_markup=markup)


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


def ask_transfer_target(message, owner_id):
    """مرحله اول: گرفتن آیدی مقصد و مقدار"""
    if message.from_user.id != owner_id:
        # فقط صاحب حساب اجازه داره جواب بده؛ دوباره منتظر می‌مونیم
        bot.register_next_step_handler(message, ask_transfer_target, owner_id)
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        msg = bot.reply_to(message, "فرمت اشتباهه. دوباره اینطوری بفرست:\n<آیدی عددی مقصد> <مقدار>\nمثال: 123456789 20")
        bot.register_next_step_handler(msg, ask_transfer_target, owner_id)
        return

    target_id, amount = int(parts[0]), int(parts[1])
    ok, result_msg = perform_transfer(owner_id, target_id, amount)
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
        "آیدی عددی مقصد و مقدار الماس رو اینطوری بفرست:\n<آیدی عددی> <مقدار>\nمثال: 123456789 20\n\n"
        "(آیدی عددی رو با @userinfobot می‌تونی پیدا کنی)"
    )
    bot.register_next_step_handler(msg, ask_transfer_target, owner_id)


# ================== give / take (ادمین) ==================
def resolve_target(message):
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        parts = message.text.split()
        amount = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return target_id, amount
    parts = message.text.split()
    if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None, None


@bot.message_handler(commands=["give"])
def cmd_give(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین.")
        return
    target_id, amount = resolve_target(message)
    if not target_id or not amount or amount <= 0:
        bot.reply_to(message, "ریپلای + /give <مقدار>   یا   /give <آیدی> <مقدار>")
        return
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return
    update_diamonds(target_id, amount)
    bot.reply_to(message, f"✅ {amount} الماس اضافه شد. موجودی: {get_balance(target_id)} 💎")


@bot.message_handler(commands=["take"])
def cmd_take(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین.")
        return
    target_id, amount = resolve_target(message)
    if not target_id or not amount or amount <= 0:
        bot.reply_to(message, "ریپلای + /take <مقدار>   یا   /take <آیدی> <مقدار>")
        return
    if not get_user(target_id):
        bot.reply_to(message, "این کاربر هنوز /start نزده.")
        return
    deduct = min(amount, get_balance(target_id))
    update_diamonds(target_id, -deduct)
    bot.reply_to(message, f"✅ {deduct} الماس کم شد. موجودی: {get_balance(target_id)} 💎")


def perform_transfer(sender_id, target_id, amount):
    """انجام انتقال الماس بین دو کاربر. خروجی: (ok: bool, پیام: str)"""
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


# ================== انتقال دستی بین کاربران ==================
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


# ================== شرط‌بندی PvP ==================
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

    # الماس شرط رو همون‌جا از سازنده کم می‌کنیم (تا داخل چند شرط همزمان خرج نکنه)
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


# ---------- پیام متنی ساده «شرط بندی 20» یا «شرط 20» (بدون /) ----------
@bot.message_handler(func=lambda m: m.text and re.match(r"^شرط\s*بندی?\s+\d+$", m.text.strip()))
def text_bet(message):
    amount = int(re.search(r"\d+", message.text).group())
    start_bet_flow(message, amount)


def resolve_bet(bet_id, opponent_id, opponent_name, is_bot=False):
    """برنده رو رندوم بین سازنده و رقیب (یا ربات) انتخاب می‌کنه و الماس رو جابه‌جا می‌کنه"""
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

    # مالیات به خزانه بات واریز میشه (اگه خودِ برنده ادمینِ خزانه نباشه)
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

    # ---------- لغو شرط ----------
    if action == "cancel":
        if clicker_id != creator_id:
            bot.answer_callback_query(call.id, "فقط سازنده شرط می‌تونه لغو کنه.", show_alert=True)
            return
        update_diamonds(creator_id, amount)  # برگردوندن الماس
        set_bet_status(bet_id, "cancelled")
        bot.edit_message_text(
            f"❌ شرط توسط {creator_name} لغو شد.\nمبلغ ({amount} 💎) برگردونده شد.",
            chat_id=chat_id, message_id=message_id, reply_markup=None,
        )
        bot.answer_callback_query(call.id, "شرط لغو شد.")
        return

    # ---------- پیوستن به شرط ----------
    if action == "join":
        if clicker_id == creator_id:
            bot.answer_callback_query(call.id, "سازنده حق شرکت در شرط خودش رو نداره!", show_alert=True)
            return
        if not get_user(clicker_id):
            bot.answer_callback_query(call.id, "اول باید به بات /start بزنی (پیوی بات).", show_alert=True)
            return
        if get_balance(clicker_id) < amount:
            bot.answer_callback_query(call.id, "موجودی الماس کافی نداری.", show_alert=True)
            return

        # قفل سریع: وضعیت رو همین الان finished نکنیم تا race condition نشه، ولی برای سادگی همینجا رزرو می‌کنیم
        update_diamonds(clicker_id, -amount)
        bot.answer_callback_query(call.id, "پیوستی! در حال مشخص شدن نتیجه...")
        resolve_bet(bet_id, clicker_id, clicker_name, is_bot=False)
        return

    # ---------- شرط با ربات ----------
    if action == "bot":
        if clicker_id != creator_id:
            bot.answer_callback_query(call.id, "فقط سازنده می‌تونه با ربات شرط ببنده.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "در حال شرط‌بندی با ربات...")
        resolve_bet(bet_id, opponent_id=None, opponent_name="🤖 ربات", is_bot=True)
        return


# ================== راهنما ==================
@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.reply_to(
        message,
        "دستورات:\n"
        "/start - ثبت‌نام / لینک رفرال\n"
        "/balance - موجودی الماس\n"
        "/account - حساب کاربری (با دکمه انتقال الماس)\n"
        "/transfer (ریپلای) <مقدار> - انتقال دستی\n"
        "/bet <مقدار> - ساخت شرط جدید (با دکمه)\n"
        "--- ادمین ---\n"
        "/give (ریپلای) <مقدار>\n"
        "/take (ریپلای) <مقدار>"
    )


if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling()
  
