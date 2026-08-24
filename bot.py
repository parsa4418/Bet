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

def _add_ring_diamond(user_id):
    try:
        user = get_user(user_id)
        if not user:
            return False
        current = int(user.get("ring_diamonds", 0) or 0)
        supabase.table("users").update({"ring_diamonds": current + 1}).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logging.error(f"خطا در ثبت الماس انگشتر برای {user_id}: {e}")
        return False

def _format_attempt_history(attempts):
    lines = []
    for idx, attempt in enumerate(attempts, 1):
        lines.append(f"در تلاش {idx} {attempt['name']} {attempt['reason']}")
    return "\n".join(lines)

def _diamond_hunt_markup(attempt_no, message_id):
    """دکمه برداشتن الماس؛ هزینه فقط داخل منطق تلاش بررسی می‌شود و روی دکمه نمایش داده نمی‌شود."""
    markup = types.InlineKeyboardMarkup()
    if attempt_no <= 3:
        markup.add(types.InlineKeyboardButton(
            "💎 برداشتن الماس",
            callback_data=f"dhunt|{message_id}|{attempt_no}"
        ))
    return markup


def _expire_diamond_hunt(chat_id, message_id):
    """بعد از ۲ دقیقه الماس را منقضی می‌کند؛ فقط همان الماس را هدف می‌گیرد."""
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
        # اول پیام را بدون دکمه می‌فرستیم تا message_id واقعی تلگرام را داشته باشیم.
        msg = bot.send_message(
            chat_id,
            "یک الماس انگشتر در شهر پیدا شد.!💎\n\n"
            "این الماس قابلیت تبدیل به انگشتر داره 💍\n\n"
            "تا از دست نرفته تلاش خودت رو برای بدست آوردنش بکن ✅\n\n"
            "از دکمه زیر برای برداشتن الماس استفاده کنید❗\n\n"
            "هزینه تلاش برای بدست آوردن الماس: 50 هزار الماس💎"
        )

        # message_id واقعی را قبل از ساخت callback داخل دکمه قرار می‌دهیم.
        _set_diamond_hunt(
            chat_id,
            active=True,
            hunt_message_id=msg.message_id,
            attempts=[]
        )

        safe_edit_message(
            "یک الماس انگشتر در شهر پیدا شد.!💎\n\n"
            "این الماس قابلیت تبدیل به انگشتر داره 💍\n\n"
            "تا از دست نرفته تلاش خودت رو برای بدست آوردنش بکن ✅\n\n"
            "از دکمه زیر برای برداشتن الماس استفاده کنید❗\n\n"
            "هزینه تلاش برای بدست آوردن الماس: 50 هزار الماس💎",
            chat_id,
            msg.message_id,
            reply_markup=_diamond_hunt_markup(1, msg.message_id)
        )

        # تایمر مستقل برای همین الماس؛ بعد از ۲ دقیقه منقضی می‌شود.
        timer = threading.Timer(
            120,
            _expire_diamond_hunt,
            args=(chat_id, msg.message_id)
        )
        timer.daemon = True
        timer.start()

    except Exception as e:
        logging.error(f"خطا در ارسال الماس تصادفی گروه {chat_id}: {e}")


def process_group_message_for_diamond_hunt(message):
    """
    هر پیام ورودی از نوع message در هر گروه/سوپرگروه یک واحد حساب می‌شود.
    متن، عکس، ویدیو، استیکر، گیف، صدا، ویس، فایل، لوکیشن، مخاطب، دایس
    و پیام‌های سرویس که Telegram به شکل message می‌فرستد هم از این مسیر عبور می‌کنند.
    """
    if not message or message.chat.type not in ("group", "supergroup"):
        return

    # پیام‌های خود ربات برای شمارش ۲۰۰ پیام حساب نشوند.
    if getattr(message.from_user, "is_bot", False):
        return

    # 🔥 لاگ برای دیباگ
    logging.info(f"📩 پیام جدید از گروه {message.chat.id} - کاربر: {message.from_user.id}")

    with diamond_hunt_lock:
        row = _ensure_diamond_hunt_row(message.chat.id)
        if not row:
            return

        # 🔥 لاگ وضعیت فعلی
        logging.info(f"📊 وضعیت الماس: active={row.get('active')}, count={row.get('message_count')}")

        # اگر الماس فعاله، فقط شمارنده رو زیاد کن و برگرد
        if row.get("active"):
            count = int(row.get("message_count", 0) or 0) + 1
            _set_diamond_hunt(message.chat.id, message_count=count)
            return

        count = int(row.get("message_count", 0) or 0) + 1

        if count < DIAMOND_HUNT_MESSAGE_INTERVAL:
            _set_diamond_hunt(message.chat.id, message_count=count)
            return

        # 🔥 رسیدیم به ۲۰۰
        logging.info(f"🎯 رسیدیم به ۲۰۰ پیام! شروع الماس...")
        
        _set_diamond_hunt(message.chat.id, message_count=0)
        _start_diamond_hunt(message.chat.id)



def _user_display_from_call(call):
    if call.from_user.username:
        return f"@{call.from_user.username}"
    return call.from_user.first_name or str(call.from_user.id)


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
        name = _user_display_from_call(call)

        won = random.random() < DIAMOND_HUNT_WIN_CHANCES[attempt_no - 1]

        if won:
            _add_ring_diamond(user_id)
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
                safe_edit_message(
                    f"الماس بعد از {total_attempts} تلاش با موفقیت نجات یافت و صاحب جدید پیدا کرد.!💎\n\n"
                    f"{name} با موفقیت الماس رو بدست آورد.💍\n\n"
                    "🎁 پاداش ⬇️\n"
                    "‏┘─ یک الماس با قابلیت تبدیل به انگشتر.💍",
                    chat_id,
                    message_id,
                    reply_markup=None
                )
            except Exception as e:
                logging.error(f"خطا در نتیجه برد الماس: {e}")
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
                f"هزینه تلاش بعدی: {next_cost:,} الماس💎"
            )
            safe_edit_message(
                text,
                chat_id,
                message_id,
                reply_markup=_diamond_hunt_markup(
                    attempt_no + 1,
                    message_id
                )
            )
        else:
            text = (
                "❗ نتونستی الماس رو بگیری علت ⬇️\n\n"
                + _format_attempt_history(attempts)
            )
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
    return user.username and f"@{user.username}" or user.first_name

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
        # تغییر وضعیت تورنومنت
        supabase.table("tournaments").update({"status": "ended", "end_time": "now()"}).eq("tournament_id", tournament_id).execute()
        return True, "تورنومنت پایان یافت و جوایز توزیع شد."
    except Exception as e:
        logging.error(f"خطا در end_tournament: {e}")
        return False, f"خطا: {e}"

# ================== دکمه‌ها و توابع کمکی ==================
def main_menu_markup(user_id=None):
    markup = types.InlineKeyboardMarkup()
    # Row 1: account (single)
    markup.row(
        types.InlineKeyboardButton("👤 حساب کاربری", callback_data=f"showaccount|{user_id}" if user_id else "showaccount")
    )
    # Row 2: casino + tournament
    markup.row(
        types.InlineKeyboardButton("🎰 کازینو", callback_data=f"casinomenu|{user_id}" if user_id else "casinomenu"),
        types.InlineKeyboardButton("🏆 تورنومنت", callback_data=f"tournament_menu|{user_id}" if user_id else "tournament_menu")
    )
    # Row 3: bank + spin
    markup.row(
        types.InlineKeyboardButton("🏦 بانک الماس", callback_data=f"bankmenu|{user_id}" if user_id else "bankmenu"),
        types.InlineKeyboardButton("🎡 گردونه الماس", callback_data=f"spinwheel|{user_id}" if user_id else "spinwheel")
    )
    # Row 4: referral + help
    markup.row(
        types.InlineKeyboardButton("👥 زیرمجموعه‌گیری", callback_data=f"showreferral|{user_id}" if user_id else "showreferral"),
        types.InlineKeyboardButton("📖 راهنما", callback_data=f"showhelp|{user_id}" if user_id else "showhelp")
    )
    # Admin panel last, visible only to admins
    if user_id and user_id in ADMIN_IDS:
        markup.row(
            types.InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")
        )
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
        f"💍 الماس انگشتر: {ring_diamonds}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("💸 انتقال الماس", callback_data=f"acctransfer|{user_id}"),
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}")
    )
    bot.reply_to(message, text, reply_markup=markup)

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
        f"💍 الماس انگشتر: {ring_diamonds}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("💸 انتقال الماس", callback_data=f"acctransfer|{user_id}"),
        types.InlineKeyboardButton("🏠 منوی اصلی", callback_data=f"mainmenu|{user_id}")
    )
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

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
    markup = back_to_main_menu_markup(user_id)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

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
    only_mainmenu_markup = types.InlineKeyboardMarkup()
    only_mainmenu_markup.add(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{expected_user_id}"))
    bot.reply_to(message, f"✅ {amount:,} 💎 به حساب بانکی واریز شد.\n💰 موجودی بانک: {get_bank_balance(expected_user_id):,} 💎", reply_markup=only_mainmenu_markup)

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
    only_mainmenu_markup = types.InlineKeyboardMarkup()
    only_mainmenu_markup.add(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{expected_user_id}"))
    bot.reply_to(message, f"✅ {amount:,} 💎 از بانک برداشت شد.\n💰 موجودی بانک: {get_bank_balance(expected_user_id):,} 💎\n💎 موجودی کیف پول: {get_balance(expected_user_id):,} 💎", reply_markup=only_mainmenu_markup)

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
    only_mainmenu_markup = types.InlineKeyboardMarkup()
    only_mainmenu_markup.add(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{expected_user_id}"))
    bot.reply_to(
        message,
        f"✅ {amount:,} 💎 وام گرفتی و به موجودیت اضافه شد.\n"
        f"💳 مجموع وام فعلی: {get_loan_balance(expected_user_id):,} 💎\n"
        f"💰 موجودی جدید: {get_balance(expected_user_id):,} 💎\n"
        "⏳ وام بعدی: فردا",
        reply_markup=only_mainmenu_markup
    )

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
        "🏦 بانک الماس:\n"
        f"با نوشتن «بانک» یا «بانک الماس» حساب بانکی خودتو باز کن. افتتاح حساب {BANK_OPENING_FEE:,} 💎 هزینه دارد.\n"
        f"هر روز ساعت ۰۰:۰۰، {int(BANK_INTEREST_RATE*100)}٪ سود بانکی تا سقف {BANK_DAILY_INTEREST_MAX:,} 💎 در روز واریز می‌شود.\n"
        "داخل بانک امکان واریز، برداشت و دریافت وام وجود دارد.\n\n"
        "🎡 گردونه الماس:\n"
        f"از /start روی «گردونه الماس» بزن و بین {SPIN_MIN} تا {SPIN_MAX} 💎 شانسی ببر (هر {SPIN_COOLDOWN_HOURS} ساعت یه‌بار).\n\n"
        "🤖 بازی با ربات تو کازینو:\n"
        "بعد از انتخاب مبلغ، به‌جای «پیوستن»، «بازی با ربات» رو بزن؛ خودت ایموجی رو بنداز، ربات هم می‌ندازه و نتیجه اعلام میشه.\n\n"
        "🏆 رتبه‌بندی برترین‌ها:\n"
        "بزن /rank یا بنویس رنک\n\n"
        "🏆 تورنومنت:\n"
        "با دکمه «تورنومنت» در منوی اصلی وارد شوید.\n"
        "هر کاربر یک کد ۶ رقمی دریافت می‌کند. دوستان خود را به بات دعوت کنید تا با وارد کردن کد شما، به شما رأی دهند.\n"
        "هر کاربر فقط یک بار می‌تواند رأی دهد. پس از پایان تورنومنت، به ۱۰ نفر برتر جوایز تعیین‌شده توسط ادمین تعلق می‌گیرد."
    )
    markup = back_to_main_menu_markup(owner_id)
    safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

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
        safe_edit_message(text, call.message.chat.id, call.message.message_id, reply_markup=mine_result_markup())
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

    only_mainmenu_markup = types.InlineKeyboardMarkup()
    only_mainmenu_markup.add(types.InlineKeyboardButton("🏠 بازگشت به منو", callback_data=f"mainmenu|{expected_user_id}"))
    level_line = f"\n🎉 سطح کارخونه رفت بالا! سطح جدید: {new_level}" if leveled_up else ""
    bot.reply_to(
        message,
        f"✅ {amount:,} 💎 از انبار برداشت شد و به موجودیت اضافه شد.{level_line}",
        reply_markup=only_mainmenu_markup
    )

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