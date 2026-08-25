# -*- coding: utf-8 -*-
"""
بات شرط‌بندی الماس با Webhook - نسخه Supabase (کامل)
اضافه شده: پنل مدیریت، سیستم تورنومنت، رفع باگ شرط
"""

import os
import random
import re
import time
import threading
import logging
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request
import telebot
from telebot import types
from supabase import create_client, Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "توکن-خودت-رو-اینجا-بذار")
ADMIN_IDS = [8904869158, 8196150649, 6094128468]  # آیدی عددی خود را اینجا قرار دهید
START_DIAMONDS = 10000
REFERRAL_BONUS = 250000
TAX_RATE = 0.10
TAX_RECEIVER_ID = ADMIN_IDS[0]
JOIN_TIMEOUT_SECONDS = 60

# ================== جواهری / انگشترسازی ==================
# هر الماس انگشتر هنگام پیدا شدن به‌صورت شانسی به یکی از این سنگ‌ها تبدیل می‌شود.
JEWELRY_GEMS = {
    "agate":      {"name": "عقیق",       "emoji": "🔴", "tier": "🟢 معمولی",   "price": 500_000},
    "turquoise":  {"name": "فیروزه",     "emoji": "🩵", "tier": "🟢 معمولی",   "price": 500_000},
    "amethyst":   {"name": "آمیتیست",    "emoji": "🟣", "tier": "🔵 کمیاب",    "price": 1_000_000},
    "garnet":     {"name": "گارنت",      "emoji": "❤️", "tier": "🔵 کمیاب",    "price": 1_000_000},
    "sapphire":   {"name": "یاقوت کبود", "emoji": "🔵", "tier": "🟣 حماسی",    "price": 2_000_000},
    "emerald":    {"name": "زمرد",       "emoji": "💚", "tier": "🟣 حماسی",    "price": 2_000_000},
    "ruby":       {"name": "یاقوت سرخ",  "emoji": "❤️", "tier": "🟣 حماسی",    "price": 2_000_000},
    "diamond":    {"name": "الماس",      "emoji": "💎", "tier": "🔴 افسانه‌ای", "price": 5_000_000},
}
# احتمال رده‌ها: معمولی 50٪، کمیاب 30٪، حماسی 15٪، افسانه‌ای 5٪
JEWELRY_TIER_WEIGHTS = [("common", 50), ("rare", 30), ("epic", 15), ("legendary", 5)]
JEWELRY_TIER_GEMS = {
    "common": ["agate", "turquoise"],
    "rare": ["amethyst", "garnet"],
    "epic": ["sapphire", "emerald", "ruby"],
    "legendary": ["diamond"],
}
JEWELRY_WORK_SECONDS = 90 * 60


# ================== الماس تصادفی گروه ==================
DIAMOND_HUNT_MESSAGE_INTERVAL = 200
DIAMOND_HUNT_COSTS = [50_000, 100_000, 150_000]
DIAMOND_HUNT_WIN_CHANCES = [0.30, 0.50, 0.70]
DIAMOND_HUNT_REASONS = [
    "باعث شد الماس از دستش بیافته و بشکنه❌",
    "باعث شد الماس گم بشه❌",
    "باعث شد الماس از دستش لیز بخوره و بشکنه❌",
    "باعث شد الماس ناپدید بشه❌",
    "باعث شد الماس توسط یک نفر دزدیده بشه❌",
    "باعث شد الماس از بین بره❌",
]

SPIN_MIN = 5000
SPIN_MAX = 250000
SPIN_COOLDOWN_HOURS = 24

LOAN_MAX = 1_000_000
LOAN_TAX_RATE = 0.10

# ================== سیستم سطح‌بندی (Leveling) ==================
# هر آیتم: (سطح، عنوان، XP لازم برای رسیدن به این سطح، پاداش الماس هنگام رسیدن به این سطح)
LEVELS = [
    (1,  "الماس‌یاب تازه‌کار",   0,          0),
    (2,  "الماس‌یاب مبتدی",      100,        50_000),
    (3,  "الماس‌یاب کارآموز",    250,        100_000),
    (4,  "الماس‌یاب نیمه‌حرفه‌ای", 500,        200_000),
    (5,  "الماس‌یاب حرفه‌ای",     800,        350_000),
    (6,  "الماس‌یاب خبره",       1_200,      500_000),
    (7,  "استاد الماس‌یابی",      1_800,      750_000),
    (8,  "افسانه الماس‌یابی",     2_500,      1_000_000),
    (9,  "سلطان الماس",          3_500,      2_000_000),
    (10, "پادشاه الماس",         5_000,      5_000_000),
]
MAX_LEVEL = LEVELS[-1][0]

XP_PER_MESSAGE = 1
XP_PER_BET_WIN = 10
XP_PER_CASINO_WIN = 15
XP_PER_RING_DIAMOND = 50
XP_PER_TOURNAMENT_WIN = 100
XP_PER_DAILY_GIFT = 5
XP_BONUS_PER_CASINO_100K = 1     # هر ۱۰۰,۰۰۰ الماس برد در کازینو ۱ XP اضافه
XP_BONUS_CASINO_CAP = 50

# ================== سیستم جام‌ها (Trophies) ==================
# کلید → (عنوان نمایشی، توضیح)
TROPHIES = {
    "msg_100":       ("📝 تازه‌وارد",         "اولین قدم رو برداشتی! از این به بعد به‌ازای هر پیام ۲ XP می‌گیری."),
    "msg_500":       ("🗣️ پرحرف",            "عاشق حرف زدنه! از این به بعد به‌ازای هر پیام ۵ XP می‌گیری."),
    "ring_1":        ("💎 الماس‌یاب",         "اولین الماس رو پیدا کردی!"),
    "ring_10":       ("💎💎 جوینده الماس",    "حرفه‌ای شدی!"),
    "ring_100":      ("👑 سلطان الماس",      "پادشاه الماس‌ها!"),
    "bet_10":        ("🎲 قمارباز",          "وارد بازی شدی!"),
    "bet_50":        ("🎲🎲 قمارباز حرفه‌ای", "🎁 از این به بعد فقط ۸٪ مالیات از شرط‌بندی معمولی می‌دی (۲٪ کمتر)."),
    "bet_100":       ("🎲🎲🎲 سلطان قمار",    "🎁 از این به بعد فقط ۵٪ مالیات از شرط‌بندی معمولی و ۸٪ مالیات وام می‌دی."),
    "casino_50":     ("🎰 حرفه‌ای در کازینو", "🎁 از این به بعد فقط ۸٪ مالیات از کازینو می‌دی (۲٪ کمتر)."),
    "casino_100":    ("🎰🎰 سلطان کازینو",   "🎁 از این به بعد فقط ۵٪ مالیات از کازینو و ۸٪ مالیات وام می‌دی."),
    "wealth_1m":     ("💰 میلیونر",          "میلیونر شدی!"),
    "wealth_10m":    ("🏦 سرمایه‌دار",       "سرمایه‌دار شدی!"),
    "wealth_1b":     ("👑 میلیاردر",         "میلیاردر شدی!"),
    "tournament_win": ("🏆 قهرمان تورنومنت", "بهترینی!"),
    # جام نادر (فقط از طریق جعبه شانس)
    "rare_diamond":  ("💎 جام الماس",        "جایزه ویژه جعبه شانس! همراهش ۱۰,۰۰۰,۰۰۰ 💎 هدیه می‌گیری."),
}
# آستانه‌های پیام / الماس انگشتر / شرط / کازینو برای اعطای خودکار جام
MSG_TROPHY_THRESHOLDS = [(100, "msg_100"), (500, "msg_500")]
RING_TROPHY_THRESHOLDS = [(1, "ring_1"), (10, "ring_10"), (100, "ring_100")]
BET_WIN_TROPHY_THRESHOLDS = [(10, "bet_10"), (50, "bet_50"), (100, "bet_100")]
CASINO_WIN_TROPHY_THRESHOLDS = [(50, "casino_50"), (100, "casino_100")]
WEALTH_TROPHY_THRESHOLDS = [(1_000_000, "wealth_1m"), (10_000_000, "wealth_10m"), (1_000_000_000, "wealth_1b")]
RARE_TROPHY_KEYS = ["rare_diamond"]
RARE_TROPHY_DIAMOND_REWARD = 10_000_000

# ================== جعبه شانس (تبدیل الماس به جوایز شانسی) ==================
LOOTBOX_COST = 2_000_000
# (شرح، احتمال، نوع، مقدار) — مجموع احتمال‌ها باید ۱۰۰ باشد
LOOTBOX_PRIZES = [
    ("💎 1,000,000 الماس",  38, "diamond", 1_000_000),
    ("💎 2,000,000 الماس",  20, "diamond", 2_000_000),
    ("💎 4,000,000 الماس",  15, "diamond", 4_000_000),
    ("💎 10,000,000 الماس", 10, "diamond", 10_000_000),
    ("💎 20,000,000 الماس", 5,  "diamond", 20_000_000),
    ("👑 لقب ویژه",          7,  "tag",     None),
    ("🏅 جام نادر",          4,  "trophy",  None),
    ("💎 50,000,000 الماس (جکپات!)", 1, "diamond", 50_000_000),
]
LOOTBOX_TAGS = ["🗣️ خوشتیپ", "🗿 کصخل", "🎲 قمارباز", "🤌 بچه مثبت", "💰 بچه پولدار", "❤️ عشق", "😍 خوشگله", "🧑‍🦯 بچه روبیکایی", "🤓 نمک"]

# ================== تنظیمات پیش‌فرض اعلان‌های گروه ==================
NOTIFY_DEFAULTS = {
    "level_up": True,
    "trophy": True,
    "new_diamond": True,
    "daily_gift": False,
    "diamond_timer": True,
    "betting": False,
    "tournament": True,
}
NOTIFY_LABELS = {
    "level_up": "🏆 ارتقا سطح",
    "trophy": "🏅 دریافت جام",
    "new_diamond": "💎 الماس جدید",
    "daily_gift": "🎁 هدیه روزانه",
    "diamond_timer": "⏰ تایمر الماس",
    "betting": "🎲 شرط‌بندی",
    "tournament": "🏆 تورنومنت",
}

# ================== تنظیمات بانک ==================
BANK_OPENING_FEE = 5_000_000
BANK_INTEREST_RATE = 0.03
BANK_DAILY_INTEREST_MAX = 1_000_000
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================== اتصال به Supabase ==================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== قفل برای کازینو ==================
casino_lock = threading.Lock()
active_casino_games = {}
active_mine_games = {}
MINE_GRID_SIZE = 9       # 3x3
MINE_MULTIPLIER_STEP = 0.25
casino_timers = {}

# ================== توابع دیتابیس (Supabase) ==================
def get_user(user_id):
    try:
        response = supabase.table("users").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در get_user: {e}")
        return None

def create_user(user_id, username, referred_by=None):
    try:
        supabase.table("users").insert({
            "user_id": user_id,
            "username": username,
            "diamonds": START_DIAMONDS,
            "referred_by": referred_by,
            "ref_count": 0,
            "loan_balance": 0,
            "last_spin": 0,
            "bank_balance": 0,
            "bank_account_number": None,
            "bank_interest_date": None,
            "last_loan_date": None,
            "ring_diamonds": 0
        }).execute()
    except Exception as e:
        logging.error(f"خطا در create_user: {e}")

def update_diamonds(user_id, amount):
    try:
        user = get_user(user_id)
        if user:
            new_balance = user['diamonds'] + amount
            supabase.table("users").update({"diamonds": new_balance}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در update_diamonds: {e}")

# ================== سیستم الماس تصادفی گروه ==================
diamond_hunt_lock = threading.Lock()

def _get_diamond_hunt(chat_id):
    try:
        response = supabase.table("diamond_hunts").select("*").eq("chat_id", chat_id).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در _get_diamond_hunt: {e}")
        return None

def _ensure_diamond_hunt_row(chat_id):
    row = _get_diamond_hunt(chat_id)
    if row:
        return row
    try:
        response = supabase.table("diamond_hunts").insert({
            "chat_id": chat_id,
            "message_count": 0,
            "active": False,
            "hunt_message_id": None,
            "attempts": []
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در ساخت وضعیت الماس گروه {chat_id}: {e}")
        return None

def _set_diamond_hunt(chat_id, **updates):
    try:
        supabase.table("diamond_hunts").update(updates).eq("chat_id", chat_id).execute()
    except Exception as e:
        logging.error(f"خطا در به‌روزرسانی وضعیت الماس گروه {chat_id}: {e}")

def _choose_jewelry_gem():
    tier = random.choices([x[0] for x in JEWELRY_TIER_WEIGHTS], weights=[x[1] for x in JEWELRY_TIER_WEIGHTS], k=1)[0]
    return random.choice(JEWELRY_TIER_GEMS[tier])

def _add_ring_diamond(user_id):
    """یک الماس انگشتر را مستقیماً به یک سنگ جواهری تصادفی تبدیل می‌کند."""
    try:
        if not get_user(user_id):
            return False
        gem_key = _choose_jewelry_gem()
        row = supabase.table("jewelry_inventory").select("*").eq("user_id", user_id).execute()
        current = row.data[0] if row.data else {"user_id": user_id}
        current[gem_key] = int(current.get(gem_key, 0) or 0) + 1
        supabase.table("jewelry_inventory").upsert(current, on_conflict="user_id").execute()
        # ring_diamonds قبلی را هم به‌عنوان شمارنده کل الماس‌های انگشتر نگه می‌داریم.
        total_ring = int(get_user(user_id).get("ring_diamonds", 0) or 0) + 1
        supabase.table("users").update({"ring_diamonds": total_ring}).eq("user_id", user_id).execute()
        return gem_key
    except Exception as e:
        logging.error(f"خطا در ثبت جواهر برای {user_id}: {e}")
        return False

def _format_attempt_history(attempts):
    lines = []
    for idx, attempt in enumerate(attempts, 1):
        lines.append(f"در تلاش {idx} {attempt['name']} {attempt['reason']}")
    return "\n".join(lines)

def _diamond_hunt_markup(attempt_no, message_id):
    markup = types.InlineKeyboardMarkup()
    if attempt_no <= 3:
        markup.add(types.InlineKeyboardButton(
            "💎 برداشتن الماس",
            callback_data=f"dhunt|{message_id}|{attempt_no}"
        ))
    return markup

def _expire_diamond_hunt(chat_id, message_id):
    with diamond_hunt_lock:
        row = _get_diamond_hunt(chat_id)
        if not row:
            return
        if not row.get("active"):
            return
        if int(row.get("hunt_message_id") or 0) != int(message_id):
            return

        _set_diamond_hunt(
            chat_id,
            active=False,
            hunt_message_id=None,
            attempts=[]
        )

    try:
        bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption="⏰ زمان برداشتن الماس به پایان رسید و الماس از دست رفت! ❌💎",
            reply_markup=None
        )
    except Exception:
        try:
            safe_edit_message(
                "⏰ زمان برداشتن الماس به پایان رسید و الماس از دست رفت! ❌💎",
                chat_id,
                message_id,
                reply_markup=None
            )
        except Exception as e:
            logging.error(f"خطا در منقضی کردن الماس گروه {chat_id}: {e}")

def _start_diamond_hunt(chat_id):
    try:
        photo_url = "https://i.ibb.co/TDMk6NZz/file-00000000aa6481f7a219e1184b4dd639.jpg"
        
        msg = bot.send_photo(
            chat_id,
            photo=photo_url,
            caption="💎 یک الماس انگشتر در شهر پیدا شد!\n\n"
                    "این الماس قابلیت تبدیل به انگشتر داره 💍\n\n"
                    "تا از دست نرفته تلاش خودت رو برای بدست آوردنش بکن ✅\n\n"
                    "از دکمه زیر برای برداشتن الماس استفاده کنید❗\n\n"
                    f"💰 هزینه تلاش برای برداشتن الماس: {DIAMOND_HUNT_COSTS[0]:,} الماس💎"
        )

        _set_diamond_hunt(
            chat_id,
            active=True,
            hunt_message_id=msg.message_id,
            attempts=[]
        )

        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=msg.message_id,
            reply_markup=_diamond_hunt_markup(1, msg.message_id)
        )

        timer = threading.Timer(120, _expire_diamond_hunt, args=(chat_id, msg.message_id))
        timer.daemon = True
        timer.start()

    except Exception as e:
        logging.error(f"خطا در ارسال الماس تصادفی گروه {chat_id}: {e}")

def process_group_message_for_diamond_hunt(message):
    if not message or message.chat.type not in ("group", "supergroup"):
        return

    if getattr(message.from_user, "is_bot", False):
        return

    try:
        if get_user(message.from_user.id):
            register_message_xp(
                message.from_user.id, message.chat.id, get_display_name(message.from_user)
            )
    except Exception as e:
        logging.error(f"خطا در ثبت XP پیام: {e}")

    with diamond_hunt_lock:
        row = _ensure_diamond_hunt_row(message.chat.id)
        if not row:
            return

        if row.get("active"):
            count = int(row.get("message_count", 0) or 0) + 1
            _set_diamond_hunt(message.chat.id, message_count=count)
            return

        count = int(row.get("message_count", 0) or 0) + 1

        if count < DIAMOND_HUNT_MESSAGE_INTERVAL:
            _set_diamond_hunt(message.chat.id, message_count=count)
            return

        _set_diamond_hunt(message.chat.id, message_count=0)
        _start_diamond_hunt(message.chat.id)

def _user_display_from_call(call):
    # فقط اسم (First Name) رو نشون بده
    return call.from_user.first_name or "کاربر"

@bot.callback_query_handler(func=lambda call: call.data.startswith("dhunt|"))
def diamond_hunt_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "دکمه نامعتبر است.", show_alert=True)
        return

    try:
        message_id = int(parts[1])
        attempt_no = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id, "دکمه نامعتبر است.", show_alert=True)
        return

    chat_id = call.message.chat.id

    with diamond_hunt_lock:
        row = _get_diamond_hunt(chat_id)

        if (
            not row
            or not row.get("active")
            or int(row.get("hunt_message_id") or 0) != message_id
        ):
            bot.answer_callback_query(
                call.id,
                "این الماس دیگر قابل برداشتن نیست ❌",
                show_alert=True
            )
            return

        attempts = row.get("attempts") or []
        expected_attempt = len(attempts) + 1

        if attempt_no != expected_attempt or attempt_no > 3:
            bot.answer_callback_query(
                call.id,
                "این تلاش قبلاً انجام شده یا معتبر نیست ❌",
                show_alert=True
            )
            return

        user_id = call.from_user.id

        if not get_user(user_id):
            bot.answer_callback_query(
                call.id,
                "اول /start را در پیوی بات بزن.",
                show_alert=True
            )
            return

        cost = DIAMOND_HUNT_COSTS[attempt_no - 1]

        if get_balance(user_id) < cost:
            bot.answer_callback_query(
                call.id,
                f"برای این تلاش {cost:,} 💎 لازم داری.",
                show_alert=True
            )
            return

        update_diamonds(user_id, -cost)
        name = display_name_with_tag(user_id, _user_display_from_call(call))

        won = random.random() < DIAMOND_HUNT_WIN_CHANCES[attempt_no - 1]

        if won:
            _add_ring_diamond(user_id)
            register_ring_diamond_win(user_id, chat_id, name)
            total_attempts = attempt_no

            _set_diamond_hunt(
                chat_id,
                active=False,
                hunt_message_id=None,
                attempts=[]
            )

            try:
                bot.answer_callback_query(
                    call.id,
                    "💍 الماس با موفقیت نجات پیدا کرد!",
                    show_alert=False
                )
                
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=f"الماس بعد از {total_attempts} تلاش با موفقیت نجات یافت و صاحب جدید پیدا کرد!💎\n\n"
                            f"{name} با موفقیت الماس رو بدست آورد.💍\n\n"
                            "🎁 پاداش ⬇️\n"
                            "‏┘─ یک الماس با قابلیت تبدیل به انگشتر.💍",
                    reply_markup=None
                )
                
            except Exception as e:
                logging.error(f"خطا در نتیجه برد الماس: {e}")
                try:
                    safe_edit_message(
                        f"الماس بعد از {total_attempts} تلاش با موفقیت نجات یافت و صاحب جدید پیدا کرد!💎\n\n"
                        f"{name} با موفقیت الماس رو بدست آورد.💍\n\n"
                        "🎁 پاداش ⬇️\n"
                        "‏┘─ یک الماس با قابلیت تبدیل به انگشتر.💍",
                        chat_id,
                        message_id,
                        reply_markup=None
                    )
                except Exception as e2:
                    logging.error(f"خطا در نتیجه برد الماس (روش جایگزین): {e2}")
            return

        reason = random.choice(DIAMOND_HUNT_REASONS)
        attempts.append({"name": name, "reason": reason})
        _set_diamond_hunt(chat_id, attempts=attempts)

        bot.answer_callback_query(
            call.id,
            f"تلاش {attempt_no} ناموفق بود ❌"
        )

        if attempt_no < 3:
            next_cost = DIAMOND_HUNT_COSTS[attempt_no]
            text = (
                "❗ نتونستی الماس رو بگیری علت ⬇️\n\n"
                + _format_attempt_history(attempts)
                + "\n\n"
                f"💰 هزینه تلاش بعدی: {next_cost:,} الماس💎"
            )
            
            try:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=_diamond_hunt_markup(attempt_no + 1, message_id)
                )
            except Exception:
                safe_edit_message(
                    text,
                    chat_id,
                    message_id,
                    reply_markup=_diamond_hunt_markup(attempt_no + 1, message_id)
                )
        else:
            text = (
                "❗ نتونستی الماس رو بگیری علت ⬇️\n\n"
                + _format_attempt_history(attempts)
            )
            
            try:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=None
                )
            except Exception:
                safe_edit_message(
                    text,
                    chat_id,
                    message_id,
                    reply_markup=None
                )
            
            _set_diamond_hunt(
                chat_id,
                active=False,
                hunt_message_id=None,
                attempts=[]
            )

# ================== بخش کارخونه حفاری الماس ==================
# بالانس کارخانه حفاری:
# هر سه بخش اصلی حداکثر سطح 10 دارند.
FACTORY_MAX_LEVEL = 10

# ظرفیت انبار در هر سطح:
FACTORY_WAREHOUSE_CAPACITY = {
    1: 5_000,
    2: 25_000,
    3: 55_000,
    4: 100_000,
    5: 170_000,
    6: 260_000,
    7: 370_000,
    8: 520_000,
    9: 730_000,
    10: 1_000_000,
}

# هزینه ارتقای انبار: سطح فعلی -> سطح بعدی
FACTORY_WAREHOUSE_UPGRADE_COSTS = {
    1: 50_000,
    2: 100_000,
    3: 200_000,
    4: 350_000,
    5: 600_000,
    6: 900_000,
    7: 1_400_000,
    8: 2_000_000,
    9: 3_000_000,
}

# حداکثر تعداد کارگر = سطح کارکنان (سطح 1 تا 10)
FACTORY_WORKERS_BASE_MAX = 1

# هزینه ارتقای سطح کارکنان: سطح فعلی -> سطح بعدی
FACTORY_WORKERS_UPGRADE_COSTS = {
    1: 100_000,
    2: 250_000,
    3: 500_000,
    4: 800_000,
    5: 1_200_000,
    6: 1_700_000,
    7: 2_300_000,
    8: 3_000_000,
    9: 4_000_000,
}

# دستمزد روزانه هر کارگر بر اساس سطح کارکنان
FACTORY_WAGE_BY_LEVEL = {
    1: 5_000,
    2: 10_000,
    3: 20_000,
    4: 35_000,
    5: 55_000,
    6: 80_000,
    7: 120_000,
    8: 180_000,
    9: 260_000,
    10: 400_000,
}

# سرعت دستگاه حفاری
FACTORY_MACHINE_DRILL_SECONDS = {
    1: 2.0,
    2: 1.8,
    3: 1.6,
    4: 1.4,
    5: 1.2,
    6: 1.0,
    7: 0.8,
    8: 0.6,
    9: 0.4,
    10: 0.2,
}

# هزینه ارتقای دستگاه: سطح فعلی -> سطح بعدی
FACTORY_MACHINE_UPGRADE_COSTS = {
    1: 50_000,
    2: 100_000,
    3: 200_000,
    4: 350_000,
    5: 550_000,
    6: 800_000,
    7: 1_200_000,
    8: 1_800_000,
    9: 2_500_000,
}

FACTORY_XP_PER_HARVEST_UNIT = 500
FACTORY_XP_NEEDED_PER_LEVEL = 500


def get_factory(user_id):
    try:
        resp = supabase.table("factories").select("*").eq("user_id", user_id).execute()
        if resp.data:
            return resp.data[0]
        default = {
            "user_id": user_id,
            "warehouse_level": 1,
            "warehouse_stored": 0,
            "workers_level": 1,
            "workers_hired": 1,
            "machine_level": 1,
            "factory_xp": 0,
            "factory_level": 1,
            "last_calc_time": int(time.time()),
            "last_wage_date": _today_tehran().isoformat(),
        }
        supabase.table("factories").insert(default).execute()
        return default
    except Exception as e:
        logging.error(f"خطا در get_factory: {e}")
        return None


def factory_warehouse_capacity(level):
    level = max(1, min(int(level), FACTORY_MAX_LEVEL))
    return FACTORY_WAREHOUSE_CAPACITY[level]


def factory_warehouse_upgrade_cost(level):
    return FACTORY_WAREHOUSE_UPGRADE_COSTS.get(int(level), 0)


def factory_workers_max(level):
    level = max(1, min(int(level), FACTORY_MAX_LEVEL))
    return FACTORY_WORKERS_BASE_MAX + (level - 1)


def factory_workers_upgrade_cost(level):
    return FACTORY_WORKERS_UPGRADE_COSTS.get(int(level), 0)


def factory_wage_per_worker(level):
    level = max(1, min(int(level), FACTORY_MAX_LEVEL))
    return FACTORY_WAGE_BY_LEVEL[level]


def factory_drill_seconds(level):
    level = max(1, min(int(level), FACTORY_MAX_LEVEL))
    return FACTORY_MACHINE_DRILL_SECONDS[level]


def factory_machine_upgrade_cost(level):
    return FACTORY_MACHINE_UPGRADE_COSTS.get(int(level), 0)


def factory_xp_needed(level):
    return level * FACTORY_XP_NEEDED_PER_LEVEL


def sync_factory_production(factory):
    """الماس تولیدشده از آخرین بار تا الان رو حساب می‌کنه و به انبار اضافه می‌کنه،
    و اگه روز جدید شده دستمزد روزانه کارگرها رو کسر می‌کنه."""
    if not factory:
        return factory
    now = int(time.time())
    last = factory.get("last_calc_time") or now
    elapsed = max(0, now - last)
    drill_seconds = factory_drill_seconds(factory["machine_level"])
    rate_per_sec = (factory["workers_hired"] / drill_seconds) if drill_seconds > 0 else 0
    produced = elapsed * rate_per_sec
    capacity = factory_warehouse_capacity(factory["warehouse_level"])
    new_stored = min(capacity, factory["warehouse_stored"] + produced)

    updates = {"warehouse_stored": new_stored, "last_calc_time": now}

    today = _today_tehran().isoformat()
    if factory.get("last_wage_date") != today and factory["workers_hired"] > 0:
        wage_per = factory_wage_per_worker(factory["workers_level"])
        user_id = factory["user_id"]
        balance = get_balance(user_id)
        wage_total = factory["workers_hired"] * wage_per
        if balance >= wage_total:
            update_diamonds(user_id, -wage_total)
        else:
            affordable = int(balance // wage_per) if wage_per > 0 else 0
            if affordable > 0:
                update_diamonds(user_id, -(affordable * wage_per))
            updates["workers_hired"] = affordable
        updates["last_wage_date"] = today

    try:
        supabase.table("factories").update(updates).eq("user_id", factory["user_id"]).execute()
    except Exception as e:
        logging.error(f"خطا در sync_factory_production: {e}")
    factory = dict(factory)
    factory.update(updates)
    return factory


def get_bank_balance(user_id):
    user = get_user(user_id)
    return int(user.get("bank_balance", 0) or 0) if user else 0

def get_bank_account_number(user_id):
    user = get_user(user_id)
    return user.get("bank_account_number") if user else None

def _today_tehran():
    return datetime.now(TEHRAN_TZ).date()

def generate_bank_account_number():
    while True:
        number = "6037" + "".join(str(random.randint(0, 9)) for _ in range(12))
        try:
            exists = supabase.table("users").select("user_id").eq("bank_account_number", number).execute()
            if not exists.data:
                return number
        except Exception as e:
            logging.error(f"خطا در تولید شماره حساب: {e}")
            return number

def apply_bank_interest(user_id):
    """سود روزهای گذشته را بعد از ساعت ۰۰:۰۰ محاسبه می‌کند؛ سود هر روز حداکثر ۱ میلیون است."""
    user = get_user(user_id)
    if not user or not user.get("bank_account_number"):
        return 0

    balance = int(user.get("bank_balance", 0) or 0)
    if balance <= 0:
        today = _today_tehran().isoformat()
        supabase.table("users").update({"bank_interest_date": today}).eq("user_id", user_id).execute()
        return 0

    today = _today_tehran()
    last_raw = user.get("bank_interest_date")
    if not last_raw:
        supabase.table("users").update({"bank_interest_date": today.isoformat()}).eq("user_id", user_id).execute()
        return 0

    try:
        last_date = datetime.fromisoformat(str(last_raw)[:10]).date()
    except Exception:
        last_date = today

    if last_date >= today:
        return 0

    total_interest = 0
    days = (today - last_date).days
    for _ in range(days):
        daily_interest = min(int(balance * BANK_INTEREST_RATE), BANK_DAILY_INTEREST_MAX)
        balance += daily_interest
        total_interest += daily_interest

    supabase.table("users").update({
        "bank_balance": balance,
        "bank_interest_date": today.isoformat()
    }).eq("user_id", user_id).execute()
    return total_interest

def open_bank_account(user_id):
    user = get_user(user_id)
    if not user:
        return False, "اول /start بزن."
    if user.get("bank_account_number"):
        return True, "حساب بانکی شما از قبل فعال است."
    if get_balance(user_id) < BANK_OPENING_FEE:
        return False, f"برای افتتاح حساب باید {BANK_OPENING_FEE:,} 💎 داشته باشی."

    account_number = generate_bank_account_number()
    update_diamonds(user_id, -BANK_OPENING_FEE)
    today = _today_tehran().isoformat()
    supabase.table("users").update({
        "bank_balance": 0,
        "bank_account_number": account_number,
        "bank_interest_date": today
    }).eq("user_id", user_id).execute()
    return True, account_number

def change_bank_balance(user_id, delta):
    user = get_user(user_id)
    if not user or not user.get("bank_account_number"):
        return False
    apply_bank_interest(user_id)
    current = get_bank_balance(user_id)
    new_balance = current + delta
    if new_balance < 0:
        return False
    supabase.table("users").update({"bank_balance": new_balance}).eq("user_id", user_id).execute()
    return True

def get_last_loan_date(user_id):
    user = get_user(user_id)
    return user.get("last_loan_date") if user else None

def set_last_loan_date(user_id, value):
    try:
        supabase.table("users").update({"last_loan_date": value}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_last_loan_date: {e}")

def add_ref_count(user_id):
    try:
        user = get_user(user_id)
        if user:
            new_count = user.get('ref_count', 0) + 1
            supabase.table("users").update({"ref_count": new_count}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در add_ref_count: {e}")

def get_balance(user_id):
    user = get_user(user_id)
    return user.get('diamonds', 0) if user else 0

def get_loan_balance(user_id):
    user = get_user(user_id)
    return user.get('loan_balance', 0) if user else 0

def change_loan_balance(user_id, delta):
    try:
        user = get_user(user_id)
        if user:
            new_balance = max(0, user.get('loan_balance', 0) + delta)
            supabase.table("users").update({"loan_balance": new_balance}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در change_loan_balance: {e}")

def get_last_spin(user_id):
    user = get_user(user_id)
    return user.get('last_spin', 0) if user else 0

def set_last_spin(user_id, ts):
    try:
        supabase.table("users").update({"last_spin": ts}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_last_spin: {e}")

def create_bet(creator_id, creator_name, amount, chat_id, message_id):
    try:
        response = supabase.table("bets").insert({
            "creator_id": creator_id,
            "creator_name": creator_name,
            "amount": amount,
            "chat_id": chat_id,
            "message_id": message_id,
            "status": "pending"
        }).execute()
        return response.data[0]['bet_id'] if response.data else None
    except Exception as e:
        logging.error(f"خطا در create_bet: {e}")
        return None

def get_bet(bet_id):
    try:
        response = supabase.table("bets").select("*").eq("bet_id", bet_id).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در get_bet: {e}")
        return None

def set_bet_status(bet_id, status):
    try:
        supabase.table("bets").update({"status": status}).eq("bet_id", bet_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_bet_status: {e}")

def get_top_users(limit=10):
    try:
        response = supabase.table("users").select("user_id, username, diamonds").order("diamonds", desc=True).limit(limit).execute()
        return [(u['user_id'], u['username'], u['diamonds']) for u in response.data] if response.data else []
    except Exception as e:
        logging.error(f"خطا در get_top_users: {e}")
        return []

def get_display_name(user):
    """نام نمایشی استاندارد ربات: فقط First Name تلگرام."""
    return (getattr(user, "first_name", None) or "کاربر").strip()

# ================== سیستم سطح‌بندی، جام‌ها، آمار و اعلان‌ها ==================
# نکته پیاده‌سازی: این بخش به جداول زیر در Supabase نیاز دارد که باید از قبل
# ساخته شده باشند (به فایل new_tables.sql مراجعه کن):
#   user_progress(user_id PK, xp int, level int, messages_count int,
#                  bets_won_count int, casino_wins_count int, cosmetics jsonb)
#   user_trophies(id PK, user_id, trophy_key, earned_at)
#   group_notify_settings(chat_id PK, level_up bool, trophy bool, new_diamond bool,
#                          daily_gift bool, diamond_timer bool, betting bool, tournament bool)

def get_progress(user_id):
    try:
        resp = supabase.table("user_progress").select("*").eq("user_id", user_id).execute()
        if resp.data:
            return resp.data[0]
        default = {
            "user_id": user_id, "xp": 0, "level": 1,
            "messages_count": 0, "bets_won_count": 0, "casino_wins_count": 0,
            "cosmetics": {}
        }
        supabase.table("user_progress").insert(default).execute()
        return default
    except Exception as e:
        logging.error(f"خطا در get_progress: {e}")
        return {"user_id": user_id, "xp": 0, "level": 1, "messages_count": 0,
                "bets_won_count": 0, "casino_wins_count": 0, "cosmetics": {}}

def level_info(level):
    for lvl, title, xp_needed, reward in LEVELS:
        if lvl == level:
            return lvl, title, xp_needed, reward
    return LEVELS[0]

def xp_needed_for_next_level(level):
    if level >= MAX_LEVEL:
        return None
    for lvl, _title, xp_needed, _reward in LEVELS:
        if lvl == level + 1:
            return xp_needed
    return None

def _notify_group_if_enabled(chat_id, key, text):
    if not chat_id:
        return
    try:
        settings = get_notify_settings(chat_id)
        if settings.get(key, True):
            bot.send_message(chat_id, text)
    except Exception as e:
        logging.error(f"خطا در ارسال اعلان گروه: {e}")

def add_xp(user_id, amount, chat_id=None, display_name=None):
    """XP اضافه می‌کند، در صورت ارتقای سطح پاداش می‌دهد و اعلان می‌فرستد."""
    if amount <= 0:
        return
    try:
        progress = get_progress(user_id)
        new_xp = int(progress.get("xp", 0) or 0) + amount
        old_level = int(progress.get("level", 1) or 1)
        new_level = old_level
        while new_level < MAX_LEVEL:
            need = xp_needed_for_next_level(new_level)
            if need is not None and new_xp >= need:
                new_level += 1
            else:
                break
        supabase.table("user_progress").update(
            {"xp": new_xp, "level": new_level}
        ).eq("user_id", user_id).execute()

        if new_level > old_level:
            name = display_name or str(user_id)
            for lvl in range(old_level + 1, new_level + 1):
                _, title, _need, reward = level_info(lvl)
                if reward:
                    update_diamonds(user_id, reward)
                try:
                    bot.send_message(
                        user_id,
                        f"🏆 تبریک! به سطح {lvl} رسیدی: {title}\n💎 پاداش: {reward:,} الماس"
                    )
                except Exception:
                    pass
                _notify_group_if_enabled(
                    chat_id, "level_up",
                    f"🏆 {name} به سطح {lvl} ({title}) رسید! 🎉"
                )
    except Exception as e:
        logging.error(f"خطا در add_xp: {e}")

def get_user_trophies(user_id):
    try:
        resp = supabase.table("user_trophies").select("trophy_key").eq("user_id", user_id).execute()
        return {row["trophy_key"] for row in (resp.data or [])}
    except Exception as e:
        logging.error(f"خطا در get_user_trophies: {e}")
        return set()

def award_trophy(user_id, trophy_key, chat_id=None, display_name=None):
    if trophy_key not in TROPHIES:
        return False
    try:
        existing = get_user_trophies(user_id)
        if trophy_key in existing:
            return False
        supabase.table("user_trophies").insert({
            "user_id": user_id, "trophy_key": trophy_key
        }).execute()
        title, desc = TROPHIES[trophy_key]
        name = display_name or str(user_id)
        try:
            bot.send_message(user_id, f"🏅 جام جدید گرفتی: {title}\n{desc}")
        except Exception:
            pass
        _notify_group_if_enabled(
            chat_id, "trophy", f"🏅 {name} جام «{title}» رو گرفت! {desc}"
        )
        return True
    except Exception as e:
        logging.error(f"خطا در award_trophy: {e}")
        return False

def _check_threshold_trophies(user_id, value, thresholds, chat_id=None, display_name=None):
    for threshold, key in thresholds:
        if value >= threshold:
            award_trophy(user_id, key, chat_id=chat_id, display_name=display_name)

def get_message_xp_rate(user_id):
    """نرخ XP هر پیام بر اساس جام‌های پیام کاربر (تازه‌وارد/پرحرف)."""
    trophies = get_user_trophies(user_id)
    if "msg_500" in trophies:
        return 5
    if "msg_100" in trophies:
        return 2
    return XP_PER_MESSAGE

def register_message_xp(user_id, chat_id, display_name):
    """به‌ازای هر پیام در گروه XP و شمارنده پیام کاربر رو آپدیت می‌کنه."""
    try:
        progress = get_progress(user_id)
        count = int(progress.get("messages_count", 0) or 0) + 1
        supabase.table("user_progress").update({"messages_count": count}).eq("user_id", user_id).execute()
        add_xp(user_id, get_message_xp_rate(user_id), chat_id=chat_id, display_name=display_name)
        _check_threshold_trophies(user_id, count, MSG_TROPHY_THRESHOLDS, chat_id, display_name)
    except Exception as e:
        logging.error(f"خطا در register_message_xp: {e}")

def register_ring_diamond_win(user_id, chat_id, display_name):
    add_xp(user_id, XP_PER_RING_DIAMOND, chat_id=chat_id, display_name=display_name)
    user = get_user(user_id)
    ring_count = int((user or {}).get("ring_diamonds", 0) or 0)
    _check_threshold_trophies(user_id, ring_count, RING_TROPHY_THRESHOLDS, chat_id, display_name)
    _notify_group_if_enabled(chat_id, "new_diamond", f"💎 {display_name} یک الماس انگشتر پیدا کرد!")
    _check_wealth_trophies(user_id, chat_id, display_name)

def register_bet_win(user_id, chat_id, display_name):
    try:
        progress = get_progress(user_id)
        count = int(progress.get("bets_won_count", 0) or 0) + 1
        supabase.table("user_progress").update({"bets_won_count": count}).eq("user_id", user_id).execute()
        add_xp(user_id, XP_PER_BET_WIN, chat_id=chat_id, display_name=display_name)
        _check_threshold_trophies(user_id, count, BET_WIN_TROPHY_THRESHOLDS, chat_id, display_name)
        _check_wealth_trophies(user_id, chat_id, display_name)
    except Exception as e:
        logging.error(f"خطا در register_bet_win: {e}")

def register_casino_win(user_id, chat_id, display_name, amount_won=0):
    try:
        progress = get_progress(user_id)
        count = int(progress.get("casino_wins_count", 0) or 0) + 1
        supabase.table("user_progress").update({"casino_wins_count": count}).eq("user_id", user_id).execute()
        bonus = min(XP_BONUS_CASINO_CAP, (amount_won // 100_000) * XP_BONUS_PER_CASINO_100K)
        add_xp(user_id, XP_PER_CASINO_WIN + bonus, chat_id=chat_id, display_name=display_name)
        _check_threshold_trophies(user_id, count, CASINO_WIN_TROPHY_THRESHOLDS, chat_id, display_name)
        _check_wealth_trophies(user_id, chat_id, display_name)
    except Exception as e:
        logging.error(f"خطا در register_casino_win: {e}")

def register_tournament_win(user_id, chat_id, display_name):
    add_xp(user_id, XP_PER_TOURNAMENT_WIN, chat_id=chat_id, display_name=display_name)
    award_trophy(user_id, "tournament_win", chat_id=chat_id, display_name=display_name)

def register_daily_gift(user_id, chat_id, display_name):
    add_xp(user_id, XP_PER_DAILY_GIFT, chat_id=chat_id, display_name=display_name)
    _notify_group_if_enabled(chat_id, "daily_gift", f"🎁 {display_name} هدیه روزانه گرفت!")

def _check_wealth_trophies(user_id, chat_id, display_name):
    balance = get_balance(user_id)
    _check_threshold_trophies(user_id, balance, WEALTH_TROPHY_THRESHOLDS, chat_id, display_name)

def stats_text_for_user(user_id, display_name):
    user = get_user(user_id) or {}
    progress = get_progress(user_id)
    trophies = get_user_trophies(user_id)
    level = int(progress.get("level", 1) or 1)
    xp = int(progress.get("xp", 0) or 0)
    _, title, _need, _reward = level_info(level)
    next_need = xp_needed_for_next_level(level)
    xp_line = f"{xp} / {next_need}" if next_need is not None else f"{xp} (حداکثر سطح)"
    messages = int(progress.get("messages_count", 0) or 0)
    bets_won = int(progress.get("bets_won_count", 0) or 0)
    casino_wins = int(progress.get("casino_wins_count", 0) or 0)
    ring_diamonds = int(user.get("ring_diamonds", 0) or 0)
    diamonds = user.get("diamonds", 0)
    active_tag = get_active_tag(user_id)
    tag_line = f"👑 لقب فعال: {active_tag}\n" if active_tag else ""
    return (
        "📊 آمار کاربر\n"
        "─────────────\n"
        f"📝 پیام‌های ارسال‌شده: {messages:,}\n"
        f"💎 الماس انگشتر: {ring_diamonds:,}\n"
        f"🏆 شرط‌های برده: {bets_won:,}\n"
        f"🎰 بازی‌های کازینو برده: {casino_wins:,}\n"
        f"💰 موجودی فعلی: {diamonds:,}\n"
        f"🏅 تعداد جام‌ها: {len(trophies)}\n"
        f"{tag_line}"
        f"⭐ سطح فعلی: {level} ({title})\n"
        f"📈 XP فعلی: {xp_line}"
    )

def trophies_help_lines():
    """لیست کامل جام‌ها برای نمایش در بخش راهنما."""
    return "\n".join(f"• {title} — {desc}" for title, desc in TROPHIES.values())

def trophies_text_for_user(user_id):
    trophies = get_user_trophies(user_id)
    if not trophies:
        return "🏅 هنوز هیچ جامی نگرفتی! با فعالیت در ربات جام‌های مختلف بگیر."
    lines = ["🏅 جام‌های شما:\n"]
    for key in trophies:
        info = TROPHIES.get(key)
        if info:
            lines.append(f"• {info[0]} — {info[1]}")
    return "\n".join(lines)

# ---------- تنظیمات اعلان‌های گروه ----------
def get_notify_settings(chat_id):
    try:
        resp = supabase.table("group_notify_settings").select("*").eq("chat_id", chat_id).execute()
        if resp.data:
            row = dict(resp.data[0])
            for k, v in NOTIFY_DEFAULTS.items():
                if k not in row or row[k] is None:
                    row[k] = v
            return row
        default = {"chat_id": chat_id, **NOTIFY_DEFAULTS}
        supabase.table("group_notify_settings").insert(default).execute()
        return default
    except Exception as e:
        logging.error(f"خطا در get_notify_settings: {e}")
        return {"chat_id": chat_id, **NOTIFY_DEFAULTS}

def set_notify_setting(chat_id, key, value):
    try:
        supabase.table("group_notify_settings").update({key: value}).eq("chat_id", chat_id).execute()
    except Exception as e:
        logging.error(f"خطا در set_notify_setting: {e}")

def notify_settings_markup(chat_id):
    settings = get_notify_settings(chat_id)
    markup = types.InlineKeyboardMarkup()
    for key, label in NOTIFY_LABELS.items():
        state = "✅" if settings.get(key, True) else "❌"
        markup.add(types.InlineKeyboardButton(
            f"{state} {label}", callback_data=f"notifytoggle|{key}"
        ))
    markup.add(types.InlineKeyboardButton("🏠 بستن", callback_data="notifyclose"))
    return markup

def notify_settings_text(chat_id):
    settings = get_notify_settings(chat_id)
    lines = ["⚙️ تنظیمات اعلان‌های گروه", "─────────────────────"]
    for key, label in NOTIFY_LABELS.items():
        state = "فعال" if settings.get(key, True) else "غیرفعال"
        mark = "✅" if settings.get(key, True) else "❌"
        lines.append(f"{mark} {label}: {state}")
    return "\n".join(lines)

@bot.message_handler(commands=["notifysettings"])
@bot.message_handler(func=lambda m: m.text and m.text.strip() == "تنظیمات اعلان")
def cmd_notify_settings(message):
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "این دستور فقط داخل گروه کار می‌کند.")
        return
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        is_admin = member.status in ("administrator", "creator") or message.from_user.id in ADMIN_IDS
    except Exception:
        is_admin = message.from_user.id in ADMIN_IDS
    if not is_admin:
        bot.reply_to(message, "⛔ فقط ادمین‌های گروه می‌توانند تنظیمات اعلان را تغییر دهند.")
        return
    bot.reply_to(message, notify_settings_text(message.chat.id), reply_markup=notify_settings_markup(message.chat.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("notifytoggle|"))
def notify_toggle(call):
    if call.message.chat.type not in ("group", "supergroup"):
        bot.answer_callback_query(call.id)
        return
    try:
        member = bot.get_chat_member(call.message.chat.id, call.from_user.id)
        is_admin = member.status in ("administrator", "creator") or call.from_user.id in ADMIN_IDS
    except Exception:
        is_admin = call.from_user.id in ADMIN_IDS
    if not is_admin:
        bot.answer_callback_query(call.id, "⛔ فقط ادمین‌های گروه اجازه دارند.", show_alert=True)
        return
    key = call.data.split("|", 1)[1]
    settings = get_notify_settings(call.message.chat.id)
    new_value = not settings.get(key, True)
    set_notify_setting(call.message.chat.id, key, new_value)
    bot.answer_callback_query(call.id)
    safe_edit_message(
        notify_settings_text(call.message.chat.id),
        call.message.chat.id, call.message.message_id,
        reply_markup=notify_settings_markup(call.message.chat.id)
    )

@bot.callback_query_handler(func=lambda call: call.data == "notifyclose")
def notify_close(call):
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

# ---------- جعبه شانس ----------
def _pick_lootbox_prize():
    roll = random.uniform(0, 100)
    cumulative = 0
    for desc, prob, kind, value in LOOTBOX_PRIZES:
        cumulative += prob
        if roll <= cumulative:
            return desc, kind, value
    return LOOTBOX_PRIZES[-1][0], LOOTBOX_PRIZES[-1][2], LOOTBOX_PRIZES[-1][3]

LAQAB_DURATION_SECONDS = 7 * 86400  # هر لقب ویژه جعبه شانس ۱ هفته اعتبار داره

def _migrate_legacy_tags(cosmetics):
    """اگه کاربر از سیستم قدیمی (تک‌لقب) یه لقب داشته باشه، به فرمت لیست جدید تبدیلش می‌کنه."""
    if "tags" not in cosmetics and cosmetics.get("tag"):
        cosmetics["tags"] = [{"name": cosmetics["tag"], "expires": cosmetics.get("tag_expires") or 0}]
    cosmetics.setdefault("tags", [])
    return cosmetics

def _purge_expired_tags(cosmetics):
    """لقب‌های منقضی‌شده رو از لیست حذف می‌کنه؛ اگه لقب فعال هم منقضی شده بود پاکش می‌کنه."""
    now = int(time.time())
    tags = [t for t in cosmetics.get("tags", []) if int(t.get("expires") or 0) > now]
    removed = len(tags) != len(cosmetics.get("tags", []))
    cosmetics["tags"] = tags
    if cosmetics.get("active_tag") and not any(t.get("name") == cosmetics["active_tag"] for t in tags):
        cosmetics["active_tag"] = None
        removed = True
    return cosmetics, removed

def _apply_cosmetic(user_id, cosmetic_type, value):
    try:
        progress = get_progress(user_id)
        cosmetics = dict(progress.get("cosmetics") or {})
        cosmetics = _migrate_legacy_tags(cosmetics)
        cosmetics, _ = _purge_expired_tags(cosmetics)
        if cosmetic_type == "tag":
            tags = cosmetics["tags"]
            new_expires = int(time.time()) + LAQAB_DURATION_SECONDS
            existing = next((t for t in tags if t.get("name") == value), None)
            if existing:
                existing["expires"] = new_expires
            else:
                tags.append({"name": value, "expires": new_expires})
            cosmetics["tags"] = tags
            # توجه: لقب جدید خودکار فعال نمی‌شه، کاربر باید خودش از بخش لقب‌ها انتخابش کنه.
            cosmetics.pop("tag", None)
            cosmetics.pop("tag_expires", None)
        supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"خطا در _apply_cosmetic: {e}")

def get_user_tags(user_id):
    """همه‌ی لقب‌های هنوز-معتبرِ کاربر رو برمی‌گردونه (منقضی‌شده‌ها خودکار حذف می‌شن)."""
    try:
        progress = get_progress(user_id)
        cosmetics = _migrate_legacy_tags(dict(progress.get("cosmetics") or {}))
        cosmetics, changed = _purge_expired_tags(cosmetics)
        if changed:
            supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
        return cosmetics.get("tags", []), cosmetics.get("active_tag")
    except Exception as e:
        logging.error(f"خطا در get_user_tags: {e}")
        return [], None

def set_active_tag(user_id, index):
    """یکی از لقب‌های معتبر کاربر رو به‌عنوان لقب نمایشی فعال انتخاب می‌کنه."""
    try:
        progress = get_progress(user_id)
        cosmetics = _migrate_legacy_tags(dict(progress.get("cosmetics") or {}))
        cosmetics, _ = _purge_expired_tags(cosmetics)
        tags = cosmetics.get("tags", [])
        if index < 0 or index >= len(tags):
            return False, "لقب پیدا نشد."
        entry = tags[index]
        cosmetics["active_tag"] = entry["name"]
        supabase.table("user_progress").update({"cosmetics": cosmetics}).eq("user_id", user_id).execute()
        return True, entry["name"]
    except Exception as e:
        logging.error(f"خطا در set_active_tag: {e}")
        return False, "خطایی پیش اومد."

def get_active_tag(user_id):
    """لقب فعال کاربر رو برمی‌گردونه، یا None اگه نداره/منقضی شده."""
    try:
        tags, active_name = get_user_tags(user_id)
        if not active_name:
            return None
        if not any(t.get("name") == active_name for t in tags):
            return None
        return active_name
    except Exception as e:
        logging.error(f"خطا در get_active_tag: {e}")
        return None

def _format_remaining(expires_ts):
    remaining = int(expires_ts) - int(time.time())
    if remaining <= 0:
        return "⌛ منقضی شده"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    if days > 0:
        return f"⏳ {days} روز و {hours} ساعت"
    if hours > 0:
        return f"⏳ {hours} ساعت و {minutes} دقیقه"
    return f"⏳ {minutes} دقیقه"

def display_name_with_tag(user_id, display_name):
    """First Name + لقب فعال با قالب یکسان در تمام پیام‌ها."""
    name = (display_name or "کاربر").strip()
    if user_id is None or "🤖" in name or name == "ربات":
        return "🤖 ربات" if "ربات" in name else name
    tag = get_active_tag(user_id)
    return f"{name} (ملقب به: {tag})" if tag else name

def casino_name_with_tag(player):
    return display_name_with_tag(player.get("id"), player.get("name"))

@bot.callback_query_handler(func=lambda call: call.data == "lootbox" or call.data.startswith("lootbox|"))
def lootbox_open(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    if not get_user(user_id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    if get_balance(user_id) < LOOTBOX_COST:
        bot.answer_callback_query(
            call.id, f"برای باز کردن جعبه شانس {LOOTBOX_COST:,} 💎 لازم داری.", show_alert=True
        )
        return
    update_diamonds(user_id, -LOOTBOX_COST)
    desc, kind, value = _pick_lootbox_prize()
    display_name = get_display_name(call.from_user)

    if kind == "diamond":
        update_diamonds(user_id, value)
    elif kind == "tag":
        tag = random.choice(LOOTBOX_TAGS)
        _apply_cosmetic(user_id, "tag", tag)
        desc = f"👑 لقب ویژه: {tag} (اعتبار ۱ هفته)\nبرای نمایشش، از بخش «لقب‌ها» تو حساب کاربری انتخابش کن."
    elif kind == "trophy":
        already = get_user_trophies(user_id)
        available = [k for k in RARE_TROPHY_KEYS if k not in already]
        if available:
            key = available[0]
            award_trophy(user_id, key, chat_id=None, display_name=display_name)
            update_diamonds(user_id, RARE_TROPHY_DIAMOND_REWARD)
            desc = f"🏅 جام نادر: {TROPHIES[key][0]} + 💎 {RARE_TROPHY_DIAMOND_REWARD:,} الماس هدیه!"
        else:
            update_diamonds(user_id, 500_000)
            desc = "🏅 جام الماس رو قبلاً داری! به‌جاش 💎 500,000 الماس گرفتی."

    bot.answer_callback_query(call.id)
    safe_edit_message(
        f"📦 جعبه شانس باز شد!\n\n🎉 جایزه تو: {desc}\n\n💰 موجودی جدید: {get_balance(user_id):,} 💎",
        call.message.chat.id, call.message.message_id, reply_markup=None
    )
    auto_return_to_main(call.message.chat.id, call.message.message_id, user_id, 5)

# ================== پارس کردن مقادیر با کا/میل ==================
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
def _normalize_digits(text):
    """ارقام فارسی/عربی رو به انگلیسی تبدیل می‌کنه."""
    table = str.maketrans(PERSIAN_DIGITS + "٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(table)

# ترتیب مهمه: عبارت‌های بلندتر (میلیون/هزار) قبل از کوتاه‌ترها (م/ک) چک بشن
_AMOUNT_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(میلیون|میل|million|m|هزار|کا|کی|ک|k)?$",
    re.IGNORECASE
)

# برای استفاده داخل رجکس‌های تشخیص پیام (تریگر دستورات متنی)؛ عدد + پسوند اختیاری کا/کی/میل
AMOUNT_TOKEN = r"[\d۰-۹]+(?:[.,،][\d۰-۹]+)?\s*(?:میلیون|میل|million|m|هزار|کا|کی|ک|k)?"

def parse_amount(raw_text):
    """
    ورودی مثل '120', '120k', '120 k', '120کا', '120 کا', '12میل', '12 میلیون'
    رو به عدد صحیح تبدیل می‌کنه. کا/k = ضرب در هزار، میل/میلیون/m = ضرب در میلیون.
    اگه فرمت نامعتبر بود None برمی‌گردونه.
    """
    if not raw_text:
        return None
    text = _normalize_digits(raw_text.strip().lower())
    text = text.replace("،", "").replace(",", "")  # جداکننده هزارگان رو حذف کن
    m = _AMOUNT_RE.match(text)
    if not m:
        return None
    number = float(m.group(1))
    suffix = m.group(2)
    if suffix in ("هزار", "کا", "کی", "ک", "k"):
        number *= 1_000
    elif suffix in ("میلیون", "میل", "million", "m"):
        number *= 1_000_000
    result = int(round(number))
    if result <= 0:
        return None
    return result

def extract_amount_from_text(text, pattern_before):
    """
    برای پیام‌هایی مثل 'شرط بندی 120کا' یا 'انتقال الماس 12 میل':
    pattern_before رگ‌اکسی برای قسمت قبل از عدده (مثلاً 'شرط\\s*بندی?\\s+').
    عدد+واحد بعدش رو با parse_amount می‌خونه.
    """
    unit = r"(?:میلیون|میل|هزار|کا|ک|million|k|m)"
    match = re.search(
        pattern_before + r"([۰-۹0-9,،.]+\s*" + unit + r"?)",
        text, re.IGNORECASE
    )
    if not match:
        return None
    return parse_amount(match.group(1))

def get_effective_tax_rate(user_id, context="bet"):
    """نرخ مالیات مؤثر بر اساس جام‌های شرط‌بندی یا کازینوی کاربر."""
    trophies = get_user_trophies(user_id)
    if context == "casino":
        if "casino_100" in trophies:
            return 0.05
        if "casino_50" in trophies:
            return 0.08
    else:
        if "bet_100" in trophies:
            return 0.05
        if "bet_50" in trophies:
            return 0.08
    return TAX_RATE

def get_effective_loan_tax_rate(user_id):
    """نرخ کسر وام مؤثر؛ فقط جام سلطان قمار یا سلطان کازینو اون رو کاهش می‌ده."""
    trophies = get_user_trophies(user_id)
    if "bet_100" in trophies or "casino_100" in trophies:
        return 0.08
    return LOAN_TAX_RATE

def calculate_payout(winner_id, pool, context="bet"):
    tax_rate = get_effective_tax_rate(winner_id, context)
    admin_tax = int(pool * tax_rate)
    loan_repay = 0
    loan_rate = LOAN_TAX_RATE
    loan_balance = get_loan_balance(winner_id)
    if loan_balance > 0:
        loan_rate = get_effective_loan_tax_rate(winner_id)
        loan_cut = int(pool * loan_rate)
        loan_repay = min(loan_cut, loan_balance)
        if loan_repay > 0:
            change_loan_balance(winner_id, -loan_repay)
    final_payout = pool - admin_tax - loan_repay
    if final_payout < 0:
        final_payout = 0
    return final_payout, admin_tax, loan_repay, tax_rate, loan_rate


# ================== واریز خودکار سود بانک ==================
def apply_all_bank_interest():
    """هر روز در ساعت ۰۰:۰۰ تهران سود همه حساب‌های بانکی را واریز می‌کند."""
    try:
        response = supabase.table("users").select(
            "user_id,bank_account_number,bank_balance,bank_interest_date"
        ).not_.is_("bank_account_number", "null").execute()

        for user in (response.data or []):
            user_id = user.get("user_id")
            try:
                apply_bank_interest(user_id)
            except Exception as e:
                logging.error(f"خطا در واریز سود بانک برای {user_id}: {e}")

        logging.info("سود روزانه بانک‌ها در ساعت ۰۰:۰۰ تهران بررسی و واریز شد.")
    except Exception as e:
        logging.error(f"خطا در اجرای سود خودکار بانک: {e}")


# Scheduler timezone must be Tehran so 00:00 is Iranian local midnight.
bank_scheduler = BackgroundScheduler(timezone=TEHRAN_TZ)
bank_scheduler.add_job(
    apply_all_bank_interest,
    "cron",
    hour=0,
    minute=0,
    id="daily_bank_interest",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
bank_scheduler.start()

# ================== توابع تورنومنت ==================
def get_active_tournament():
    """بازگرداندن تورنومنت فعال (status='active') یا None"""
    try:
        response = supabase.table("tournaments").select("*").eq("status", "active").execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"خطا در get_active_tournament: {e}")
        return None

def get_user_code(tournament_id, user_id):
    """دریافت کد کاربر برای تورنومنت مشخص، در صورت نبود، تولید و ذخیره می‌کند"""
    try:
        # اول جستجو
        resp = supabase.table("tournament_codes").select("*").eq("tournament_id", tournament_id).eq("user_id", user_id).execute()
        if resp.data:
            return resp.data[0]['code']
        # تولید کد یکتا
        import string
        import random
        while True:
            code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            # بررسی یکتایی
            check = supabase.table("tournament_codes").select("*").eq("code", code).execute()
            if not check.data:
                break
        # ذخیره
        supabase.table("tournament_codes").insert({
            "tournament_id": tournament_id,
            "user_id": user_id,
            "code": code
        }).execute()
        return code
    except Exception as e:
        logging.error(f"خطا در get_user_code: {e}")
        return None

def get_votes_for_tournament(tournament_id):
    """دریافت تعداد آراء برای هر کاربر در تورنومنت"""
    try:
        response = supabase.table("tournament_votes").select("target_user_id").eq("tournament_id", tournament_id).execute()
        votes = {}
        for row in response.data:
            target = row['target_user_id']
            votes[target] = votes.get(target, 0) + 1
        return votes
    except Exception as e:
        logging.error(f"خطا در get_votes_for_tournament: {e}")
        return {}

def get_tournament_ranking(tournament_id, limit=10):
    """بازگرداندن لیست (user_id, vote_count) مرتب نزولی"""
    votes = get_votes_for_tournament(tournament_id)
    sorted_users = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    return sorted_users[:limit]

def add_vote(tournament_id, voter_id, code):
    """ثبت رأی: اگر کد معتبر باشد و رأی‌دهنده قبلاً رأی نداده باشد"""
    try:
        # بررسی کد
        code_resp = supabase.table("tournament_codes").select("user_id").eq("tournament_id", tournament_id).eq("code", code).execute()
        if not code_resp.data:
            return False, "کد نامعتبر است ❌"
        target_user_id = code_resp.data[0]['user_id']
        if target_user_id == voter_id:
            return False, "نمی‌توانید به خودتان رأی دهید ❌"
        # بررسی تکراری نبودن رأی
        vote_check = supabase.table("tournament_votes").select("*").eq("tournament_id", tournament_id).eq("voter_id", voter_id).execute()
        if vote_check.data:
            return False, "شما قبلاً رأی خود را ثبت کرده‌اید ❌"
        # ثبت رأی
        supabase.table("tournament_votes").insert({
            "tournament_id": tournament_id,
            "voter_id": voter_id,
            "target_user_id": target_user_id
        }).execute()
        return True, "✅ رأی شما با موفقیت ثبت شد."
    except Exception as e:
        logging.error(f"خطا در add_vote: {e}")
        return False, "خطای داخلی رخ داد."

def end_tournament(tournament_id):
    """پایان تورنومنت: محاسبه برندگان و توزیع جوایز"""
    try:
        tourn = supabase.table("tournaments").select("*").eq("tournament_id", tournament_id).execute()
        if not tourn.data:
            return False, "تورنومنت یافت نشد"
        prizes = tourn.data[0]['prizes']  # JSON مانند {"1": 1000, "2": 500, ...}
        if not prizes:
            return False, "جایزه‌ای تعریف نشده است"
        # دریافت رتبه‌بندی
        ranking = get_tournament_ranking(tournament_id, limit=10)
        # توزیع جوایز
        for idx, (user_id, votes) in enumerate(ranking, start=1):
            prize = prizes.get(str(idx), 0)
            if prize > 0:
                update_diamonds(user_id, prize)
            if idx == 1:
                try:
                    register_tournament_win(user_id, None, str(user_id))
                except Exception as e:
                    logging.error(f"خطا در ثبت XP قهرمان تورنومنت: {e}")
        # تغییر وضعیت تورنومنت
        supabase.table("tournaments").update({"status": "ended", "end_time": "now()"}).eq("tournament_id", tournament_id).execute()
        return True, "تورنومنت پایان یافت و جوایز توزیع شد."
    except Exception as e:
        logging.error(f"خطا در end_tournament: {e}")
        return False, f"خطا: {e}"

# ================== دکمه‌ها و توابع کمکی ==================
def main_menu_markup(user_id=None):
    # فقط حساب کاربری، راهنما و زیرمجموعه‌گیری دکمه دارند.
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("👤 حساب کاربری", callback_data=f"showaccount|{user_id}" if user_id else "showaccount"))
    markup.row(types.InlineKeyboardButton("📖 راهنما", callback_data=f"showhelp|{user_id}" if user_id else "showhelp"))
    markup.row(types.InlineKeyboardButton("👥 زیرمجموعه‌گیری", callback_data=f"showreferral|{user_id}" if user_id else "showreferral"))
    return markup


def account_menu_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("💸 انتقال الماس", callback_data=f"acctransfer|{user_id}"))
    markup.row(
        types.InlineKeyboardButton("🏅 جام‌ها", callback_data=f"showtrophies|{user_id}"),
        types.InlineKeyboardButton("🏷 لقب‌ها", callback_data=f"showtags|{user_id}")
    )
    markup.row(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}"))
    return markup

def back_to_main_menu_markup(user_id=None):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}" if user_id else "mainmenu"))
    return markup

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

def _auto_edit_after_delay(chat_id, message_id, delay, text, reply_markup):
    """بعد از delay ثانیه، پیام نتیجه را به پنل مربوطه برمی‌گرداند."""
    def _job():
        try:
            safe_edit_message(text, chat_id, message_id, reply_markup=reply_markup)
        except Exception as e:
            logging.error(f"خطا در برگشت خودکار پنل: {e}")
    threading.Timer(delay, _job).start()


def auto_return_to_main(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        "به بات شرط‌بندی خوش اومدید🌹\nاز دکمه‌های زیر استفاده کنید:",
        main_menu_markup(user_id)
    )


def auto_return_to_bank(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        bank_text(user_id),
        bank_markup(user_id)
    )


def auto_return_to_factory(chat_id, message_id, user_id, delay=5):
    factory = sync_factory_production(get_factory(user_id))
    name = get_user(user_id).get("username") if get_user(user_id) else str(user_id)
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        factory_panel_text(factory, name),
        factory_main_markup(user_id)
    )


def auto_return_to_casino(chat_id, message_id, user_id, delay=5):
    _auto_edit_after_delay(
        chat_id, message_id, delay,
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        casino_games_keyboard(user_id)
    )

def extract_owner_id(call_data):
    """از callback_data شناسه صاحب پنل را استخراج می‌کند. همیشه آخرین بخش
    جدا‌شده با '|' است (پشتیبانی از فرمت‌های چندبخشی مثل cbet|game|amount|owner)."""
    try:
        return int(call_data.split("|")[-1])
    except (IndexError, ValueError):
        return None

def check_panel_owner(call):
    """اگر کاربری غیر از صاحب پنل روی دکمه بزند، بی‌صدا کلیک را نادیده می‌گیرد
    (بدون هیچ پیام popup) و None برمی‌گرداند. این جلوی سوءاستفاده‌ای را می‌گیرد
    که کاربر دیگری در گروه بتواند پنل شخصی شخص دیگر را با کلیک، تغییر بدهد
    یا از آن استفاده کند."""
    owner_id = extract_owner_id(call.data)
    if owner_id is None or call.from_user.id != owner_id:
        bot.answer_callback_query(call.id)  # فقط ack خالی؛ هیچ اتفاقی نمی‌افتد
        return None
    return owner_id

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
            reply_markup=main_menu_markup(user_id)
        )
    except Exception:
        safe_send_message(message.chat.id, caption, reply_markup=main_menu_markup(user_id))

# ================== کالبک منوی اصلی ==================
@bot.callback_query_handler(func=lambda call: call.data == "mainmenu" or call.data.startswith("mainmenu|"))
def main_menu(call):
    # اگه این دکمه داخل یه پنل شخصی (مثل بانک، حساب، رفرال، گردونه، راهنما) قرار
    # داشته باشه، شناسه صاحبش رو حمل می‌کنه؛ در این حالت فقط خودش می‌تونه بزنتش
    # و کلیک بقیه بی‌صدا نادیده گرفته میشه. روی پیام‌های عمومی/گروهی (نتیجه شرط،
    # رتبه‌بندی و ...) این دکمه owner نداره و برای همه باز می‌مونه.
    if "|" in call.data:
        clicker_id = check_panel_owner(call)
        if clicker_id is None:
            return
    else:
        clicker_id = call.from_user.id
    bot.answer_callback_query(call.id)
    bot.clear_step_handler(call.message)
    caption = "به بات شرط‌بندی خوش اومدید🌹\nاز دکمه‌های زیر استفاده کنید:"
    # همون پیام قبلی ویرایش میشه (نه پیام جدید). این کار امنه چون بالاتر مالکیت
    # پنل‌های شخصی چک شده؛ فقط اگه ویرایش به هر دلیلی (مثلاً پیام اصلی عکس بود)
    # شکست بخوره، به‌عنوان فالبک یه پیام تازه فرستاده میشه.
    edited = safe_edit_message(caption, call.message.chat.id, call.message.message_id, main_menu_markup(clicker_id))
    if not edited:
        safe_send_message(call.message.chat.id, caption, reply_markup=main_menu_markup(clicker_id))

# ================== دستور /account ==================
@bot.message_handler(commands=["account"])
def cmd_account(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "اول /start بزن.")
        return

    diamonds = user.get('diamonds', 0)
    ref_count = user.get('ref_count', 0)
    loan_balance = user.get('loan_balance', 0)
    ring_diamonds = user.get('ring_diamonds', 0) or 0

    text = (
        f"👤 حساب کاربری\n"
        f"نام: {get_display_name(message.from_user)}\n"
        f"آیدی عددی: {user_id}\n"
        f"💎 موجودی الماس: {diamonds}\n"
        f"👥 تعداد زیرمجموعه (رفرال): {ref_count}\n"
        f"💳 وام فعلی: {loan_balance} 💎 (از سقف {LOAN_MAX})\n"
        f"💍 الماس انگشتر: {ring_diamonds}\n\n"
        f"{stats_text_for_user(user_id, get_display_name(message.from_user))}"
    )
    bot.reply_to(message, text, reply_markup=back_to_main_menu_markup(user_id))

# دسترسی متنی منوی اصلی
@bot.message_handler(func=lambda m: m.text and m.text.strip() == "حساب کاربری")
def text_account(message):
    cmd_account(message)

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["راهنما", "کمک"])
def text_help(message):
    text = (
    "📖 راهنمای استفاده از بات\n\n"
    "💰 موجودی:\n"
    "کلمه «موجودی» را در چت ارسال کنید.\n\n"
    "💰 موجودی دیگران:\n"
    "کلمه «موجودی» را در چت به همراه ریپلای روی پیام شخص ارسال کنید.\n\n"
    "💸 انتقال الماس:\n"
    "روی پیام شخص ریپلای کنید و بنویسید:\n"
    "انتقال الماس 100 کا یا عدد دلخواه\n"
    "(یا از دکمه «انتقال الماس» تو حساب کاربری استفاده کنید)\n\n"
    "🎲 شرطبندی:\n"
    "برای شرط بنویسید:\n"
    "شرطبندی 100 کا یا عدد دلخواه\n\n"
    "🎰 کازینو:\n"
    "کلمه «کازینو» را در چت ارسال کنید.\n\n"
    "🏦 بانک الماس:\n"
    "کلمه «بانک» را ارسال کنید.\n\n"
    "🎡 گردونه روزانه:\n"
    "کلمه «گردونه» را در چت ارسال کنید و الماس رایگان بگیرید 🎁\n\n"
    "⛏ حفاری:\n"
    "کلمه «حفاری» را ارسال کنید.\n\n"
    "📦 جعبه شانس:\n"
    "کلمه «جعبه شانس» را ارسال کنید.\n\n"
    "💎💍 جواهری / انگشترسازی:\n"
    "کلمه «جواهری» یا «زرگری» یا «انگشتر سازی» را ارسال کنید.\n\n"
    "🏆 رتبه‌بندی:\n"
    "کلمه «رنک» را ارسال کنید.\n\n"
    "👥 زیرمجموعه‌گیری:\n"
    "از دکمه زیرمجموعه‌گیری استفاده کنید.\n\n"
    f"{trophies_help_lines()}\n\n"
    "🏷 لقب‌ها:\n"
    "از جعبه شانس ممکنه یه لقب ویژه ببری. لقب‌هایی که تا حالا بردی، تو بخش «لقب‌ها» "
    "(داخل حساب کاربری) لیست می‌شن؛ هرکدوم ۱ هفته اعتبار داره و خودت باید انتخاب کنی "
    "کدومش نمایش داده بشه. لقب منقضی‌شده خودکار از لیست پاک می‌شه."
)
    bot.reply_to(message, text, reply_markup=back_to_main_menu_markup(message.from_user.id))

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["زیرمجموعه گیری", "زیرمجموعه‌گیری"])
def text_referral(message):
    user_id=message.from_user.id; user=get_user(user_id)
    if not user:
        bot.reply_to(message,"اول /start بزن."); return
    link=f"https://t.me/{bot.get_me().username}?start={user_id}"
    bot.reply_to(message, f"👥 زیرمجموعه‌گیری\nتعداد زیرمجموعه‌های شما: {user.get('ref_count',0)}\nپاداش هر زیرمجموعه: {REFERRAL_BONUS:,} 💎\n\nلینک اختصاصی شما:\n{link}", reply_markup=back_to_main_menu_markup(user_id))

# ================== بخش حساب کاربری ==================
@bot.callback_query_handler(func=lambda call: call.data == "showaccount" or call.data.startswith("showaccount|"))
def handle_show_account(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    username = user.get('username')
    diamonds = user.get('diamonds', 0)
    referred_by = user.get('referred_by')
    ref_count = user.get('ref_count', 0)
    loan_balance = user.get('loan_balance', 0)
    last_spin = user.get('last_spin', 0)
    ring_diamonds = user.get('ring_diamonds', 0) or 0

    text = (
        f"👤 حساب کاربری\n"
        f"نام: {get_display_name(call.from_user)}\n"
        f"آیدی عددی: {user_id}\n"
        f"💎 موجودی الماس: {diamonds}\n"
        f"👥 تعداد زیرمجموعه (رفرال): {ref_count}\n"
        f"💳 وام فعلی: {loan_balance} 💎 (از سقف {LOAN_MAX})\n"
        f"💍 الماس انگشتر: {ring_diamonds}\n\n"
        f"{stats_text_for_user(user_id, get_display_name(call.from_user))}"
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=account_menu_markup(user_id))

# ================== بخش جام‌های من ==================
@bot.callback_query_handler(func=lambda call: call.data == "showtrophies" or call.data.startswith("showtrophies|"))
def handle_show_trophies(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    safe_edit_message(trophies_text_for_user(user_id), call.message.chat.id, call.message.message_id, reply_markup=back_to_main_menu_markup(user_id))

# ================== بخش لقب‌های من ==================
def tags_menu_markup(user_id, tags, active_name):
    markup = types.InlineKeyboardMarkup()
    for i, entry in enumerate(tags):
        name = entry.get("name", "")
        label = f"✅ {name}" if name == active_name else name
        markup.row(
            types.InlineKeyboardButton(label, callback_data=f"selecttag|{user_id}|{i}"),
            types.InlineKeyboardButton(_format_remaining(entry.get("expires") or 0), callback_data=f"tagtime|{user_id}|{i}")
        )
    markup.row(types.InlineKeyboardButton("🔙 بازگشت به حساب کاربری", callback_data=f"showaccount|{user_id}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "showtags" or call.data.startswith("showtags|"))
def handle_show_tags(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    tags, active_name = get_user_tags(user_id)
    if not tags:
        text = "🏷 لقب‌ها\n\nهنوز هیچ لقب ویژه‌ای نگرفتی! از جعبه شانس امتحان کن."
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton("🔙 بازگشت به حساب کاربری", callback_data=f"showaccount|{user_id}"))
    else:
        text = "🏷 لقب‌های شما\n\nروی یه لقب بزن تا به‌عنوان لقب نمایشی فعالت انتخاب بشه:"
        markup = tags_menu_markup(user_id, tags, active_name)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("selecttag|"))
def handle_select_tag(call):
    _, owner_id_str, idx_str = call.data.split("|")
    owner_id = int(owner_id_str)
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    ok, result = set_active_tag(owner_id, int(idx_str))
    if not ok:
        bot.answer_callback_query(call.id, f"❌ {result}", show_alert=True)
        return
    bot.answer_callback_query(call.id, f"✅ لقب فعال شد: {result}")
    tags, active_name = get_user_tags(owner_id)
    safe_edit_message(
        "🏷 لقب‌های شما\n\nروی یه لقب بزن تا به‌عنوان لقب نمایشی فعالت انتخاب بشه:",
        call.message.chat.id, call.message.message_id,
        reply_markup=tags_menu_markup(owner_id, tags, active_name)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("tagtime|"))
def handle_tag_time(call):
    _, owner_id_str, idx_str = call.data.split("|")
    owner_id = int(owner_id_str)
    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "این حساب متعلق به تو نیست.", show_alert=True)
        return
    tags, _ = get_user_tags(owner_id)
    idx = int(idx_str)
    if idx < 0 or idx >= len(tags):
        bot.answer_callback_query(call.id, "لقب پیدا نشد.", show_alert=True)
        return
    entry = tags[idx]
    bot.answer_callback_query(call.id, f"{entry.get('name')}\n{_format_remaining(entry.get('expires') or 0)}", show_alert=True)

# ================== بخش زیرمجموعه ==================
@bot.callback_query_handler(func=lambda call: call.data == "showreferral" or call.data.startswith("showreferral|"))
def handle_show_referral(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    ref_count = user.get('ref_count', 0)
    link = f"https://t.me/{bot.get_me().username}?start={user_id}"
    text = (
        f"👥 زیرمجموعه‌گیری\n"
        f"تعداد زیرمجموعه‌های شما: {ref_count}\n"
        f"پاداش هر زیرمجموعه: {REFERRAL_BONUS} 💎\n\n"
        f"لینک اختصاصی شما:\n{link}"
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=back_to_main_menu_markup(user_id))

# ================== بخش بانک ==================
# نکته امنیتی: تمام دکمه‌های زیر شناسه صاحب پنل را داخل callback_data حمل می‌کنند
# (owner|user_id) تا کاربر دیگری در گروه نتواند با زدن دکمه، پنل شخص دیگر را
# به پنل خودش تغییر بدهد. هندلرهای مربوطه این شناسه را با کاربری که کلیک کرده
# مقایسه می‌کنند (به همان الگویی که در acctransfer استفاده شده).
def bank_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("💰 وام الماس", callback_data=f"loanmenu|{user_id}"))
    markup.row(
        types.InlineKeyboardButton("📤 برداشت", callback_data=f"bankwithdraw|{user_id}"),
        types.InlineKeyboardButton("📥 واریز", callback_data=f"bankdeposit|{user_id}")
    )
    markup.row(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{user_id}"))
    return markup


def loan_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💎 بانک الماس 🏦", callback_data=f"bankmenu|{user_id}"))
    return markup


def bank_back_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💎 بانک الماس 🏦", callback_data=f"bankmenu|{user_id}"))
    return markup

def bank_open_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏦 افتتاح حساب - 5,000,000 💎", callback_data=f"bankopen|{user_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}"))
    return markup

def bank_text(user_id):
    apply_bank_interest(user_id)
    user = get_user(user_id)
    bank_balance = get_bank_balance(user_id)
    account_number = user.get("bank_account_number")
    today_interest = min(int(bank_balance * BANK_INTEREST_RATE), BANK_DAILY_INTEREST_MAX)
    return (
        "🐱 بانک الماس 🏦\n\n"
        f"💳 شماره حساب : {account_number}\n"
        f"👤 به نام : {user.get('username') or user_id}\n\n"
        f"💰 موجودی حساب : {bank_balance:,} 💎\n\n"
        "🤑 سود بانکی\n"
        f"┘─ 🛍 درصد سود : {int(BANK_INTEREST_RATE * 100)}%\n"
        f"┘─ 📥 مبلغ واریزی : {today_interest:,} 💎\n"
        "┘─ ⏳ زمان واریز : 00:00\n\n"
        "❗️ برای مدیریت حساب بانکی از گزینه های زیر استفاده کنید ⬇️"
    )

@bot.callback_query_handler(func=lambda call: call.data == "bankmenu" or call.data.startswith("bankmenu|"))
def bank_menu(call):
    # سازگاری با دکمه‌ای که owner ندارد (مثلاً وقتی از منوی اصلی بدون owner ساخته شده)
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user = get_user(user_id)
    if not user:
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return
    if not user.get("bank_account_number"):
        safe_edit_message(
            "🏦 بانک الماس\n\n"
            f"برای اولین بار باید حساب بانکی خودت رو با پرداخت {BANK_OPENING_FEE:,} 💎 افتتاح کنی.\n\n"
            f"💎 موجودی فعلی: {get_balance(user_id):,} 💎",
            call.message.chat.id, call.message.message_id, bank_open_markup(user_id)
        )
        return
    safe_edit_message(bank_text(user_id), call.message.chat.id, call.message.message_id, bank_markup(user_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankopen|"))
def bank_open(call):
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    ok, result = open_bank_account(user_id)
    if not ok:
        safe_edit_message(f"❌ {result}", call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(
        "✅ حساب بانکی با موفقیت افتتاح شد!\n\n" + bank_text(user_id),
        call.message.chat.id, call.message.message_id, bank_markup(user_id)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankdeposit|"))
def bank_deposit_prompt(call):
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_bank_account_number(user_id):
        safe_edit_message("❌ اول حساب بانکی خودت رو افتتاح کن.", call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    safe_edit_message(
        f"💎 بانک الماس 🏦\n\n"
        f"💳 شماره حساب : {get_bank_account_number(user_id)}\n"
        f"👤 به نام : {get_user(user_id).get('username') or user_id}\n\n"
        f"💰 موجودی حساب : {get_bank_balance(user_id):,} 💎\n\n"
        "➕ شما درحال واریز الماس به حساب بانکی خود میباشید.\n"
        "🔺 لطفا مبلغ مورد نظر جهت واریز رو در جواب همین پنل ارسال کنید..",
        call.message.chat.id, call.message.message_id, bank_back_markup(user_id)
    )
    bot.register_next_step_handler(call.message, bank_deposit_step, user_id)

def bank_deposit_step(message, expected_user_id):
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        bot.register_next_step_handler(message, bank_deposit_step, expected_user_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        msg = bot.reply_to(message, "❌ فرمت درست نیست. مثال: 500000 یا 500k یا 500کا یا 12.5میل")
        bot.register_next_step_handler(msg, bank_deposit_step, expected_user_id)
        return
    if amount <= 0:
        msg = bot.reply_to(message, "❌ مبلغ باید بیشتر از صفر باشد.")
        bot.register_next_step_handler(msg, bank_deposit_step, expected_user_id)
        return
    if get_balance(expected_user_id) < amount:
        bot.reply_to(message, "❌ موجودی الماس شما کافی نیست.", reply_markup=bank_markup(expected_user_id))
        return
    apply_bank_interest(expected_user_id)
    update_diamonds(expected_user_id, -amount)
    change_bank_balance(expected_user_id, amount)
    result = bot.reply_to(
        message,
        f"✅ {amount:,} 💎 به حساب بانکی واریز شد.\n💰 موجودی بانک: {get_bank_balance(expected_user_id):,} 💎"
    )
    if result:
        auto_return_to_bank(message.chat.id, result.message_id, expected_user_id, 5)

@bot.callback_query_handler(func=lambda call: call.data.startswith("bankwithdraw|"))
def bank_withdraw_prompt(call):
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_bank_account_number(user_id):
        safe_edit_message("❌ اول حساب بانکی خودت رو افتتاح کن.", call.message.chat.id, call.message.message_id, bank_open_markup(user_id))
        return
    apply_bank_interest(user_id)
    safe_edit_message(
        f"💎 بانک الماس 🏦\n\n"
        f"💳 شماره حساب : {get_bank_account_number(user_id)}\n"
        f"👤 به نام : {get_user(user_id).get('username') or user_id}\n\n"
        f"💰 موجودی قابل برداشت : {get_bank_balance(user_id):,} 💎\n\n"
        "➖ شما درحال برداشت الماس از حساب بانکی خود میباشید.\n"
        "🔺 لطفا مبلغ مورد نظر جهت برداشت رو در جواب همین پنل ارسال کنید..",
        call.message.chat.id, call.message.message_id, bank_back_markup(user_id)
    )
    bot.register_next_step_handler(call.message, bank_withdraw_step, user_id)

def bank_withdraw_step(message, expected_user_id):
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        bot.register_next_step_handler(message, bank_withdraw_step, expected_user_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        msg = bot.reply_to(message, "❌ فرمت درست نیست. مثال: 500000 یا 500k یا 500کا یا 12.5میل")
        bot.register_next_step_handler(msg, bank_withdraw_step, expected_user_id)
        return
    if amount <= 0:
        msg = bot.reply_to(message, "❌ مبلغ باید بیشتر از صفر باشد.")
        bot.register_next_step_handler(msg, bank_withdraw_step, expected_user_id)
        return
    apply_bank_interest(expected_user_id)
    if get_bank_balance(expected_user_id) < amount:
        bot.reply_to(message, "❌ موجودی بانک برای این برداشت کافی نیست.", reply_markup=bank_markup(expected_user_id))
        return
    change_bank_balance(expected_user_id, -amount)
    update_diamonds(expected_user_id, amount)
    result = bot.reply_to(
        message,
        f"✅ {amount:,} 💎 از بانک برداشت شد.\n💰 موجودی بانک: {get_bank_balance(expected_user_id):,} 💎\n💎 موجودی کیف پول: {get_balance(expected_user_id):,} 💎"
    )
    if result:
        auto_return_to_bank(message.chat.id, result.message_id, expected_user_id, 5)

# ================== بخش وام داخل بانک ==================
@bot.callback_query_handler(func=lambda call: call.data.startswith("loanmenu|"))
def loan_menu(call):
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return

    current = get_loan_balance(user_id)
    remaining = LOAN_MAX - current
    today = _today_tehran().isoformat()
    last_loan = get_last_loan_date(user_id)
    can_take_today = last_loan != today

    if not can_take_today:
        safe_edit_message(
            f"💰 وام الماس\n\n"
            f"💳 وام فعلی شما: {current:,} 💎 (از سقف {LOAN_MAX:,})\n"
            "⏳ امروز یک بار وام گرفتی. فردا دوباره می‌تونی درخواست بدی.",
            call.message.chat.id, call.message.message_id,
            loan_markup(user_id)
        )
        return

    if remaining <= 0:
        safe_edit_message(
            f"💰 وام الماس\n\n"
            f"💳 وام فعلی شما: {current:,} 💎 (از سقف {LOAN_MAX:,})\n"
            "⛔ به سقف مجاز رسیدی.\n"
            "با بردن شرط یا کازینو، ۱۰٪ اضافه از هر برد بابت بازپرداخت وام کسر میشه.",
            call.message.chat.id, call.message.message_id,
            loan_markup(user_id)
        )
        return

    safe_edit_message(
        f"💰 وام الماس\n\n"
        f"💳 وام فعلی شما: {current:,} 💎 (از سقف {LOAN_MAX:,})\n"
        f"✅ امروز می‌تونی حداکثر {remaining:,} 💎 دیگه وام بگیری.\n"
        "⏳ هر روز فقط یک بار امکان دریافت وام داری.\n\n"
        "مبلغی که می‌خوای وام بگیری رو به عدد بفرست:",
        call.message.chat.id, call.message.message_id,
        loan_markup(user_id)
    )
    bot.register_next_step_handler(call.message, loan_amount_step, user_id, remaining)

def loan_amount_step(message, expected_user_id, remaining):
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        bot.register_next_step_handler(message, loan_amount_step, expected_user_id, remaining)
        return
    amount = parse_amount(message.text)
    if amount is None:
        msg = bot.reply_to(message, "لطفاً یه مبلغ درست بفرست. مثال: 100000 یا 100k یا 100کا")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return

    today = _today_tehran().isoformat()
    if get_last_loan_date(expected_user_id) == today:
        bot.reply_to(message, "⏳ امروز قبلاً وام گرفتی. فردا دوباره می‌تونی وام بگیری.", reply_markup=loan_markup(expected_user_id))
        return
    if amount <= 0:
        msg = bot.reply_to(message, "مبلغ باید بزرگتر از صفر باشه. دوباره بفرست:")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return
    if amount > remaining:
        msg = bot.reply_to(message, f"حداکثر می‌تونی {remaining:,} 💎 وام بگیری. یه عدد کمتر یا مساوی بفرست:")
        bot.register_next_step_handler(msg, loan_amount_step, expected_user_id, remaining)
        return

    update_diamonds(expected_user_id, amount)
    change_loan_balance(expected_user_id, amount)
    set_last_loan_date(expected_user_id, today)
    result = bot.reply_to(
        message,
        f"✅ {amount:,} 💎 وام گرفتی و به موجودیت اضافه شد.\n"
        f"💳 مجموع وام فعلی: {get_loan_balance(expected_user_id):,} 💎\n"
        f"💰 موجودی جدید: {get_balance(expected_user_id):,} 💎\n"
        "⏳ وام بعدی: فردا"
    )
    if result:
        auto_return_to_bank(message.chat.id, result.message_id, expected_user_id, 5)

# ================== دستورات متنی بانک ==================
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["بانک", "بانک الماس"])
def text_bank(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return
    user = get_user(user_id)
    if not user.get("bank_account_number"):
        bot.reply_to(
            message,
            "🏦 بانک الماس\n\n"
            f"برای اولین بار باید حساب بانکی خودت رو با پرداخت {BANK_OPENING_FEE:,} 💎 افتتاح کنی.\n"
            f"💎 موجودی فعلی: {get_balance(user_id):,} 💎",
            reply_markup=bank_open_markup(user_id)
        )
        return
    bot.send_message(message.chat.id, bank_text(user_id), reply_markup=bank_markup(user_id))

# ================== بخش گردونه ==================
@bot.callback_query_handler(func=lambda call: call.data == "spinwheel" or call.data.startswith("spinwheel|"))
def spin_wheel(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
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
    register_daily_gift(user_id, call.message.chat.id, get_display_name(call.from_user))

    bot.answer_callback_query(call.id)
    markup = back_to_main_menu_markup(user_id)
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
@bot.callback_query_handler(func=lambda call: call.data == "showhelp" or call.data.startswith("showhelp|"))
def handle_show_help(call):
    if "|" in call.data:
        owner_id = check_panel_owner(call)
        if owner_id is None:
            return
    else:
        owner_id = call.from_user.id

    bot.answer_callback_query(call.id)

    text = (
    "📖 راهنمای استفاده از بات\n\n"
    "💰 موجودی:\n"
    "کلمه «موجودی» را در چت ارسال کنید.\n\n"
    "💰 موجودی دیگران:\n"
    "کلمه «موجودی» را در چت به همراه ریپلای روی پیام شخص ارسال کنید.\n\n"
    "💸 انتقال الماس:\n"
    "روی پیام شخص ریپلای کنید و بنویسید:\n"
    "انتقال الماس 100 کا یا عدد دلخواه\n"
    "(یا از دکمه «انتقال الماس» تو حساب کاربری استفاده کنید)\n\n"
    "🎲 شرطبندی:\n"
    "برای شرط بنویسید:\n"
    "شرطبندی 100 کا یا عدد دلخواه\n\n"
    "🎰 کازینو:\n"
    "کلمه «کازینو» را در چت ارسال کنید.\n\n"
    "🏦 بانک الماس:\n"
    "کلمه «بانک» را ارسال کنید.\n\n"
    "🎡 گردونه روزانه:\n"
    "کلمه «گردونه» را در چت ارسال کنید و الماس رایگان بگیرید 🎁\n\n"
    "⛏ حفاری:\n"
    "کلمه «حفاری» را ارسال کنید.\n\n"
    "📦 جعبه شانس:\n"
    "کلمه «جعبه شانس» را ارسال کنید.\n\n"
    "🏆 رتبه‌بندی:\n"
    "کلمه «رنک» را ارسال کنید.\n\n"
    "👥 زیرمجموعه‌گیری:\n"
    "از دکمه زیرمجموعه‌گیری استفاده کنید.\n\n"
    f"{trophies_help_lines()}\n\n"
    "🏷 لقب‌ها:\n"
    "از جعبه شانس ممکنه یه لقب ویژه ببری. لقب‌هایی که تا حالا بردی، تو بخش «لقب‌ها» "
    "(داخل حساب کاربری) لیست می‌شن؛ هرکدوم ۱ هفته اعتبار داره و خودت باید انتخاب کنی "
    "کدومش نمایش داده بشه. لقب منقضی‌شده خودکار از لیست پاک می‌شه."
)

    safe_edit_message(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=back_to_main_menu_markup(owner_id)
    )

# ================== بخش انتقال الماس ==================
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
    markup = back_to_main_menu_markup(owner_id)
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
    amount = parse_amount(parts[1]) if len(parts) == 2 and parts[0].isdigit() else None
    if len(parts) != 2 or not parts[0].isdigit() or amount is None:
        msg = bot.reply_to(message, "فرمت اشتباهه. دوباره اینطوری بفرست:\n<آیدی عددی مقصد> <مقدار>\nمثال: 123456789 20 یا 123456789 20k")
        bot.register_next_step_handler(msg, ask_transfer_target, owner_id)
        return

    target_id = int(parts[0])
    ok, result_msg = perform_transfer(owner_id, target_id, amount)

    if not ok and "نزده" in result_msg:
        result_msg += (
            "\n\n⚠️ برای اینکه بات بتونه کاربری رو بشناسه، اون شخص باید حتماً یه‌بار "
            "توی پیوی خودِ بات دستور /start رو بزنه. فقط عضو گروه بودن کافی نیست."
        )
    markup = back_to_main_menu_markup(owner_id)
    bot.reply_to(message, result_msg, reply_markup=markup)

    if ok:
        try:
            bot.send_message(target_id, f"💎 {amount} الماس از طرف کاربر {owner_id} برات واریز شد.")
        except Exception:
            pass

# ================== دستورات ادمین (قدیمی) ==================
@bot.message_handler(func=lambda m: m.text and re.search(r"افزودن\s*الماس\s*(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_add_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس اضافه کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nافزودن الماس <مقدار>\nمثال: افزودن الماس 50 یا افزودن الماس 50k")
        return

    match = re.search(r"افزودن\s*الماس\s*(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
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

@bot.message_handler(func=lambda m: m.text and re.search(r"کم\s*کردن\s*الماس\s*(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_remove_diamonds(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ فقط ادمین می‌تونه الماس کم کنه.")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nکم کردن الماس <مقدار>\nمثال: کم کردن الماس 50 یا کم کردن الماس 50k")
        return

    match = re.search(r"کم\s*کردن\s*الماس\s*(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
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
    amount = parse_amount(parts[1]) if len(parts) >= 2 else None
    if amount is None:
        bot.reply_to(message, "مثال: /transfer 20 یا /transfer 20k")
        return
    target_id = message.reply_to_message.from_user.id
    ok, msg = perform_transfer(sender_id, target_id, amount)
    markup = back_to_main_menu_markup()
    bot.reply_to(message, msg, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and re.search(r"انتقال\s+الماس\s+(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_transfer(message):
    sender_id = message.from_user.id
    if not get_user(sender_id):
        bot.reply_to(message, "اول باید یه‌بار /start بزنی (توی پیوی بات).")
        return
    if not message.reply_to_message:
        bot.reply_to(message, "روی پیام کاربر مقصد ریپلای کن و بنویس:\nانتقال الماس <مقدار>\nمثال: انتقال الماس 200")
        return

    match = re.search(r"انتقال\s+الماس\s+(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است.")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
    target_id = message.reply_to_message.from_user.id
    ok, msg = perform_transfer(sender_id, target_id, amount)
    markup = back_to_main_menu_markup()
    bot.reply_to(message, msg, reply_markup=markup)

# ================== شرط متنی ==================
def check_bet_timeout(bet_id):
    bet = get_bet(bet_id)
    if not bet:
        return
    creator_id = bet.get('creator_id')
    creator_name = bet.get('creator_name')
    amount = bet.get('amount')
    chat_id = bet.get('chat_id')
    message_id = bet.get('message_id')
    status = bet.get('status')
    if status != "pending":
        return

    update_diamonds(creator_id, amount)
    set_bet_status(bet_id, "timeout")
    markup = back_to_main_menu_markup()
    safe_edit_message(
        f"⏱ زمان تموم شد!\n"
        f"هیچ‌کس ظرف {JOIN_TIMEOUT_SECONDS} ثانیه به شرط {display_name_with_tag(creator_id, creator_name)} نپیوست.\n"
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
        f"👤 سازنده: {display_name_with_tag(user_id, creator_name)}\n"
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
    safe_edit_message(sent.text, chat_id=message.chat.id, message_id=sent.message_id, reply_markup=markup)

    threading.Timer(JOIN_TIMEOUT_SECONDS, check_bet_timeout, args=[bet_id]).start()

@bot.message_handler(commands=["bet", "شرط"])
def cmd_bet(message):
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 else None
    if amount is None:
        bot.reply_to(message, "استفاده درست: /bet <مقدار>\nمثال: /bet 20 یا /bet 20k")
        return
    start_bet_flow(message, amount)

@bot.message_handler(func=lambda m: m.text and re.search(r"شرط\s*بندی?\s+(" + AMOUNT_TOKEN + r")", m.text, re.IGNORECASE))
def text_bet(message):
    match = re.search(r"شرط\s*بندی?\s+(" + AMOUNT_TOKEN + r")", message.text, re.IGNORECASE)
    if not match:
        bot.reply_to(message, "فرمت اشتباه است. مثال: شرط بندی 20 یا شرط بندی 20k")
        return
    amount = parse_amount(match.group(1))
    if amount is None:
        bot.reply_to(message, "❌ مبلغ نامعتبره.")
        return
    start_bet_flow(message, amount)

def resolve_bet(bet_id, opponent_id, opponent_name, is_bot=False):
    bet = get_bet(bet_id)
    creator_id = bet.get('creator_id')
    creator_name = bet.get('creator_name')
    amount = bet.get('amount')
    chat_id = bet.get('chat_id')
    message_id = bet.get('message_id')
    status = bet.get('status')

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
        payout, tax, loan_repay, tax_rate_used, loan_rate_used = calculate_payout(real_winner_id, pool, context="bet")
        update_diamonds(real_winner_id, payout)
        if get_user(TAX_RECEIVER_ID):
            update_diamonds(TAX_RECEIVER_ID, tax)
        if get_user(real_winner_id):
            register_bet_win(real_winner_id, chat_id, winner_name)
    else:
        payout = 0
        tax = int(pool * TAX_RATE)
        loan_repay = 0
        tax_rate_used = TAX_RATE
        loan_rate_used = LOAN_TAX_RATE

    set_bet_status(bet_id, "finished")

    winner_display = display_name_with_tag(winner_id, winner_name)
    loser_display = display_name_with_tag(loser_id, loser_name)
    extra_line = f"💳 کسر بابت وام ({int(loan_rate_used*100)}٪): {loan_repay}\n" if loan_repay > 0 else ""
    text = (
        f"🎲 شرط تموم شد!\n"
        f"💎 مبلغ: {amount}\n"
        f"⚔️ {display_name_with_tag(creator_id, creator_name)} در برابر {display_name_with_tag(opponent_id, opponent_name)}\n\n"
        f"🏆 برنده: {winner_display}\n"
        f"😢 بازنده: {loser_display}\n\n"
        f"💰 مبلغ برد: {pool}\n"
        f"🏛 مالیات ({int(tax_rate_used*100)}٪): {tax}\n"
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

    creator_id = bet.get('creator_id')
    creator_name = bet.get('creator_name')
    amount = bet.get('amount')
    chat_id = bet.get('chat_id')
    message_id = bet.get('message_id')
    status = bet.get('status')

    # ========== بهبود پیام‌های خطا برای وضعیت‌های مختلف ==========
    if status != "pending":
        if status == "timeout":
            msg = "⏱ زمان انتظار به پایان رسید و شرط لغو شد."
        elif status == "cancelled":
            msg = "❌ این شرط توسط سازنده لغو شده است."
        elif status == "finished":
            msg = "🏁 این شرط قبلاً به پایان رسیده است."
        else:
            msg = "این شرط دیگر فعال نیست."
        bot.answer_callback_query(call.id, msg, show_alert=True)
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
            f"❌ شرط توسط {display_name_with_tag(creator_id, creator_name)} لغو شد.\nمبلغ ({amount} 💎) برگردونده شد.",
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

# ================== بخش کازینو ==================
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
CASINO_BET_PRESETS = [100000, 500000, 1000000, 5000000, 10000000]

def casino_games_keyboard(owner_id):
    markup = types.InlineKeyboardMarkup()
    for key, emoji in CASINO_GAMES.items():
        markup.add(types.InlineKeyboardButton(f"{emoji} {CASINO_GAME_NAMES[key]}", callback_data=f"cgame|{key}|{owner_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{owner_id}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == "casinomenu" or call.data.startswith("casinomenu|"))
def casino_from_main_menu(call):
    if "|" in call.data:
        owner_id = check_panel_owner(call)
        if owner_id is None:
            return
    else:
        owner_id = call.from_user.id
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard(owner_id)
    )

@bot.message_handler(func=lambda m: m.text and m.text.strip() == "کازینو")
def casino_panel(message):
    if not get_user(message.from_user.id):
        bot.reply_to(message, "اول /start بزن.")
        return
    safe_send_message(
        message.chat.id,
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        reply_markup=casino_games_keyboard(message.from_user.id)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("cgame|"))
def casino_game_select(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    game_key = call.data.split("|")[1]

    markup = types.InlineKeyboardMarkup()
    markup.row(*[
        types.InlineKeyboardButton(f"💎 {amt}", callback_data=f"cbet|{game_key}|{amt}|{owner_id}")
        for amt in CASINO_BET_PRESETS[:3]
    ])
    markup.row(*[
        types.InlineKeyboardButton(f"💎 {amt}", callback_data=f"cbet|{game_key}|{amt}|{owner_id}")
        for amt in CASINO_BET_PRESETS[3:]
    ])
    markup.row(types.InlineKeyboardButton("✏️ مبلغ دلخواه", callback_data=f"ccustom|{game_key}|{owner_id}"))
    markup.row(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"cback|{owner_id}"))

    safe_edit_message(
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]} انتخاب شد.\n💎 مبلغ شرط رو انتخاب کن:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "cback" or call.data.startswith("cback|"))
def casino_back(call):
    if "|" in call.data:
        owner_id = check_panel_owner(call)
        if owner_id is None:
            return
    else:
        owner_id = call.from_user.id
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=casino_games_keyboard(owner_id)
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
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    _, game_key, amount, _owner = call.data.split("|")
    amount = int(amount)
    user = call.from_user  # == owner_id, تضمین‌شده توسط چک بالا

    if not get_user(user.id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    if get_balance(user.id) < amount:
        bot.answer_callback_query(call.id, "💎 موجودی الماس شما کافی نیست!", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    update_diamonds(user.id, -amount)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو بازی", callback_data=f"ccancel|{call.message.message_id}"),
        types.InlineKeyboardButton("✅ پیوستن به بازی", callback_data="cjoin"),
    )
    markup.row(types.InlineKeyboardButton("🤖 بازی با ربات", callback_data="cbotplay"))

    text = (
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"👤 بازیکن اول: {display_name_with_tag(user.id, get_display_name(user))}\n"
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

@bot.callback_query_handler(func=lambda call: call.data == "casinoback")
def casino_back_to_list(call):
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "🎰 به کازینو خوش اومدی!\nیکی از بازی‌ها رو انتخاب کن:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=casino_games_keyboard(call.from_user.id)
    )

# ================== معدن الماس (مین‌زدن تکی) ==================
def mine_board_text(bet, found, multiplier):
    payout = int(bet * multiplier)
    not_found = (MINE_GRID_SIZE - 1) - found
    return (
        "💎 معدن الماس 🃏\n\n"
        f"🎒 مبلغ ورودی : {bet:,} 💎\n"
        f"🏆 مبلغ دریافتی : {payout:,} 💎 ({multiplier:.2f}x)\n\n"
        f"💎 الماس های پیدا شده : {found}\n"
        f"❔ الماس های پیدا نشده : {not_found} —\n\n"
        "❗ الماس هارو پیدا کن"
    )

def mine_board_markup(revealed, owner_id):
    markup = types.InlineKeyboardMarkup()
    for row in range(3):
        buttons = []
        for col in range(3):
            idx = row * 3 + col
            label = "💎" if idx in revealed else " "
            buttons.append(types.InlineKeyboardButton(label, callback_data=f"mine|{idx}|{owner_id}"))
        markup.row(*buttons)
    markup.row(types.InlineKeyboardButton("💰 برداشت الان", callback_data=f"minecashout|{owner_id}"))
    return markup

def mine_result_markup():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
    return markup

@bot.message_handler(func=lambda m: m.text and m.text.strip() == "معدن الماس")
def mine_panel_entry(message):
    if not get_user(message.from_user.id):
        bot.reply_to(message, "اول /start بزن.")
        return
    msg = bot.reply_to(message, "💎 معدن الماس\n\nلطفاً مبلغ شرطت رو بفرست (مثلاً 100000 یا 100k):")
    bot.register_next_step_handler(msg, mine_bet_step, message.from_user.id, msg.message_id)

def mine_bet_step(message, expected_user_id, prompt_msg_id):
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        bot.register_next_step_handler(message, mine_bet_step, expected_user_id, prompt_msg_id)
        return
    amount = parse_amount(message.text)
    if amount is None:
        msg = bot.reply_to(message, "❌ مبلغ نامعتبره. یه عدد درست بفرست (مثلاً 100000 یا 100k):")
        bot.register_next_step_handler(msg, mine_bet_step, expected_user_id, prompt_msg_id)
        return
    if get_balance(expected_user_id) < amount:
        msg = bot.reply_to(message, "💎 موجودی کافی نیست. مبلغ دیگه‌ای بفرست:")
        bot.register_next_step_handler(msg, mine_bet_step, expected_user_id, prompt_msg_id)
        return

    update_diamonds(expected_user_id, -amount)
    bomb_index = random.randint(0, MINE_GRID_SIZE - 1)
    text = mine_board_text(amount, 0, 0.0)
    markup = mine_board_markup(set(), expected_user_id)
    # همون پیام اول (که مبلغ رو ازش پرسیده بودیم) ویرایش میشه، پیام جدید فرستاده نمیشه
    safe_edit_message(text, message.chat.id, prompt_msg_id, reply_markup=markup)
    active_mine_games[prompt_msg_id] = {
        "chat_id": message.chat.id,
        "owner_id": expected_user_id,
        "bet": amount,
        "bomb_index": bomb_index,
        "revealed": set(),
        "diamonds_found": 0,
    }

@bot.callback_query_handler(func=lambda call: call.data == "noop")
def noop_callback(call):
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mine|"))
def mine_cell_reveal(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game = active_mine_games.get(call.message.message_id)
    if not game:
        bot.answer_callback_query(call.id, "این بازی تموم شده.", show_alert=True)
        return
    idx = int(call.data.split("|")[1])
    if idx in game["revealed"]:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)

    if idx == game["bomb_index"]:
        del active_mine_games[call.message.message_id]
        markup = types.InlineKeyboardMarkup()
        for row in range(3):
            buttons = []
            for col in range(3):
                i = row * 3 + col
                if i == game["bomb_index"]:
                    label = "💥"
                elif i in game["revealed"]:
                    label = "💎"
                else:
                    label = " "
                buttons.append(types.InlineKeyboardButton(label, callback_data="noop"))
            markup.row(*buttons)
        markup.row(types.InlineKeyboardButton("🎰 بازگشت به کازینو", callback_data="casinoback"))
        text = (
            "💎 معدن الماس 🃏\n\n"
            f"🎒 مبلغ ورودی : {game['bet']:,} 💎\n"
            f"🏆 مبلغ دریافتی : 0 💎 (0.00x)\n\n"
            f"💎 الماس های پیدا شده : {game['diamonds_found']}\n\n"
            "بووووممم 💣\n"
            "❌ باختی! کل مبلغ شرط از دست رفت."
        )
        safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
        return

    game["revealed"].add(idx)
    game["diamonds_found"] += 1
    multiplier = game["diamonds_found"] * MINE_MULTIPLIER_STEP

    if game["diamonds_found"] >= MINE_GRID_SIZE - 1:
        # همه‌ی خونه‌های غیر بمب پیدا شدن؛ برد کامل و برداشت خودکار
        payout = int(game["bet"] * multiplier)
        update_diamonds(owner_id, payout)
        del active_mine_games[call.message.message_id]
        text = mine_board_text(game["bet"], game["diamonds_found"], multiplier) + \
            "\n\n🎉 همه‌ی الماس‌ها رو پیدا کردی! مبلغ به حسابت اضافه شد."
        safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
        auto_return_to_casino(call.message.chat.id, call.message.message_id, owner_id, 5)
        return

    text = mine_board_text(game["bet"], game["diamonds_found"], multiplier)
    markup = mine_board_markup(game["revealed"], owner_id)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("minecashout|"))
def mine_cash_out(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game = active_mine_games.get(call.message.message_id)
    if not game:
        bot.answer_callback_query(call.id, "این بازی تموم شده.", show_alert=True)
        return
    if game["diamonds_found"] == 0:
        bot.answer_callback_query(call.id, "هنوز هیچ الماسی پیدا نکردی!", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    multiplier = game["diamonds_found"] * MINE_MULTIPLIER_STEP
    payout = int(game["bet"] * multiplier)
    update_diamonds(owner_id, payout)
    del active_mine_games[call.message.message_id]
    text = mine_board_text(game["bet"], game["diamonds_found"], multiplier) + \
        f"\n\n✅ برداشت انجام شد! {payout:,} 💎 به حسابت اضافه شد."
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=mine_result_markup())

# ================== پنل کارخونه حفاری الماس ==================
def factory_panel_text(factory, owner_name):
    capacity = factory_warehouse_capacity(factory["warehouse_level"])
    stored = int(factory["warehouse_stored"])
    workers_max = factory_workers_max(factory["workers_level"])
    drill_seconds = factory_drill_seconds(factory["machine_level"])
    xp_needed = factory_xp_needed(factory["factory_level"])
    xp = factory["factory_xp"]
    filled = min(5, int((xp / xp_needed) * 5)) if xp_needed > 0 else 0
    bar = "▰" * filled + "▱" * (5 - filled)
    return (
        "💎 حفاری الماس 🏗️\n\n"
        f"💼 مدیر کارخونه : {owner_name}\n\n"
        "🧳 انبار کارخونه\n"
        f"┘─ 🔺 ظرفیت انبار : {stored:,} / {capacity:,} الماس\n"
        f"┘─ ⭐️ سطح : {factory['warehouse_level']}\n\n"
        "👷‍♂️ کارگران حفار\n"
        f"┘─ 😺 تعداد کارگران : {factory['workers_hired']} / {workers_max} کارگر\n"
        f"┘─ ⭐️ سطح : {factory['workers_level']}\n\n"
        "🖨 دستگاه حفاری\n"
        f"┘─ ⏳ زمان در آوردن هر الماس : {drill_seconds:.1f} ثانیه\n"
        f"┘─ ⭐️ سطح : {factory['machine_level']}\n\n"
        f"🌟 سطح کارخونه : {factory['factory_level']}\n"
        f"┘─ {xp}xᴘ / {xp_needed}xᴘ {bar}\n\n"
        "🧮 شما درحال مدیریت کارخانه خود میباشید."
    )

def factory_main_markup(owner_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 برداشت", callback_data=f"fprod|{owner_id}"))
    markup.row(
        types.InlineKeyboardButton("👷‍♂️ کارکنان", callback_data=f"fworkers|{owner_id}"),
        types.InlineKeyboardButton("🧳 انبار", callback_data=f"fwarehouse|{owner_id}"),
    )
    markup.add(types.InlineKeyboardButton("🖨 دستگاه‌های حفاری", callback_data=f"fmachine|{owner_id}"))
    markup.add(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{owner_id}"))
    return markup

@bot.message_handler(func=lambda m: m.text and m.text.strip() == "حفاری")
def factory_entry(message):
    if not get_user(message.from_user.id):
        bot.reply_to(message, "اول /start بزن.")
        return
    factory = sync_factory_production(get_factory(message.from_user.id))
    if not factory:
        bot.reply_to(message, "❌ خطا در بارگذاری کارخونه. دوباره امتحان کن.")
        return
    name = get_display_name(message.from_user)
    bot.reply_to(message, factory_panel_text(factory, name), reply_markup=factory_main_markup(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("fback|"))
def factory_back(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    factory = sync_factory_production(get_factory(owner_id))
    name = get_display_name(call.from_user)
    safe_edit_message(
        factory_panel_text(factory, name), call.message.chat.id, call.message.message_id,
        reply_markup=factory_main_markup(owner_id)
    )

# ---------- تولید (برداشت از انبار) ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("fprod|"))
def factory_production_prompt(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    factory = sync_factory_production(get_factory(owner_id))
    stored = int(factory["warehouse_stored"])
    if stored <= 0:
        bot.answer_callback_query(call.id, "هنوز الماسی توی انبار جمع نشده!", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = f"⛏ چند تا الماس می‌خوای از انبار برداری؟\n📦 موجودی انبار: {stored:,} الماس\n(می‌تونی مثلاً 5000 یا 5k بفرستی)"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.register_next_step_handler(call.message, factory_production_step, owner_id, call.message.message_id)

def factory_production_step(message, expected_user_id, panel_msg_id):
    if message.from_user.id != expected_user_id:
        bot.register_next_step_handler(message, factory_production_step, expected_user_id, panel_msg_id)
        return
    factory = sync_factory_production(get_factory(expected_user_id))
    stored = int(factory["warehouse_stored"])
    amount = parse_amount(message.text)
    if amount is None or amount <= 0:
        msg = bot.reply_to(message, f"❌ مبلغ نامعتبره. بین 1 تا {stored:,} بفرست:")
        bot.register_next_step_handler(msg, factory_production_step, expected_user_id, panel_msg_id)
        return
    if amount > stored:
        msg = bot.reply_to(message, f"❌ انبار فقط {stored:,} الماس داره. یه مقدار کمتر بفرست:")
        bot.register_next_step_handler(msg, factory_production_step, expected_user_id, panel_msg_id)
        return

    new_stored = stored - amount
    update_diamonds(expected_user_id, amount)
    xp_gain = amount // FACTORY_XP_PER_HARVEST_UNIT
    new_xp = factory["factory_xp"] + xp_gain
    new_level = factory["factory_level"]
    needed = factory_xp_needed(new_level)
    leveled_up = False
    while needed > 0 and new_xp >= needed:
        new_xp -= needed
        new_level += 1
        leveled_up = True
        needed = factory_xp_needed(new_level)
    try:
        supabase.table("factories").update({
            "warehouse_stored": new_stored, "factory_xp": new_xp, "factory_level": new_level
        }).eq("user_id", expected_user_id).execute()
    except Exception as e:
        logging.error(f"خطا در ثبت برداشت کارخونه: {e}")

    level_line = f"\n🎉 سطح کارخونه رفت بالا! سطح جدید: {new_level}" if leveled_up else ""
    result = bot.reply_to(
        message,
        f"✅ {amount:,} 💎 از انبار برداشت شد و به موجودیت اضافه شد.{level_line}"
    )
    if result:
        auto_return_to_factory(message.chat.id, result.message_id, expected_user_id, 5)

# ---------- انبار ----------
def render_warehouse_panel(call, owner_id, factory):
    level = factory["warehouse_level"]
    cap = factory_warehouse_capacity(level)
    cost = factory_warehouse_upgrade_cost(level)
    markup = types.InlineKeyboardMarkup()
    if level < FACTORY_MAX_LEVEL:
        next_cap = factory_warehouse_capacity(level + 1)
        text = (
            "🧳 انبار کارخونه\n\n"
            f"⭐️ سطح فعلی: {level}\n"
            f"🔺 ظرفیت فعلی: {int(factory['warehouse_stored']):,} / {cap:,} الماس\n\n"
            f"⬆️ هزینه ارتقا به سطح {level + 1}: {cost:,} 💎\n"
            f"🔺 ظرفیت بعد از ارتقا: {next_cap:,} الماس"
        )
        markup.add(types.InlineKeyboardButton(f"⬆️ ارتقا ({cost:,} 💎)", callback_data=f"fwhup|{owner_id}"))
    else:
        text = (
            "🧳 انبار کارخونه\n\n"
            f"⭐️ سطح فعلی: {level} (حداکثر)\n"
            f"🔺 ظرفیت فعلی: {int(factory['warehouse_stored']):,} / {cap:,} الماس\n\n"
            "🏆 انبار به حداکثر سطح رسیده است."
        )
    markup.add(types.InlineKeyboardButton("🔙 بازگشت به پنل حفاری 🏗️", callback_data=f"fback|{owner_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fwarehouse|"))
def factory_warehouse_panel(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    render_warehouse_panel(call, owner_id, sync_factory_production(get_factory(owner_id)))

@bot.callback_query_handler(func=lambda call: call.data.startswith("fwhup|"))
def factory_warehouse_upgrade(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    factory = sync_factory_production(get_factory(owner_id))
    level = factory["warehouse_level"]
    if level >= FACTORY_MAX_LEVEL:
        bot.answer_callback_query(call.id, "🏆 انبار به حداکثر سطح ۱۰ رسیده است.", show_alert=True)
        render_warehouse_panel(call, owner_id, factory)
        return
    cost = factory_warehouse_upgrade_cost(level)
    if get_balance(owner_id) < cost:
        bot.answer_callback_query(call.id, "💎 موجودی کافی نیست!", show_alert=True)
        return
    update_diamonds(owner_id, -cost)
    try:
        supabase.table("factories").update({"warehouse_level": level + 1}).eq("user_id", owner_id).execute()
    except Exception as e:
        logging.error(f"خطا در ارتقای انبار: {e}")
    bot.answer_callback_query(call.id, "✅ ارتقا انجام شد!")
    factory["warehouse_level"] = level + 1
    render_warehouse_panel(call, owner_id, factory)

# ---------- کارکنان ----------
def render_workers_panel(call, owner_id, factory):
    level = factory["workers_level"]
    max_workers = factory_workers_max(level)
    hired = factory["workers_hired"]
    wage = factory_wage_per_worker(level)
    upgrade_cost = factory_workers_upgrade_cost(level)
    if level < FACTORY_MAX_LEVEL:
        text = (
            "👷‍♂️ کارگران حفار\n\n"
            f"⭐️ سطح فعلی: {level}\n"
            f"😺 کارگران: {hired} / {max_workers}\n"
            f"💰 دستمزد هر کارگر: {wage:,} 💎 در روز\n\n"
            f"⬆️ هزینه ارتقا به سطح {level + 1}: {upgrade_cost:,} 💎\n"
            f"😺 حداکثر کارگر بعد از ارتقا: {factory_workers_max(level + 1)}\n\n"
            "ℹ️ هر کارگر به‌صورت خودکار در تولید الماس سهم دارد."
        )
    else:
        text = (
            "👷‍♂️ کارگران حفار\n\n"
            f"⭐️ سطح فعلی: {level} (حداکثر)\n"
            f"😺 کارگران: {hired} / {max_workers}\n"
            f"💰 دستمزد هر کارگر: {wage:,} 💎 در روز\n\n"
            "🏆 سطح کارکنان به حداکثر رسیده است.\n"
            "ℹ️ هر کارگر به‌صورت خودکار در تولید الماس سهم دارد."
        )
    markup = types.InlineKeyboardMarkup()
    if hired < max_workers:
        markup.add(types.InlineKeyboardButton("➕ استخدام یک کارگر", callback_data=f"fhire|{owner_id}"))
    if level < FACTORY_MAX_LEVEL:
        markup.add(types.InlineKeyboardButton(f"⬆️ ارتقا ({upgrade_cost:,} 💎)", callback_data=f"fwkup|{owner_id}"))
    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"fback|{owner_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fworkers|"))
def factory_workers_panel(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    render_workers_panel(call, owner_id, sync_factory_production(get_factory(owner_id)))

@bot.callback_query_handler(func=lambda call: call.data.startswith("fhire|"))
def factory_hire_worker(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    factory = sync_factory_production(get_factory(owner_id))
    max_workers = factory_workers_max(factory["workers_level"])
    if factory["workers_hired"] >= max_workers:
        bot.answer_callback_query(call.id, "به حداکثر ظرفیت کارگر رسیدی!", show_alert=True)
        return
    new_hired = min(factory["workers_hired"] + 1, max_workers)
    try:
        supabase.table("factories").update({"workers_hired": new_hired}).eq("user_id", owner_id).execute()
    except Exception as e:
        logging.error(f"خطا در استخدام کارگر: {e}")
    bot.answer_callback_query(call.id, "✅ کارگر استخدام شد!")
    factory["workers_hired"] = new_hired
    render_workers_panel(call, owner_id, factory)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fwkup|"))
def factory_workers_upgrade(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    factory = sync_factory_production(get_factory(owner_id))
    level = factory["workers_level"]
    if level >= FACTORY_MAX_LEVEL:
        bot.answer_callback_query(call.id, "🏆 سطح کارکنان به حداکثر ۱۰ رسیده است.", show_alert=True)
        render_workers_panel(call, owner_id, factory)
        return
    cost = factory_workers_upgrade_cost(level)
    if get_balance(owner_id) < cost:
        bot.answer_callback_query(call.id, "💎 موجودی کافی نیست!", show_alert=True)
        return
    update_diamonds(owner_id, -cost)
    try:
        supabase.table("factories").update({"workers_level": level + 1}).eq("user_id", owner_id).execute()
    except Exception as e:
        logging.error(f"خطا در ارتقای کارگران: {e}")
    bot.answer_callback_query(call.id, "✅ ارتقا انجام شد!")
    factory["workers_level"] = level + 1
    render_workers_panel(call, owner_id, factory)

# ---------- دستگاه حفاری ----------
def render_machine_panel(call, owner_id, factory):
    level = factory["machine_level"]
    drill = factory_drill_seconds(level)
    cost = factory_machine_upgrade_cost(level)
    next_drill = factory_drill_seconds(level + 1)
    markup = types.InlineKeyboardMarkup()
    if level < FACTORY_MAX_LEVEL:
        text = (
            "🖨 دستگاه حفاری\n\n"
            f"⭐️ سطح فعلی: {level}\n"
            f"⏳ زمان در آوردن هر الماس: {drill:.1f} ثانیه\n\n"
            f"⬆️ هزینه ارتقا به سطح {level + 1}: {cost:,} 💎\n"
            f"⏳ زمان جدید بعد از ارتقا: {next_drill:.1f} ثانیه"
        )
        markup.add(types.InlineKeyboardButton(f"⬆️ ارتقا ({cost:,} 💎)", callback_data=f"fmcup|{owner_id}"))
    else:
        text = (
            "🖨 دستگاه حفاری\n\n"
            f"⭐️ سطح فعلی: {level} (حداکثر)\n"
            f"⏳ زمان در آوردن هر الماس: {drill:.1f} ثانیه\n\n"
            "🏆 دستگاه به حداکثر سطح رسیده است."
        )
    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"fback|{owner_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fmachine|"))
def factory_machine_panel(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    render_machine_panel(call, owner_id, sync_factory_production(get_factory(owner_id)))

@bot.callback_query_handler(func=lambda call: call.data.startswith("fmcup|"))
def factory_machine_upgrade(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    factory = sync_factory_production(get_factory(owner_id))
    level = factory["machine_level"]
    if level >= FACTORY_MAX_LEVEL:
        bot.answer_callback_query(call.id, "🏆 دستگاه به حداکثر سطح ۱۰ رسیده است.", show_alert=True)
        render_machine_panel(call, owner_id, factory)
        return
    cost = factory_machine_upgrade_cost(level)
    if get_balance(owner_id) < cost:
        bot.answer_callback_query(call.id, "💎 موجودی کافی نیست!", show_alert=True)
        return
    update_diamonds(owner_id, -cost)
    try:
        supabase.table("factories").update({"machine_level": level + 1}).eq("user_id", owner_id).execute()
    except Exception as e:
        logging.error(f"خطا در ارتقای دستگاه: {e}")
    bot.answer_callback_query(call.id, "✅ ارتقا انجام شد!")
    factory["machine_level"] = level + 1
    render_machine_panel(call, owner_id, factory)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ccustom|"))
def casino_custom_amount_prompt(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    game_key = call.data.split("|")[1]
    if not get_user(owner_id):
        bot.answer_callback_query(call.id, "اول /start بزن.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    safe_edit_message(
        "✏️ لطفاً مبلغ شرط رو به عدد بفرست (مثلاً 250 یا 250k):",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None
    )
    bot.register_next_step_handler(call.message, casino_custom_amount_step, game_key, owner_id, call.message.message_id)

def casino_custom_amount_step(message, game_key, expected_user_id, panel_msg_id):
    if message.from_user.id != expected_user_id:
        # پیام از یه نفر دیگه بود؛ نادیده می‌گیریم ولی منتظر پیام خودِ کاربر می‌مونیم
        bot.register_next_step_handler(message, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id)
        return

    amount = parse_amount(message.text)
    if amount is None:
        msg = bot.reply_to(message, "لطفاً یه مبلغ درست بفرست. مثال: 250 یا 250k")
        bot.register_next_step_handler(msg, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id)
        return

    if amount <= 0:
        msg = bot.reply_to(message, "مبلغ باید بزرگتر از صفر باشه. دوباره بفرست:")
        bot.register_next_step_handler(msg, casino_custom_amount_step, game_key, expected_user_id, panel_msg_id)
        return

    user = message.from_user
    if get_balance(user.id) < amount:
        bot.reply_to(message, "💎 موجودی الماس شما کافی نیست!")
        return

    update_diamonds(user.id, -amount)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("❌ لغو بازی", callback_data=f"ccancel|{panel_msg_id}"),
        types.InlineKeyboardButton("✅ پیوستن به بازی", callback_data="cjoin"),
    )
    markup.row(types.InlineKeyboardButton("🤖 بازی با ربات", callback_data="cbotplay"))

    text = (
        f"{CASINO_GAMES[game_key]} بازی {CASINO_GAME_NAMES[game_key]}\n"
        f"💎 مبلغ شرط: {amount}\n\n"
        f"👤 بازیکن اول: {display_name_with_tag(user.id, get_display_name(user))}\n"
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
        f"⚔️ {casino_name_with_tag(game['player1'])} در برابر {casino_name_with_tag(game['player2'])}\n"
        f"💎 مبلغ: {game['bet']}\n\n"
        f"🎲 حالا هر دو بازیکن باید خودشون {emoji} رو همینجا تو چت بفرستن!",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

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
        f"⚔️ {casino_name_with_tag(game['player1'])} در برابر 🤖 ربات\n"
        f"💎 مبلغ: {game['bet']}\n\n"
        f"🎲 حالا خودت {emoji} رو همینجا تو چت بفرست، بعدش ربات هم می‌ندازه!",
        chat_id=game["chat_id"], message_id=msg_id, reply_markup=markup
    )

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
        p1_display = casino_name_with_tag(player1)
        p2_display = casino_name_with_tag(player2)
        result_text = (
            f"{emoji} شرط تموم شد!\n"
            f"💎 مبلغ: {bet}\n"
            f"⚔️ {p1_display} در برابر {p2_display}\n\n"
            f"🤝 مساوی شد!\n"
            f"امتیاز {p1_display} : {score1}\n"
            f"امتیاز {p2_display} : {score2}\n\n"
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
            final_amount, tax, loan_repay, tax_rate_used, loan_rate_used = calculate_payout(winner_id, total_pot, context="casino")
            update_diamonds(winner_id, final_amount)
            if get_user(TAX_RECEIVER_ID):
                update_diamonds(TAX_RECEIVER_ID, tax)
            if get_user(winner_id):
                register_casino_win(winner_id, chat_id, winner["name"], amount_won=final_amount)
        else:
            final_amount = None
            loan_repay = 0
            tax = int(total_pot * TAX_RATE)
            tax_rate_used = TAX_RATE
            loan_rate_used = LOAN_TAX_RATE

        extra_line = f"💳 کسر بابت وام ({int(loan_rate_used*100)}٪): {loan_repay}\n" if loan_repay > 0 else ""
        p1_display = casino_name_with_tag(player1)
        p2_display = casino_name_with_tag(player2)
        winner_display = casino_name_with_tag(winner)
        loser_display = casino_name_with_tag(loser)
        result_text = (
            f"{emoji} شرط تموم شد!\n"
            f"💎 مبلغ: {bet}\n"
            f"⚔️ {p1_display} در برابر {p2_display}\n\n"
            f"🏆 برنده: {winner_display}\n"
            f"😢 بازنده: {loser_display}\n\n"
            f"امتیاز {p1_display} : {score1}\n"
            f"امتیاز {p2_display} : {score2}\n\n"
            f"💰 مبلغ برد: {total_pot}\n"
            f"🏛 مالیات ({int(tax_rate_used*100)}٪): {tax}\n"
            f"{extra_line}"
            f"✅ مبلغ نهایی برنده: {final_amount if final_amount is not None else '—'}"
        )

    safe_edit_message(result_text, chat_id=chat_id, message_id=msg_id, reply_markup=None)

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["گردونه", "گردونه الماس"])
def text_spin(message):
    uid=message.from_user.id
    if not get_user(uid): bot.reply_to(message,"اول /start بزن."); return
    now=int(time.time()); elapsed=now-get_last_spin(uid); cooldown=SPIN_COOLDOWN_HOURS*3600
    if elapsed < cooldown:
        rem=cooldown-elapsed; bot.reply_to(message,f"⏳ گردونه آماده نیست. {rem//3600} ساعت و {(rem%3600)//60} دقیقه مونده."); return
    won=random.randint(SPIN_MIN,SPIN_MAX); update_diamonds(uid,won); set_last_spin(uid,now)
    register_daily_gift(uid,message.chat.id,get_display_name(message.from_user))
    bot.reply_to(message,f"🎡 گردونه چرخید!\n💎 تبریک، {won:,} الماس بردی!\n💰 موجودی جدید: {get_balance(uid):,} 💎")

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["جام ها", "جام‌ها", "جام های من", "جام‌های من"])
def text_trophies(message):
    uid=message.from_user.id
    if not get_user(uid): bot.reply_to(message,"اول /start بزن."); return
    bot.reply_to(message,trophies_text_for_user(uid))

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["جعبه شانس", "جعبه"])
def text_lootbox(message):
    uid=message.from_user.id
    if not get_user(uid): bot.reply_to(message,"اول /start بزن."); return
    if get_balance(uid)<LOOTBOX_COST: bot.reply_to(message,f"📦 برای باز کردن جعبه شانس {LOOTBOX_COST:,} 💎 لازم داری."); return
    update_diamonds(uid,-LOOTBOX_COST); desc,kind,value=_pick_lootbox_prize()
    if kind=="diamond": update_diamonds(uid,value)
    elif kind=="tag":
        tag=random.choice(LOOTBOX_TAGS); _apply_cosmetic(uid,"tag",tag); desc=f"👑 لقب ویژه: {tag} (اعتبار ۱ هفته)\nبرای نمایشش، از بخش «لقب‌ها» تو حساب کاربری انتخابش کن."
    elif kind=="trophy":
        available=[k for k in RARE_TROPHY_KEYS if k not in get_user_trophies(uid)]
        if available:
            key=available[0]; award_trophy(uid,key,chat_id=None,display_name=get_display_name(message.from_user)); update_diamonds(uid,RARE_TROPHY_DIAMOND_REWARD); desc=f"🏅 جام نادر: {TROPHIES[key][0]} + 💎 {RARE_TROPHY_DIAMOND_REWARD:,} الماس هدیه!"
        else: update_diamonds(uid,500_000); desc="🏅 جام الماس رو قبلاً داری! به‌جاش 💎 500,000 الماس گرفتی."
    bot.reply_to(message,f"📦 جعبه شانس باز شد!\n\n🎉 جایزه تو: {desc}\n\n💰 موجودی جدید: {get_balance(uid):,} 💎")

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["تورنومنت", "تورنامنت"])
def text_tournament(message):
    t=get_active_tournament()
    if not t: bot.reply_to(message,"🏆 هیچ تورنومنتی فعال نیست.\nلطفاً بعداً مراجعه کنید."); return
    tid=t["tournament_id"]; ranking=get_tournament_ranking(tid,limit=10); prizes=t["prizes"]; text="🏆 تورنومنت ربات شرط‌بندی\n\n"
    if ranking:
        text+="🔹 رتبه‌بندی فعلی:\n"
        for idx,(uid,votes) in enumerate(ranking,1):
            u=get_user(uid) or {}; name=display_name_with_tag(uid,u.get("username") or f"کاربر {uid}"); text+=f"{idx}. {name} — {votes} رأی\n"
    else: text+="هنوز رأی‌ای ثبت نشده است.\n"
    if prizes:
        text+="\n🎁 جوایز:\n"
        for rank in range(1,11):
            prize=prizes.get(str(rank),0)
            if prize>0: text+=f"نفر {rank}: {prize:,} 💎\n"
    bot.reply_to(message,text)

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
        name = display_name_with_tag(user_id, name)
        text += f"{idx}. {name} — 💎 {diamonds}\n"

    markup = back_to_main_menu_markup()
    bot.reply_to(message, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["رنک", "رتبه بندی"])
def text_rank(message):
    cmd_rank(message)

# ================== جواهری / انگشترسازی ==================
def _get_jewelry_inventory(user_id):
    try:
        row = supabase.table("jewelry_inventory").select("*").eq("user_id", user_id).execute()
        if row.data:
            return row.data[0]
        data = {"user_id": user_id, "legacy_migrated": True}
        data.update({k: 0 for k in JEWELRY_GEMS})
        # الماس‌های انگشتر قدیمی را فقط یک‌بار به سنگ‌های جدید تبدیل می‌کنیم.
        legacy_count = int((get_user(user_id) or {}).get("ring_diamonds", 0) or 0)
        for _ in range(legacy_count):
            data[_choose_jewelry_gem()] += 1
        supabase.table("jewelry_inventory").insert(data).execute()
        return data
    except Exception as e:
        logging.error(f"خطا در دریافت موجودی جواهری {user_id}: {e}")
        return {"user_id": user_id, "legacy_migrated": True, **{k: 0 for k in JEWELRY_GEMS}}

def _jewelry_panel_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    for key, gem in JEWELRY_GEMS.items():
        markup.add(types.InlineKeyboardButton(
            f"{gem['emoji']} {gem['name']} ({_get_jewelry_inventory(user_id).get(key, 0) or 0})",
            callback_data=f"jewel_select|{user_id}|{key}"
        ))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}"))
    return markup

def _jewelry_text(user_id):
    inv = _get_jewelry_inventory(user_id)
    lines = ["💎💍 جواهری / انگشترسازی", "", "الماس‌های انگشتر شما به‌صورت شانسی به سنگ‌های زیر تبدیل شده‌اند:", ""]
    for tier, title in [("common", "🟢 معمولی"), ("rare", "🔵 کمیاب"), ("epic", "🟣 حماسی"), ("legendary", "🔴 افسانه‌ای")]:
        lines.append(title)
        for key in JEWELRY_TIER_GEMS[tier]:
            g = JEWELRY_GEMS[key]
            lines.append(f"{g['emoji']} {g['name']}: {int(inv.get(key, 0) or 0)} عدد — ارزش هر انگشتر {g['price']:,} 💎")
        lines.append("")
    lines.append("برای ساخت، روی سنگ موردنظر بزنید و تعداد انگشترها را انتخاب کنید.")
    lines.append("⏳ ساخت هر انگشتر ۱ ساعت و نیم زمان می‌برد.")
    return "\n".join(lines)

def _pending_jewelry_total(user_id):
    try:
        rows = supabase.table("jewelry_orders").select("payout_total").eq("user_id", user_id).eq("claimed", False).lte("finish_at", datetime.utcnow().isoformat()).execute()
        return sum(int(r.get("payout_total", 0) or 0) for r in (rows.data or []))
    except Exception as e:
        logging.error(f"خطا در محاسبه دریافتی جواهری {user_id}: {e}")
        return 0

def _jewelry_main_markup(user_id):
    markup = _jewelry_panel_markup(user_id)
    total = _pending_jewelry_total(user_id)
    if total > 0:
        markup.add(types.InlineKeyboardButton(f"🌹 دریافت {total:,} 💎", callback_data=f"jewel_claim|{user_id}"))
    return markup

def _jewelry_quantity_markup(user_id, key):
    inv = _get_jewelry_inventory(user_id)
    available = int(inv.get(key, 0) or 0)
    markup = types.InlineKeyboardMarkup()
    choices = [1, 5, 10, 25, 50, available]
    seen = set()
    for qty in choices:
        if qty <= 0 or qty > available or qty in seen:
            continue
        seen.add(qty)
        total_time = qty * JEWELRY_WORK_SECONDS
        markup.add(types.InlineKeyboardButton(f"{qty} عدد — ⏳ {total_time // 3600}س {(total_time % 3600)//60}د", callback_data=f"jewel_start|{user_id}|{key}|{qty}"))
    markup.add(types.InlineKeyboardButton("↩️ بازگشت", callback_data=f"jewel_menu|{user_id}"))
    return markup

@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["انگشتر سازی", "انگشترسازی", "زرگری", "جواهری"])
def text_jewelry(message):
    user_id = message.from_user.id
    if not get_user(user_id):
        bot.reply_to(message, "اول /start بزن.")
        return
    bot.reply_to(message, _jewelry_text(user_id), reply_markup=_jewelry_main_markup(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "jewel_menu" or call.data.startswith("jewel_menu|"))
def jewelry_menu(call):
    user_id = check_panel_owner(call) if "|" in call.data else call.from_user.id
    if user_id is None:
        return
    bot.answer_callback_query(call.id)
    safe_edit_message(_jewelry_text(user_id), call.message.chat.id, call.message.message_id, _jewelry_main_markup(user_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("jewel_select|"))
def jewelry_select(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    key = parts[2]
    if key not in JEWELRY_GEMS:
        bot.answer_callback_query(call.id, "سنگ نامعتبر است.", show_alert=True)
        return
    inv = _get_jewelry_inventory(user_id)
    available = int(inv.get(key, 0) or 0)
    if available <= 0:
        bot.answer_callback_query(call.id, "از این سنگ نداری.", show_alert=True)
        return
    g = JEWELRY_GEMS[key]
    bot.answer_callback_query(call.id)
    text = (f"{g['emoji']} انگشتر {g['name']}\n\n"
            f"💎 موجودی سنگ: {available}\n"
            f"💰 ارزش هر انگشتر: {g['price']:,} الماس\n"
            "⏳ ساخت هر انگشتر: ۱ ساعت و نیم\n\n"
            "تعداد انگشترهایی که می‌خواهی بسازی را انتخاب کن:")
    safe_edit_message(text, call.message.chat.id, call.message.message_id, _jewelry_quantity_markup(user_id, key))

@bot.callback_query_handler(func=lambda call: call.data.startswith("jewel_start|"))
def jewelry_start(call):
    parts = call.data.split("|")
    if len(parts) != 4:
        return
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    key = parts[2]
    try:
        qty = int(parts[3])
    except ValueError:
        return
    if key not in JEWELRY_GEMS or qty <= 0:
        return
    inv = _get_jewelry_inventory(user_id)
    available = int(inv.get(key, 0) or 0)
    if available < qty:
        bot.answer_callback_query(call.id, "موجودی سنگ کافی نیست.", show_alert=True)
        return
    # کم‌کردن سنگ قبل از ثبت سفارش تا دوباره خرج نشود.
    inv[key] = available - qty
    supabase.table("jewelry_inventory").upsert(inv, on_conflict="user_id").execute()
    g = JEWELRY_GEMS[key]
    finish_at = datetime.utcnow() + timedelta(seconds=qty * JEWELRY_WORK_SECONDS)
    supabase.table("jewelry_orders").insert({
        "user_id": user_id,
        "gem_key": key,
        "quantity": qty,
        "payout_total": qty * g["price"],
        "finish_at": finish_at.isoformat(),
        "claimed": False,
    }).execute()
    bot.answer_callback_query(call.id, "ساخت انگشتر شروع شد! 💍")
    safe_edit_message(
        f"💍 ساخت {qty} انگشتر {g['name']} شروع شد!\n\n"
        f"💰 مبلغ نهایی: {qty * g['price']:,} 💎\n"
        f"⏳ زمان ساخت: {qty * 1.5:g} ساعت\n\n"
        "بعد از تمام شدن زمان، دکمه دریافت در پنل جواهری ظاهر می‌شود. 🌹",
        call.message.chat.id, call.message.message_id, _jewelry_main_markup(user_id)
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("jewel_claim|"))
def jewelry_claim(call):
    user_id = check_panel_owner(call)
    if user_id is None:
        return
    try:
        rows = supabase.table("jewelry_orders").select("id,payout_total,finish_at").eq("user_id", user_id).eq("claimed", False).lte("finish_at", datetime.utcnow().isoformat()).execute()
        rows = rows.data or []
        total = sum(int(r.get("payout_total", 0) or 0) for r in rows)
        if total <= 0:
            bot.answer_callback_query(call.id, "هنوز انگشتر آماده‌ای برای دریافت نداری.", show_alert=True)
            return
        ids = [r["id"] for r in rows]
        supabase.table("jewelry_orders").update({"claimed": True}).in_("id", ids).eq("user_id", user_id).execute()
        update_diamonds(user_id, total)
        bot.answer_callback_query(call.id, f"{total:,} 💎 دریافت شد!")
        safe_edit_message(f"🌹 {total:,} الماس از انگشترهای آماده دریافت کردی! 💎\n\nموجودی کیف پول: {get_balance(user_id):,} 💎", call.message.chat.id, call.message.message_id, _jewelry_main_markup(user_id))
    except Exception as e:
        logging.error(f"خطا در دریافت جواهرات {user_id}: {e}")
        bot.answer_callback_query(call.id, "خطایی رخ داد. دوباره امتحان کن.", show_alert=True)

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

# ================== بخش تورنومنت ==================
@bot.callback_query_handler(func=lambda call: call.data == "tournament_menu" or call.data.startswith("tournament_menu|"))
def tournament_menu(call):
    if "|" in call.data:
        user_id = check_panel_owner(call)
        if user_id is None:
            return
    else:
        user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    if not get_user(user_id):
        bot.send_message(call.message.chat.id, "اول /start بزن.")
        return

    tournament = get_active_tournament()
    if not tournament:
        text = "🏆 هیچ تورنومنتی فعال نیست.\nلطفاً بعداً مراجعه کنید."
        markup = back_to_main_menu_markup(user_id)
        safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
        return

    tournament_id = tournament['tournament_id']
    prizes = tournament['prizes']  # json

    # دریافت رتبه‌بندی
    ranking = get_tournament_ranking(tournament_id, limit=10)
    text = "🏆 تورنومنت ربات شرطبندی\n\n"
    if ranking:
        text += "🔹 رتبه‌بندی فعلی:\n"
        for idx, (uid, votes) in enumerate(ranking, 1):
            user_info = get_user(uid)
            name = user_info['username'] if user_info and user_info['username'] else f"کاربر {uid}"
            text += f"{idx}. {name} — {votes} رأی\n"
    else:
        text += "هنوز رأی‌ای ثبت نشده است.\n"

    # نمایش جوایز
    if prizes:
        text += "\n🎁 جوایز:\n"
        for rank in range(1, 11):
            prize = prizes.get(str(rank), 0)
            if prize > 0:
                text += f"نفر {rank}: {prize} 💎\n"

    markup = types.InlineKeyboardMarkup()
    # نکته: دریافت کد فقط برای صاحب پنل قفله (owner embedded)، ولی ثبت رأی عمداً
    # باز می‌مونه چون هر کسی که این پیام رو می‌بینه باید بتونه با کد یه نفر دیگه رأی بده.
    markup.add(types.InlineKeyboardButton("📋 دریافت کد رأی", callback_data=f"getvote|{tournament_id}|{user_id}"))
    markup.add(types.InlineKeyboardButton("✍️ ثبت رأی", callback_data=f"submitvote|{tournament_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("getvote|"))
def get_vote_code(call):
    owner_id = check_panel_owner(call)
    if owner_id is None:
        return
    bot.answer_callback_query(call.id)
    tournament_id = int(call.data.split("|")[1])
    user_id = owner_id
    code = get_user_code(tournament_id, user_id)
    if code:
        text = f"🔑 کد رأی شما برای این تورنومنت:\n`{code}`\n\nاین کد را با دوستان خود به اشتراک بگذارید تا به شما رأی دهند."
    else:
        text = "خطا در دریافت کد. لطفاً دوباره تلاش کنید."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"tournament_menu|{owner_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("submitvote|"))
def submit_vote_prompt(call):
    bot.answer_callback_query(call.id)
    tournament_id = int(call.data.split("|")[1])
    voter_id = call.from_user.id  # عمداً باز است: هر کسی که این پیام رو ببینه می‌تونه رأی بده
    text = "✍️ لطفاً کد رأی شخص مورد نظر را وارد کنید (۶ کاراکتر):"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"tournament_menu|{voter_id}"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.register_next_step_handler(call.message, process_vote_code, tournament_id, voter_id, call.message.message_id)

def process_vote_code(message, tournament_id, voter_id, original_msg_id):
    if message.from_user.id != voter_id:
        return
    code = message.text.strip().lower()
    if len(code) != 6:
        bot.reply_to(message, "❌ کد باید دقیقاً ۶ کاراکتر باشد. دوباره تلاش کنید.")
        bot.register_next_step_handler(message, process_vote_code, tournament_id, voter_id, original_msg_id)
        return
    # ثبت رأی
    success, msg = add_vote(tournament_id, voter_id, code)
    if success:
        # حذف پیام ورودی و نمایش نتیجه در پیام اصلی
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except:
            pass
        # ویرایش پیام اصلی
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 بازگشت به تورنومنت", callback_data=f"tournament_menu|{voter_id}"))
        safe_edit_message(f"✅ رأی شما با موفقیت ثبت شد.\n{msg}", chat_id=message.chat.id, message_id=original_msg_id, reply_markup=markup)
    else:
        bot.reply_to(message, f"❌ {msg}\nلطفاً کد را دوباره وارد کنید:")
        bot.register_next_step_handler(message, process_vote_code, tournament_id, voter_id, original_msg_id)

# ================== بخش پنل مدیریت ==================
@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "⚙️ پنل مدیریت\nلطفاً یکی از گزینه‌ها را انتخاب کنید:"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏆 شروع تورنومنت", callback_data="admin_start_tournament"))
    markup.add(types.InlineKeyboardButton("🏁 پایان تورنومنت", callback_data="admin_end_tournament"))
    markup.add(types.InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast"))
    markup.add(types.InlineKeyboardButton("➕ افزودن الماس", callback_data="admin_add_diamond"))
    markup.add(types.InlineKeyboardButton("➖ کم کردن الماس", callback_data="admin_remove_diamond"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="mainmenu"))
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ---------- شروع تورنومنت ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_start_tournament")
def admin_start_tournament(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    # بررسی وجود تورنومنت فعال
    active = get_active_tournament()
    if active:
        bot.send_message(call.message.chat.id, "⚠️ در حال حاضر یک تورنومنت فعال وجود دارد. ابتدا آن را پایان دهید.")
        return
    # شروع مراحل دریافت جوایز
    text = "🎯 لطفاً جایزه نفر اول را به عدد وارد کنید (یا 0 برای عدم جایزه):"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.register_next_step_handler(call.message, admin_set_prize_step, 1, {})  # step 1, prizes dict

# ========== اصلاح تابع شروع تورنومنت ==========
def admin_set_prize_step(message, rank, prizes):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw = (message.text or "").strip()
    amount = 0 if raw == "0" else parse_amount(raw)
    if amount is None:
        msg = bot.reply_to(message, "لطفاً یه مبلغ درست وارد کنید (مثلاً 1000 یا 1میل) یا 0 برای عدم جایزه:")
        bot.register_next_step_handler(msg, admin_set_prize_step, rank, prizes)
        return
    prizes[str(rank)] = amount
    if rank < 10:
        next_rank = rank + 1
        text = f"جایزه نفر {next_rank} را وارد کنید (یا 0):"
        bot.reply_to(message, text)
        bot.register_next_step_handler(message, admin_set_prize_step, next_rank, prizes)
    else:
        # همه جوایز دریافت شد، ایجاد تورنومنت
        try:
            # ذخیره به‌صورت دیکشنری (Supabase خودش به JSONB تبدیل می‌کند)
            supabase.table("tournaments").insert({
                "status": "active",
                "prizes": prizes   # ← اینجا دیگر json.dumps نمی‌زنیم
            }).execute()
            bot.reply_to(message, "✅ تورنومنت با موفقیت شروع شد! کاربران می‌توانند وارد بخش تورنومنت شوند.")
        except Exception as e:
            logging.error(f"خطا در شروع تورنومنت: {e}")
            bot.reply_to(message, f"❌ خطا در شروع تورنومنت: {e}")

# ---------- پایان تورنومنت ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_end_tournament")
def admin_end_tournament(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    tournament = get_active_tournament()
    if not tournament:
        bot.send_message(call.message.chat.id, "❌ هیچ تورنومنت فعالی وجود ندارد.")
        return
    tid = tournament['tournament_id']
    success, msg = end_tournament(tid)
    bot.send_message(call.message.chat.id, msg)
    if success:
        # ارسال پیام به همه کاربران (اختیاری)
        pass

# ---------- پیام همگانی ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "📢 لطفاً پیام همگانی خود را بنویسید (متن یا با فرمت HTML):"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.register_next_step_handler(call.message, admin_send_broadcast)

def admin_send_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    broadcast_text = message.text
    # دریافت تمام کاربران
    try:
        users = supabase.table("users").select("user_id").execute()
        if not users.data:
            bot.reply_to(message, "هیچ کاربری یافت نشد.")
            return
        count = 0
        for user in users.data:
            try:
                bot.send_message(user['user_id'], broadcast_text)
                count += 1
                time.sleep(0.05)  # جلوگیری از محدودیت
            except Exception:
                continue
        bot.reply_to(message, f"✅ پیام به {count} کاربر ارسال شد.")
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در ارسال پیام همگانی: {e}")

# ---------- افزودن الماس (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_diamond")
def admin_add_diamond_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "➕ لطفاً آیدی عددی کاربر و مقدار الماس را به صورت زیر وارد کنید:\n`<user_id> <amount>`\nمثال: `123456789 100`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.register_next_step_handler(call.message, admin_add_diamond_execute)

def admin_add_diamond_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 and parts[0].isdigit() else None
    if len(parts) != 2 or not parts[0].isdigit() or amount is None:
        bot.reply_to(message, "❌ فرمت نامعتبر. مجدداً تلاش کنید. مثال: 123456789 100 یا 123456789 100k")
        return
    user_id = int(parts[0])
    if not get_user(user_id):
        bot.reply_to(message, "کاربر یافت نشد.")
        return
    update_diamonds(user_id, amount)
    bot.reply_to(message, f"✅ {amount} الماس به کاربر {user_id} اضافه شد. موجودی جدید: {get_balance(user_id)}")

# ---------- کم کردن الماس (مدیریت) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_remove_diamond")
def admin_remove_diamond_prompt(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "➖ لطفاً آیدی عددی کاربر و مقدار الماس را به صورت زیر وارد کنید:\n`<user_id> <amount>`\nمثال: `123456789 50`"
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.register_next_step_handler(call.message, admin_remove_diamond_execute)

def admin_remove_diamond_execute(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    amount = parse_amount(parts[1]) if len(parts) == 2 and parts[0].isdigit() else None
    if len(parts) != 2 or not parts[0].isdigit() or amount is None:
        bot.reply_to(message, "❌ فرمت نامعتبر. مجدداً تلاش کنید. مثال: 123456789 50 یا 123456789 50k")
        return
    user_id = int(parts[0])
    if not get_user(user_id):
        bot.reply_to(message, "کاربر یافت نشد.")
        return
    balance = get_balance(user_id)
    deduct = min(amount, balance)
    update_diamonds(user_id, -deduct)
    bot.reply_to(message, f"✅ {deduct} الماس از کاربر {user_id} کم شد. موجودی جدید: {get_balance(user_id)}")

# ================== Webhook ==================
@app.route("/", methods=["GET"])
def health_check():
    return "Bot is running!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.is_json:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)

        # همه پیام‌های معمولی تلگرام که در فیلد message می‌آیند شمارش می‌شوند.
        # از جمله متن، عکس، ویدیو، استیکر، گیف، صدا، ویس، فایل، لوکیشن،
        # مخاطب، دایس و پیام‌های سرویس.
        incoming_message = getattr(update, "message", None)
        if incoming_message is not None:
            try:
                process_group_message_for_diamond_hunt(incoming_message)
            except Exception as e:
                logging.error(
                    f"خطا در شمارش پیام گروه برای الماس: {e}"
                )

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