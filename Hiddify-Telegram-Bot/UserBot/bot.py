import datetime
import random

import telebot
from telebot.types import Message, CallbackQuery
from config import *
from AdminBot.templates import configs_template
from UserBot.markups import *
from UserBot.templates import *
from UserBot.content import *

import Utils.utils as utils
from Shared.common import admin_bot
from Database.dbManager import USERS_DB
from Utils import api

# *********************************** Configuration Bot ***********************************
bot = telebot.TeleBot(CLIENT_TOKEN, parse_mode="HTML")
bot.remove_webhook()
admin_bot = admin_bot()
BASE_URL = f"{urlparse(PANEL_URL).scheme}://{urlparse(PANEL_URL).netloc}"
selected_server_id = 0
# {telegram_id: {kind: buy|renewal, plan_id, uuid(optional), code_id, code, discount_amount, final_price}}
gift_code_session = {}

# *********************************** Helper Functions ***********************************

def _as_int(v, default: int = 0) -> int:
    """Best-effort int conversion.

    Handles values stored as strings like "10", "10.0", floats, ints, None.
    """
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default
# Check if message is digit
def is_it_digit(message: Message,allow_float=False, response=MESSAGES['ERROR_INVALID_NUMBER'], markup=main_menu_keyboard_markup()):
    if not message.text:
        bot.send_message(message.chat.id, response, reply_markup=markup)
        return False
    try:
        value = float(message.text) if allow_float else int(message.text)
        return True
    except ValueError:
        bot.send_message(message.chat.id, response, reply_markup=markup)
        return False

def _get_pending_invoice(chat_id: int):
    """Safely read pending invoice for a chat."""
    try:
        return USERS_DB.get_pending_invoice(chat_id)
    except Exception:
        return None


def get_renewal_context(chat_id: int):
    """Return (uuid, plan_id) for renewal flow.

    Prefer in-memory renew_subscription_dict; fall back to pending invoice in DB
    (important for auto-invoice messages after wallet top-up).
    """
    try:
        ctx = renew_subscription_dict.get(chat_id, {})
        uuid = ctx.get('uuid')
        plan_id = ctx.get('plan_id')
        if uuid and plan_id:
            return str(uuid), int(plan_id)
    except Exception:
        pass

    pending = _get_pending_invoice(chat_id)
    if pending and pending.get('kind') == 'renewal' and pending.get('uuid') and pending.get('plan_id') is not None:
        try:
            return str(pending.get('uuid')), int(pending.get('plan_id'))
        except Exception:
            return None, None
    return None, None



# Check if message is cancel
def is_it_cancel(message: Message, response=MESSAGES['CANCELED']):
    if message.text == KEY_MARKUP['CANCEL']:
        bot.send_message(message.chat.id, response, reply_markup=main_menu_keyboard_markup())
        return True
    return False


# Check if message is command
def is_it_command(message: Message):
    if message.text.startswith("/"):
        return True
    return False


# Check is it UUID, Config or Subscription Link
def type_of_subscription(text):
    if text.startswith("vmess://"):
        config = text.replace("vmess://", "")
        config = utils.base64decoder(config)
        if not config:
            return False
        uuid = config['id']
    else:
        uuid = utils.extract_uuid_from_config(text)
    return uuid

# check is user banned
def is_user_banned(user_id):
    user = USERS_DB.find_user(telegram_id=user_id)
    if user:
        user = user[0]
        if user['banned']:
            bot.send_message(user_id, MESSAGES['BANNED_USER'], reply_markup=main_menu_keyboard_markup())
            return True
    return False
# *********************************** Next-Step Handlers ***********************************
# ----------------------------------- Buy Plan Area -----------------------------------
charge_wallet = {}
renew_subscription_dict = {}


def user_channel_status(user_id):
    try:
        settings = utils.all_configs_settings()
        if settings['channel_id']:
            user = bot.get_chat_member(settings['channel_id'], user_id)
            return user.status in ['member', 'administrator', 'creator']
        else:
            return True
    except telebot.apihelper.ApiException as e:
        logging.error("ApiException: %s" % e)
        return False


def is_user_in_channel(user_id):
    settings = all_configs_settings()
    if settings['force_join_channel'] == 1:
        if not settings['channel_id']:
            return True
        if not user_channel_status(user_id):
            bot.send_message(user_id, MESSAGES['REQUEST_JOIN_CHANNEL'],
                             reply_markup=force_join_channel_markup(settings['channel_id']))
            return False
    return True




def _to_int(value, default: int = 0) -> int:
    """Safe int() conversion that preserves existing fallback behavior."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default

# -------------------- Referral (Invite Friends) --------------------
def _get_referral_settings():
    s = utils.all_configs_settings()
    # Stored amounts are in Rial (same as wallet). Commission percent is int.
    # New installs may have separate bonuses for referrer/new user; older installs may only have referral_signup_bonus_amount.
    referrer_bonus = _to_int(s.get('referral_signup_bonus_referrer_amount') or s.get('referral_signup_bonus_amount') or 0)
    new_user_bonus = _to_int(s.get('referral_signup_bonus_new_user_amount') or s.get('referral_signup_bonus_amount') or 0)
    percent = _to_int(s.get('referral_commission_percent') or 0)
    enabled = (_to_int(s.get('referral_enabled') or 0) == 1)
    dashboard_tpl = s.get('referral_dashboard_text')
    share_text = s.get('referral_share_text')
    return enabled, referrer_bonus, new_user_bonus, percent, dashboard_tpl, share_text


def _credit_wallet(telegram_id: int, amount_rial: int) -> bool:
    try:
        if amount_rial <= 0:
            return True
        wallet = USERS_DB.find_wallet(telegram_id=int(telegram_id))
        if not wallet:
            if not USERS_DB.add_wallet(int(telegram_id)):
                return False
            wallet = USERS_DB.find_wallet(telegram_id=int(telegram_id))
        wallet = wallet[0]
        new_balance = int(wallet['balance']) + int(amount_rial)
        return bool(USERS_DB.edit_wallet(int(telegram_id), balance=new_balance))
    except Exception as e:
        logging.error(f"_credit_wallet failed for {telegram_id}: {e}")
        return False


def _send_safe(chat_id: int, text: str):
    try:
        bot.send_message(int(chat_id), text)
    except Exception:
        pass


def process_referral_signup_bonus(new_user_id: int, referrer_id: int, referrer_bonus_rial: int, new_user_bonus_rial: int):
    """Pay signup bonus to both parties (if not already paid)."""
    if (referrer_bonus_rial <= 0) and (new_user_bonus_rial <= 0):
        USERS_DB.mark_referral_bonus_paid(new_user_id)
        return
    # Idempotency: only pay once per new user.
    if USERS_DB.is_referral_bonus_paid(new_user_id):
        return

    # Credit wallets
    _credit_wallet(new_user_id, new_user_bonus_rial)
    _credit_wallet(referrer_id, referrer_bonus_rial)

    # Ledger
    if referrer_bonus_rial > 0:
        USERS_DB.add_referral_earning(referrer_id, 'signup_bonus', referrer_bonus_rial, related_telegram_id=new_user_id)
    if new_user_bonus_rial > 0:
        USERS_DB.add_referral_earning(new_user_id, 'signup_bonus', new_user_bonus_rial, related_telegram_id=referrer_id)
    USERS_DB.mark_referral_bonus_paid(new_user_id)

    # Notify
    def _toman_str(rial_val: int) -> str:
        try:
            return utils.rial_to_toman(int(rial_val))
        except Exception:
            return str(int(rial_val) // 10)

    ref_bonus_toman = _toman_str(referrer_bonus_rial)
    new_bonus_toman = _toman_str(new_user_bonus_rial)

    _send_safe(
        referrer_id,
        f"یک کاربر با لینک شما عضو شد\nمبلغ {ref_bonus_toman} تومان بابت هدیه عضویت به کیف پول شما واریز شد ✅" if referrer_bonus_rial > 0 else "یک کاربر با لینک شما عضو شد ✅",
    )
    _send_safe(
        new_user_id,
        "👋 کاربر عزیز، خوش آمدید\n\n"
        "🎁 هدیه دعوت از دوستان\n"
        f"مبلغ {new_bonus_toman} تومان\n"
        "به کیف پول شما واریز شد ✅\n\n"
        "💳 می‌توانید از این اعتبار برای استفاده از خدمات ربات استفاده کنید.",
    )


def process_referral_commission(buyer_id: int, order_id: int, paid_amount_rial: int):
    enabled, _ref_bonus, _new_bonus, percent, *_ = _get_referral_settings()
    if not enabled or percent <= 0 or paid_amount_rial <= 0:
        return
    referrer_id = USERS_DB.get_referrer_of_user(int(buyer_id))
    if not referrer_id:
        return
    commission = int(int(paid_amount_rial) * int(percent) / 100)
    if commission <= 0:
        return
    # Credit + ledger (unique index prevents duplicates per order)
    if not _credit_wallet(referrer_id, commission):
        return
    USERS_DB.add_referral_earning(referrer_id, 'commission', commission, related_telegram_id=buyer_id, order_id=order_id)

    # Notify
    try:
        comm_toman = utils.rial_to_toman(int(commission))
        paid_toman = utils.rial_to_toman(int(paid_amount_rial))
    except Exception:
        comm_toman = str(int(commission) // 10)
        paid_toman = str(int(paid_amount_rial) // 10)

    _send_safe(
        referrer_id,
        f"💸 پورسانت خرید زیرمجموعه\n"
        f"مبلغ خرید: {paid_toman} تومان\n"
        f"پورسانت ({percent}٪): {comm_toman} تومان\n"
        f"به کیف پول شما واریز شد ✅\n"
        f"شماره سفارش: {order_id}",
    )
    # Note: Do not send any extra message to the buyer here.
    # The buyer already receives the standard purchase confirmation elsewhere.

# Next Step Buy From Wallet - Confirm


def get_effective_plan_price(telegram_id: int, plan: dict, kind: str = "buy", uuid: str | None = None) -> tuple[int, dict | None]:
    """Return (price_to_pay, gift_session_or_None) for the given plan and context."""
    sess = gift_code_session.get(telegram_id)
    if not sess:
        # If bot restarted or memory session is gone, try to resume from DB pending invoice.
        pending = USERS_DB.get_pending_invoice(telegram_id)
        if pending and str(pending.get('kind')) == kind and int(pending.get('plan_id')) == int(plan.get('id')):
            if kind == "renewal" and uuid and str(pending.get('uuid')) != str(uuid):
                return int(plan["price"]), None
            if pending.get('final_price') is not None:
                gift = {
                    "kind": kind,
                    "plan_id": int(plan.get('id')),
                    "uuid": pending.get('uuid'),
                    "code_id": pending.get('code_id'),
                    "code": pending.get('discount_code'),
                    "discount_amount": pending.get('discount_amount') or 0,
                    "final_price": pending.get('final_price'),
                }
                try:
                    return int(pending.get('final_price')), gift
                except Exception:
                    return int(plan["price"]), None
        return int(plan["price"]), None

    if sess.get("kind") != kind:
        return int(plan["price"]), None
    if sess.get("plan_id") != plan.get("id"):
        return int(plan["price"]), None
    if kind == "renewal" and uuid and sess.get("uuid") != uuid:
        return int(plan["price"]), None
    if sess.get("final_price") is None:
        return int(plan["price"]), None
    try:
        return int(sess["final_price"]), sess
    except Exception:
        return int(plan["price"]), None



def next_step_apply_gift_code(message: Message, plan_id: int, origin_message_id: int, renewal: bool = False, uuid: str | None = None):
    if is_it_cancel(message):
        gift_code_session.pop(message.chat.id, None)
        bot.send_message(message.chat.id, MESSAGES['GIFT_CODE_CLEARED'], reply_markup=main_menu_keyboard_markup())
        return


    # In renewal flow, uuid may be missing in auto-invoice scenarios. Recover it from pending invoice if possible.
    if renewal and not uuid:
        uuid, _pid = get_renewal_context(message.chat.id)

    code = (message.text or "").strip()
    plan = USERS_DB.find_plan(id=plan_id)
    if not plan:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
        return
    plan = plan[0]
    # In auto-invoice scenarios, renewal uuid may not be present in memory; recover it.
    if renewal and not uuid:
        uuid, _pid = get_renewal_context(message.chat.id)


    result = USERS_DB.validate_discount_code(code, message.chat.id, int(plan['price']))
    if not result.get("ok"):
        reason = result.get("reason")
        if reason == "INACTIVE":
            err = MESSAGES['GIFT_CODE_INACTIVE']
        elif reason == "EXPIRED":
            err = MESSAGES['GIFT_CODE_EXPIRED']
        elif reason == "USAGE_LIMIT":
            err = MESSAGES['GIFT_CODE_USAGE_LIMIT']
        elif reason == "ALREADY_USED":
            err = MESSAGES['GIFT_CODE_ALREADY_USED']
        elif reason == "MIN_PRICE":
            err = MESSAGES['GIFT_CODE_MIN_PRICE']
        else:
            err = MESSAGES['INVALID_GIFT_CODE']
        bot.send_message(message.chat.id, err, reply_markup=confirm_buy_plan_markup(plan_id, renewal=renewal, uuid=uuid))
        return

    gift_code_session[message.chat.id] = {
        "kind": "renewal" if renewal else "buy",
        "plan_id": plan_id,
        "uuid": uuid if renewal else None,
        "code_id": result["code_id"],
        "code": result["code"],
        "discount_amount": result["discount_amount"],
        "final_price": result["final_price"],
    }

    # Persist discount info so if the user tops up wallet or the bot restarts, the invoice can be resumed.
    try:
        USERS_DB.set_pending_invoice(
            telegram_id=message.chat.id,
            kind="renewal" if renewal else "buy",
            plan_id=int(plan_id),
            uuid=str(uuid) if (renewal and uuid) else None,
            code_id=int(result.get("code_id")) if result.get("code_id") else None,
            discount_code=result.get("code"),
            discount_amount=int(result.get("discount_amount") or 0),
            final_price=int(result.get("final_price")) if result.get("final_price") is not None else None,
        )
    except Exception:
        pass

    # Update the original plan message to show the new final price
    try:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=origin_message_id,
            text=plan_info_template(
                plan,
                final_price=result["final_price"],
                discount_amount=result["discount_amount"],
                discount_code=result["code"],
            ),
            reply_markup=confirm_buy_plan_markup(plan_id, renewal=renewal, uuid=uuid, gift_applied=True),
        )
    except Exception:
        # If edit failed (message too old, etc.) send a new one
        bot.send_message(
            message.chat.id,
            plan_info_template(
                plan,
                final_price=result["final_price"],
                discount_amount=result["discount_amount"],
                discount_code=result["code"],
            ),
            reply_markup=confirm_buy_plan_markup(plan_id, renewal=renewal, uuid=uuid, gift_applied=True),
        )

    bot.send_message(message.chat.id, MESSAGES['GIFT_CODE_APPLIED_SUCCESS'], reply_markup=main_menu_keyboard_markup())

def buy_from_wallet_confirm(message: Message, plan):
    if not plan:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    price_to_pay, _gift = get_effective_plan_price(message.chat.id, plan, kind="buy")

    wallet = USERS_DB.find_wallet(telegram_id=message.chat.id)
    if not wallet:
        # Wallet not created
        bot.send_message(message.chat.id, MESSAGES['LACK_OF_WALLET_BALANCE'],
                         reply_markup=wallet_info_markup())
    if wallet:
        wallet = wallet[0]
        if price_to_pay > wallet['balance']:
            # Save pending invoice so after wallet top-up the user can continue without re-selecting the plan.
            if _gift:
                USERS_DB.set_pending_invoice(
                    telegram_id=message.chat.id,
                    kind="buy",
                    plan_id=int(plan['id']),
                    code_id=_gift.get('code_id'),
                    discount_code=_gift.get('code'),
                    discount_amount=int(_gift.get('discount_amount') or 0),
                    final_price=int(_gift.get('final_price')) if _gift.get('final_price') is not None else None,
                )
            else:
                USERS_DB.set_pending_invoice(telegram_id=message.chat.id, kind="buy", plan_id=int(plan['id']))
            bot.send_message(message.chat.id, MESSAGES['LACK_OF_WALLET_BALANCE'],
                             reply_markup=wallet_info_specific_markup(price_to_pay - wallet['balance']))
            return
        else:
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_NAME'], reply_markup=cancel_markup())
            bot.register_next_step_handler(message, next_step_send_name_for_buy_from_wallet, plan)


def renewal_from_wallet_confirm(message: Message):
    # Robust renewal context: in auto-invoice messages (after wallet top-up approval),
    # in-memory renew_subscription_dict may be empty. Fall back to pending invoice stored in DB.
    uuid, plan_id = get_renewal_context(message.chat.id)
    if not uuid or not plan_id:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
        return

    wallet = USERS_DB.find_wallet(telegram_id=message.chat.id)
    if not wallet:
        status = USERS_DB.add_wallet(telegram_id=message.chat.id)
        if not status:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return

    wallet = wallet[0]
    plan_info = USERS_DB.find_plan(id=plan_id)
    if not plan_info:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    plan_info = plan_info[0]
    price_to_pay, _gift = get_effective_plan_price(message.chat.id, plan_info, kind="renewal", uuid=uuid)
    if price_to_pay > wallet['balance']:
        # Save pending invoice so after wallet top-up the user can continue the renewal without repeating steps.
        if _gift:
            USERS_DB.set_pending_invoice(
                telegram_id=message.chat.id,
                kind="renewal",
                plan_id=int(plan_id),
                uuid=str(uuid),
                code_id=_gift.get('code_id'),
                discount_code=_gift.get('code'),
                discount_amount=int(_gift.get('discount_amount') or 0),
                final_price=int(_gift.get('final_price')) if _gift.get('final_price') is not None else None,
            )
        else:
            USERS_DB.set_pending_invoice(telegram_id=message.chat.id, kind="renewal", plan_id=int(plan_id), uuid=str(uuid))
        bot.send_message(message.chat.id, MESSAGES['LACK_OF_WALLET_BALANCE'],
                         reply_markup=wallet_info_specific_markup(price_to_pay - wallet['balance']))
        del renew_subscription_dict[message.chat.id]
        return

    server_id = plan_info['server_id']
    server = USERS_DB.find_server(id=server_id)
    if not server:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    server = server[0]
    URL = server['url'] + API_PATH
    user = api.find(URL, uuid=uuid)
    if not user:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    user_info = utils.users_to_dict([user])
    if not user_info:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    user_info_process = utils.dict_process(URL, user_info)
    user_info = user_info[0]

    if not user_info_process:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    user_info_process = user_info_process[0]
    new_balance = int(wallet['balance']) - int(price_to_pay)
    edit_wallet = USERS_DB.edit_wallet(message.chat.id, balance=new_balance)
    if not edit_wallet:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    last_reset_time = datetime.datetime.now().strftime("%Y-%m-%d")    
    sub = utils.find_order_subscription_by_uuid(uuid) 
    if not sub:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return   
    settings = utils.all_configs_settings()

    # ---------------- Add-on plans support ----------------
    # A plan can be defined as:
    # - Monthly: size_gb>0 and days>0
    # - Volume-only add-on: days==0 and size_gb>0 (should NOT change subscription time)
    # - Time-only add-on: size_gb==0 and days>0 (should NOT change subscription volume)
    plan_size = _as_int(plan_info.get('size_gb'), 0)
    plan_days = _as_int(plan_info.get('days'), 0)
    is_volume_addon = plan_size > 0 and plan_days == 0
    is_time_addon = plan_size == 0 and plan_days > 0

    # For add-on plans we always apply changes on the existing subscription without resetting usage/time.
    if is_volume_addon or is_time_addon:
        new_usage_limit = _as_int(user_info.get('usage_limit_GB'), 0)
        new_package_days = _as_int(user_info.get('package_days'), 0)
        if is_volume_addon:
            new_usage_limit += plan_size
        if is_time_addon:
            new_package_days += plan_days

        # Only update the fields that must change (prevents unintended resets).
        edit_status = api.update(
            URL,
            uuid=uuid,
            usage_limit_GB=new_usage_limit,
            package_days=new_package_days,
            comment=f"HidyBot:{sub['id']}"
        )

    else:
        # Default renewal mode
        if settings['renewal_method'] == 1:
            if user_info_process['remaining_day'] <= 0 or user_info_process['usage']['remaining_usage_GB'] <= 0:
                new_usage_limit = plan_info['size_gb']
                new_package_days = plan_info['days']
                current_usage_GB = 0
                edit_status = api.update(
                    URL,
                    uuid=uuid,
                    usage_limit_GB=new_usage_limit,
                    package_days=new_package_days,
                    start_date=last_reset_time,
                    current_usage_GB=current_usage_GB,
                    comment=f"HidyBot:{sub['id']}"
                )
            else:
                new_usage_limit = user_info['usage_limit_GB'] + plan_info['size_gb']
                new_package_days = plan_info['days'] + (user_info['package_days'] - user_info_process['remaining_day'])
                edit_status = api.update(
                    URL,
                    uuid=uuid,
                    usage_limit_GB=new_usage_limit,
                    package_days=new_package_days,
                    last_reset_time=last_reset_time,
                    comment=f"HidyBot:{sub['id']}"
                )

        # Advance renewal mode
        elif settings['renewal_method'] == 2:
            new_usage_limit = plan_info['size_gb']
            new_package_days = plan_info['days']
            current_usage_GB = 0
            edit_status = api.update(
                URL,
                uuid=uuid,
                usage_limit_GB=new_usage_limit,
                start_date=last_reset_time,
                package_days=new_package_days,
                current_usage_GB=current_usage_GB,
                comment=f"HidyBot:{sub['id']}"
            )

        # Fair renewal mode
        elif settings['renewal_method'] == 3:
            if user_info_process['remaining_day'] <= 0 or user_info_process['usage']['remaining_usage_GB'] <= 0:
                new_usage_limit = plan_info['size_gb']
                new_package_days = plan_info['days']
                current_usage_GB = 0
                edit_status = api.update(
                    URL,
                    uuid=uuid,
                    usage_limit_GB=new_usage_limit,
                    package_days=new_package_days,
                    start_date=last_reset_time,
                    current_usage_GB=current_usage_GB,
                    comment=f"HidyBot:{sub['id']}"
                )
            else:
                new_usage_limit = user_info['usage_limit_GB'] + plan_info['size_gb']
                new_package_days = plan_info['days'] + user_info['package_days']
                edit_status = api.update(
                    URL,
                    uuid=uuid,
                    usage_limit_GB=new_usage_limit,
                    package_days=new_package_days,
                    last_reset_time=last_reset_time,
                    comment=f"HidyBot:{sub['id']}"
                )

            

    # Common result check (both add-ons and normal renewal)
    if not edit_status:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    # Add New Order
    order_id = random.randint(1000000, 9999999)
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    discount_code = _gift.get('code') if _gift else None
    discount_amount = _gift.get('discount_amount') if _gift else 0
    final_price = _gift.get('final_price') if _gift else None
    status = USERS_DB.add_order(order_id, message.chat.id, user_info_process['name'], plan_id, created_at,
                               discount_code=discount_code, discount_amount=discount_amount, final_price=final_price)
    if not status:
        bot.send_message(message.chat.id,
                         f"{MESSAGES['UNKNOWN_ERROR']}\n{MESSAGES['ORDER_ID']} {order_id}",
                         reply_markup=main_menu_keyboard_markup())
        return

    # Redeem gift code after the order is created (so it is linked to a real order)
    if _gift and _gift.get('code_id'):
        USERS_DB.redeem_discount_code(_gift['code_id'], message.chat.id, order_id)
        gift_code_session.pop(message.chat.id, None)
    USERS_DB.clear_pending_invoice(message.chat.id)

    # Referral commission for purchases by invited users
    try:
        process_referral_commission(message.chat.id, order_id, int(price_to_pay))
    except Exception:
        pass
    # edit_status = ADMIN_DB.edit_user(uuid=uuid, usage_limit_GB=new_usage_limit, package_days=new_package_days)
    # edit_status = api.update(URL, uuid=uuid, usage_limit_GB=new_usage_limit, package_days=new_package_days)
    # if not edit_status:
    #     bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
    #                      reply_markup=main_menu_keyboard_markup())
    #     return

    bot.send_message(message.chat.id, MESSAGES['SUCCESSFUL_RENEWAL'], reply_markup=main_menu_keyboard_markup())
    update_info_subscription(message, uuid)
    BASE_URL = urlparse(server['url']).scheme + "://" + urlparse(server['url']).netloc
    link = f"{BASE_URL}/{urlparse(server['url']).path.split('/')[1]}/{uuid}/"
    user_name = f"<a href='{link}'> {user_info_process['name']} </a>"
    bot_users = USERS_DB.find_user(telegram_id=message.chat.id)
    if bot_users:
        bot_user = bot_users[0]

    # Notify admins if this renewal payment used a gift/discount code
    gift_info = ""
    if _gift and _gift.get('code'):
        try:
            gift_info = (
                f"\n\n🎁 پرداخت با کد هدیه: <code>{_gift.get('code')}</code>"
                f"\n➖ تخفیف: {rial_to_toman(int(_gift.get('discount_amount') or 0))} {MESSAGES['TOMAN']}"
                f"\n💰 مبلغ پرداختی: {rial_to_toman(int(price_to_pay))} {MESSAGES['TOMAN']}"
            )
        except Exception:
            # Keep admin notification robust; never fail the flow due to formatting
            gift_info = f"\n\n🎁 پرداخت با کد هدیه: <code>{_gift.get('code')}</code>"
    for ADMIN in ADMINS_ID:
        admin_bot.send_message(ADMIN,
                               f"""{MESSAGES['ADMIN_NOTIFY_NEW_RENEWAL']} {user_name} {MESSAGES['ADMIN_NOTIFY_NEW_RENEWAL_2']}
{MESSAGES['SERVER']}<a href='{server['url']}/admin'> {server['title']} </a>
{MESSAGES['INFO_ID']} <code>{sub['id']}</code>{gift_info}""", reply_markup=notify_to_admin_markup(bot_user))


# Next Step Buy Plan - Send Screenshot

def next_step_send_screenshot(message, charge_wallet):
    if is_it_cancel(message):
        return
    if not charge_wallet:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    if message.content_type != 'photo':
        bot.send_message(message.chat.id, MESSAGES['ERROR_TYPE_SEND_SCREENSHOT'], reply_markup=cancel_markup())
        bot.register_next_step_handler(message, next_step_send_screenshot, charge_wallet)
        return

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_name = f"{message.chat.id}-{charge_wallet['id']}.jpg"
    path_recp = os.path.join(os.getcwd(), 'UserBot', 'Receiptions', file_name)
    if not os.path.exists(os.path.join(os.getcwd(), 'UserBot', 'Receiptions')):
        os.makedirs(os.path.join(os.getcwd(), 'UserBot', 'Receiptions'))
    with open(path_recp, 'wb') as new_file:
        new_file.write(downloaded_file)

    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payment_method = "Card"

    status = USERS_DB.add_payment(charge_wallet['id'], message.chat.id,
                                  charge_wallet['amount'], payment_method, file_name, created_at)
    if status:
        payment = USERS_DB.find_payment(id=charge_wallet['id'])
        if not payment:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return
        payment = payment[0]
        user_data = USERS_DB.find_user(telegram_id=message.chat.id)
        if not user_data:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return
        user_data = user_data[0]
        for ADMIN in ADMINS_ID:
            admin_bot.send_photo(ADMIN, open(path_recp, 'rb'),
                                 caption=payment_received_template(payment,user_data),
                                 reply_markup=confirm_payment_by_admin(charge_wallet['id']))
        bot.send_message(message.chat.id, MESSAGES['WAIT_FOR_ADMIN_CONFIRMATION'],
                         reply_markup=main_menu_keyboard_markup())
    else:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        
# Next Step Payment - Send Answer
def next_step_answer_to_admin(message, admin_id):
    if is_it_cancel(message):
        return
    bot_users = USERS_DB.find_user(telegram_id=message.chat.id)
    if bot_users:
        bot_user = bot_users[0]
    admin_bot.send_message(int(admin_id), f"{MESSAGES['NEW_TICKET_RECEIVED']}\n{MESSAGES['TICKET_TEXT']} {message.text}",
                           reply_markup=answer_to_user_markup(bot_user,message.chat.id))
    bot.send_message(message.chat.id, MESSAGES['SEND_TICKET_TO_ADMIN_RESPONSE'],
                         reply_markup=main_menu_keyboard_markup())

# Next Step Payment - Send Ticket To Admin
def next_step_send_ticket_to_admin(message):
    if is_it_cancel(message):
        return
    bot_users = USERS_DB.find_user(telegram_id=message.chat.id)
    if bot_users:
        bot_user = bot_users[0]
    for ADMIN in ADMINS_ID:
        admin_bot.send_message(ADMIN, f"{MESSAGES['NEW_TICKET_RECEIVED']}\n{MESSAGES['TICKET_TEXT']} {message.text}",
                               reply_markup=answer_to_user_markup(bot_user,message.chat.id))
        bot.send_message(message.chat.id, MESSAGES['SEND_TICKET_TO_ADMIN_RESPONSE'],
                            reply_markup=main_menu_keyboard_markup())

# ----------------------------------- Buy From Wallet Area -----------------------------------
# Next Step Buy From Wallet - Send Name
def next_step_send_name_for_buy_from_wallet(message: Message, plan):
    if is_it_cancel(message):
        return

    if not plan:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    name = message.text
    while is_it_command(message):
        message = bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_NAME'])
        bot.register_next_step_handler(message, next_step_send_name_for_buy_from_wallet, plan)
        return
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    paid_amount, gift = get_effective_plan_price(message.chat.id, plan)
    discount_code = gift.get('code') if gift else None
    discount_amount = gift.get('discount_amount') if gift else 0
    final_price = gift.get('final_price') if gift else None

    order_id = random.randint(1000000, 9999999)
    server_id = plan['server_id']
    server = USERS_DB.find_server(id=server_id)
    if not server:
        bot.send_message(message.chat.id, f"{MESSAGES['UNKNOWN_ERROR']}:Server Not Found",
                         reply_markup=main_menu_keyboard_markup())
        return
    server = server[0]
    URL = server['url'] + API_PATH

    # value = ADMIN_DB.add_default_user(name, plan['days'], plan['size_gb'],)
    sub_id = random.randint(1000000, 9999999)
    value = api.insert(URL, name=name, usage_limit_GB=plan['size_gb'], package_days=plan['days'],comment=f"HidyBot:{sub_id}")
    if not value:
        bot.send_message(message.chat.id,
                         f"{MESSAGES['UNKNOWN_ERROR']}:Create User Error\n{MESSAGES['ORDER_ID']} {order_id}",
                         reply_markup=main_menu_keyboard_markup())
        return
    add_sub_status = USERS_DB.add_order_subscription(sub_id, order_id, value, server_id)
    if not add_sub_status:
        bot.send_message(message.chat.id,
                         f"{MESSAGES['UNKNOWN_ERROR']}:Add Subscription Error\n{MESSAGES['ORDER_ID']} {order_id}",
                         reply_markup=main_menu_keyboard_markup())
        return
    status = USERS_DB.add_order(order_id, message.chat.id, name, plan['id'], created_at,
                              discount_code=discount_code, discount_amount=discount_amount, final_price=final_price)
    if not status:
        bot.send_message(message.chat.id,
                         f"{MESSAGES['UNKNOWN_ERROR']}:Add Order Error\n{MESSAGES['ORDER_ID']} {order_id}",
                         reply_markup=main_menu_keyboard_markup())
        return
    # Redeem gift code after order is created (so it is linked to a real order)
    if gift and gift.get('code_id'):
        USERS_DB.redeem_discount_code(gift['code_id'], message.chat.id, order_id)
        gift_code_session.pop(message.chat.id, None)
    USERS_DB.clear_pending_invoice(message.chat.id)
    wallet = USERS_DB.find_wallet(telegram_id=message.chat.id)
    if wallet:
        wallet = wallet[0]
        wallet_balance = int(wallet['balance']) - int(paid_amount)
        user_info = USERS_DB.edit_wallet(message.chat.id, balance=wallet_balance)
        if not user_info:
            bot.send_message(message.chat.id,
                             f"{MESSAGES['UNKNOWN_ERROR']}:Edit Wallet Balance Error\n{MESSAGES['ORDER_ID']} {order_id}",
                             reply_markup=main_menu_keyboard_markup())
            return

    # Referral commission for purchases by invited users
    try:
        process_referral_commission(message.chat.id, order_id, int(paid_amount))
    except Exception:
        pass
    bot.send_message(message.chat.id,
                     f"{MESSAGES['PAYMENT_CONFIRMED']}\n{MESSAGES['ORDER_ID']} {order_id}",
                     reply_markup=main_menu_keyboard_markup())
    
    user_info = api.find(URL, value)
    user_info = utils.users_to_dict([user_info])
    user_info = utils.dict_process(URL, user_info)
    user_info = user_info[0]
    api_user_data = user_info_template(sub_id, server, user_info, MESSAGES['INFO_USER'])
    bot.send_message(message.chat.id, api_user_data,
                                 reply_markup=user_info_markup(user_info['uuid']))
    
    BASE_URL = urlparse(server['url']).scheme + "://" + urlparse(server['url']).netloc
    link = f"{BASE_URL}/{urlparse(server['url']).path.split('/')[1]}/{value}/"
    user_name = f"<a href='{link}'> {name} </a>"
    bot_users = USERS_DB.find_user(telegram_id=message.chat.id)
    if bot_users:
        bot_user = bot_users[0]

    # Notify admins if this purchase payment used a gift/discount code
    gift_info = ""
    if gift and gift.get('code'):
        try:
            gift_info = (
                f"\n\n🎁 پرداخت با کد هدیه: <code>{gift.get('code')}</code>"
                f"\n➖ تخفیف: {rial_to_toman(int(gift.get('discount_amount') or 0))} {MESSAGES['TOMAN']}"
                f"\n💰 مبلغ پرداختی: {rial_to_toman(int(paid_amount))} {MESSAGES['TOMAN']}"
            )
        except Exception:
            gift_info = f"\n\n🎁 پرداخت با کد هدیه: <code>{gift.get('code')}</code>"
    for ADMIN in ADMINS_ID:
        admin_bot.send_message(ADMIN,
                               f"""{MESSAGES['ADMIN_NOTIFY_NEW_SUB']} {user_name} {MESSAGES['ADMIN_NOTIFY_CONFIRM']}
{MESSAGES['SERVER']}<a href='{server['url']}/admin'> {server['title']} </a>
{MESSAGES['INFO_ID']} <code>{sub_id}</code>{gift_info}""", reply_markup=notify_to_admin_markup(bot_user))


# ----------------------------------- Get Free Test Area -----------------------------------
# Next Step Get Free Test - Send Name
def next_step_send_name_for_get_free_test(message: Message, server_id):
    if is_it_cancel(message):
        return
    name = message.text
    while is_it_command(message):
        message = bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_NAME'])
        bot.register_next_step_handler(message, next_step_send_name_for_get_free_test)
        return

    settings = utils.all_configs_settings()
    test_user_comment = "HidyBot:FreeTest"
    server = USERS_DB.find_server(id=server_id)
    if not server:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    server = server[0]
    URL = server['url'] + API_PATH
    # uuid = ADMIN_DB.add_default_user(name, test_user_days, test_user_size_gb, int(PANEL_ADMIN_ID), test_user_comment)
    uuid = api.insert(URL, name=name, usage_limit_GB=settings['test_sub_size_gb'], package_days=settings['test_sub_days'],
                      comment=test_user_comment)
    if not uuid:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    non_order_id = random.randint(10000000, 99999999)
    non_order_status = USERS_DB.add_non_order_subscription(non_order_id, message.chat.id, uuid, server_id)
    if not non_order_status:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    edit_user_status = USERS_DB.edit_user(message.chat.id, test_subscription=True)
    if not edit_user_status:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    bot.send_message(message.chat.id, MESSAGES['GET_FREE_CONFIRMED'],
                     reply_markup=main_menu_keyboard_markup())
    user_info = api.find(URL, uuid)
    user_info = utils.users_to_dict([user_info])
    user_info = utils.dict_process(URL, user_info)
    user_info = user_info[0]
    api_user_data = user_info_template(non_order_id, server, user_info, MESSAGES['INFO_USER'])
    bot.send_message(message.chat.id, api_user_data,
                                 reply_markup=user_info_markup(user_info['uuid']))
    BASE_URL = urlparse(server['url']).scheme + "://" + urlparse(server['url']).netloc
    link = f"{BASE_URL}/{urlparse(server['url']).path.split('/')[1]}/{uuid}/"
    user_name = f"<a href='{link}'> {name} </a>"
    bot_users = USERS_DB.find_user(telegram_id=message.chat.id)
    if bot_users:
        bot_user = bot_users[0]
    for ADMIN in ADMINS_ID:
        admin_bot.send_message(ADMIN,
                               f"""{MESSAGES['ADMIN_NOTIFY_NEW_FREE_TEST']} {user_name} {MESSAGES['ADMIN_NOTIFY_CONFIRM']}
{MESSAGES['SERVER']}<a href='{server['url']}/admin'> {server['title']} </a>
{MESSAGES['INFO_ID']} <code>{non_order_id}</code>""", reply_markup=notify_to_admin_markup(bot_user))


# ----------------------------------- To QR Area -----------------------------------
# Next Step QR - QR Code
def next_step_to_qr(message: Message):
    if is_it_cancel(message):
        return
    if not message.text:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    is_it_valid = utils.is_it_config_or_sub(message.text)
    if is_it_valid:
        qr_code = utils.txt_to_qr(message.text)
        if qr_code:
            bot.send_photo(message.chat.id, qr_code, reply_markup=main_menu_keyboard_markup())
    else:
        bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_TO_QR_ERROR'],
                         reply_markup=main_menu_keyboard_markup())


# ----------------------------------- Link Subscription Area -----------------------------------
# Next Step Link Subscription to bot
def next_step_link_subscription(message: Message):
    if not message.text:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    if is_it_cancel(message):
        return
    uuid = utils.is_it_config_or_sub(message.text)
    if uuid:
        # check is it already subscribed
        is_it_subscribed = utils.is_it_subscription_by_uuid_and_telegram_id(uuid, message.chat.id)
        if is_it_subscribed:
            bot.send_message(message.chat.id, MESSAGES['ALREADY_SUBSCRIBED'],
                             reply_markup=main_menu_keyboard_markup())
            return
        non_sub_id = random.randint(10000000, 99999999)
        servers = USERS_DB.select_servers()
        if not servers:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        for server in servers:
            users_list = api.find(server['url'] + API_PATH, uuid)
            if users_list:
                server_id = server['id']
                break
        status = USERS_DB.add_non_order_subscription(non_sub_id, message.chat.id, uuid, server_id)
        if status:
            bot.send_message(message.chat.id, MESSAGES['SUBSCRIPTION_CONFIRMED'],
                             reply_markup=main_menu_keyboard_markup())
        else:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
    else:
        bot.send_message(message.chat.id, MESSAGES['SUBSCRIPTION_INFO_NOT_FOUND'],
                         reply_markup=main_menu_keyboard_markup())


# ----------------------------------- wallet balance Area -----------------------------------
# Next Step increase wallet balance - Send amount
def next_step_increase_wallet_balance(message):
    if is_it_cancel(message):
        return
    if not is_it_digit(message, markup=cancel_markup()):
        bot.register_next_step_handler(message, next_step_increase_wallet_balance)
        return
    minimum_deposit_amount = utils.all_configs_settings()
    minimum_deposit_amount = minimum_deposit_amount['min_deposit_amount']
    amount = utils.toman_to_rial(message.text)
    if amount < minimum_deposit_amount:
        bot.send_message(message.chat.id,
                         f"{MESSAGES['INCREASE_WALLET_BALANCE_AMOUNT']}\n{MESSAGES['MINIMUM_DEPOSIT_AMOUNT']}: "
                         f"{rial_to_toman(minimum_deposit_amount)} {MESSAGES['TOMAN']}", reply_markup=cancel_markup())
        bot.register_next_step_handler(message, next_step_increase_wallet_balance)
        return
    settings = utils.all_configs_settings()
    if not settings:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return

    charge_wallet['amount'] = str(amount)
    if settings['three_random_num_price'] == 1:
        charge_wallet['amount'] = utils.replace_last_three_with_random(str(amount))

    charge_wallet['id'] = random.randint(1000000, 9999999)
    # Send 0 to identify wallet balance charge
    bot.send_message(message.chat.id,
                     owner_info_template(settings['card_number'], settings['card_holder'], charge_wallet['amount']),
                     reply_markup=send_screenshot_markup(plan_id=charge_wallet['id']))

def increase_wallet_balance_specific(message,amount):
    settings = utils.all_configs_settings()
    user = USERS_DB.find_user(telegram_id=message.chat.id)
    if user:
        wallet_status = USERS_DB.find_wallet(telegram_id=message.chat.id)
        if not wallet_status:
            status = USERS_DB.add_wallet(telegram_id=message.chat.id)
            if not status:
                bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'])
                return
    charge_wallet['amount'] = str(amount)
    if settings['three_random_num_price'] == 1:
        charge_wallet['amount'] = utils.replace_last_three_with_random(str(amount))

    charge_wallet['id'] = random.randint(1000000, 9999999)

    # Send 0 to identify wallet balance charge
    bot.send_message(message.chat.id,
                     owner_info_template(settings['card_number'], settings['card_holder'], charge_wallet['amount']),
                     reply_markup=send_screenshot_markup(plan_id=charge_wallet['id']))
    


def update_info_subscription(message: Message, uuid,markup=None):
    value = uuid
    sub = utils.find_order_subscription_by_uuid(value)
    if not sub:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    if not markup:
        if sub.get('telegram_id', None):
            # Non-Order Subscription markup
            mrkup = user_info_non_sub_markup(sub['uuid'])
        else:
            # Ordered Subscription markup
            mrkup = user_info_markup(sub['uuid'])
    else:
        mrkup = markup
    server_id = sub['server_id']
    server = USERS_DB.find_server(id=server_id)
    if not server:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    server = server[0]
    URL = server['url'] + API_PATH
    user = api.find(URL, uuid=sub['uuid'])
    if not user:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                         reply_markup=main_menu_keyboard_markup())
        return
    user = utils.dict_process(URL, utils.users_to_dict([user]))[0]
    try:
        bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id,
                              text=user_info_template(sub['id'], server, user, MESSAGES['INFO_USER']),
                              reply_markup=mrkup)
    except:
        pass


# *********************************** Callback Query Area ***********************************
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call: CallbackQuery):
    bot.answer_callback_query(call.id, MESSAGES['WAIT'])
    bot.clear_step_handler(call.message)
    if is_user_banned(call.message.chat.id):
        return
    # Split Callback Data to Key(Command) and Value
    # NOTE: use maxsplit=1 so values like "a:b:c" won't break parsing.
    data = call.data.split(':', 1)
    if len(data) < 2:
        # Invalid callback payload; ignore gracefully
        return
    key = data[0]
    value = data[1]

    global selected_server_id
    # ----------------------------------- Link Subscription Area -----------------------------------
    # Confirm Link Subscription
    if key == 'force_join_status':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        join_status = is_user_in_channel(call.message.chat.id)

        if not join_status:
            return
        else:
            # If the user came via a referral link and signup bonus was deferred due to force-join,
            # pay it now.
            try:
                enabled, ref_bonus_rial, new_bonus_rial, _p, _d, _s = _get_referral_settings()
                if enabled and USERS_DB.is_referral_bonus_pending(call.message.chat.id) and not USERS_DB.is_referral_bonus_paid(call.message.chat.id):
                    referrer_id = USERS_DB.get_referrer_of_user(call.message.chat.id)
                    if referrer_id:
                        process_referral_signup_bonus(call.message.chat.id, referrer_id, ref_bonus_rial, new_bonus_rial)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, MESSAGES['JOIN_CHANNEL_SUCCESSFUL'])

    # -------------------- Referral callbacks --------------------
    elif key == 'referral_dashboard':
        # Show dashboard again inside inline navigation
        try:
            msg = call.message
            fake = Message.de_json(msg.json) if hasattr(msg, 'json') else None
        except Exception:
            fake = None
        try:
            # Reuse the same handler logic (without duplicating join checks)
            enabled, ref_bonus_rial, _new_bonus_rial, percent, dashboard_tpl, _share_text = _get_referral_settings()
            if not enabled:
                bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                      text="این بخش در حال حاضر غیرفعال است.")
                return
            USERS_DB.ensure_user_referral_code(call.message.chat.id)
            ref_count = USERS_DB.count_referred_users(call.message.chat.id)
            buy_count, buy_total = USERS_DB.referral_purchase_stats(call.message.chat.id)
            signup_total, commission_total = USERS_DB.referral_earnings_totals(call.message.chat.id)

            def _toman_str(rial_val: int) -> str:
                try:
                    return utils.rial_to_toman(int(rial_val))
                except Exception:
                    return str(int(rial_val) // 10)

            text = dashboard_tpl or ""
            try:
                text = text.format(
                    signup_bonus_toman=_toman_str(ref_bonus_rial),
                    commission_percent=int(percent),
                    ref_count=int(ref_count),
                    buy_count=int(buy_count),
                    buy_total_toman=_toman_str(buy_total),
                    signup_total_toman=_toman_str(signup_total),
                    commission_total_toman=_toman_str(commission_total),
                )
            except Exception:
                pass
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=text, reply_markup=referral_dashboard_markup())
        except Exception:
            pass

    elif key == 'referral_back':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['WELCOME'], reply_markup=main_menu_keyboard_markup())

    elif key == 'referral_share':
        enabled, _ref_bonus_rial, _new_bonus_rial, _percent, _dash, share_text = _get_referral_settings()
        if not enabled:
            return
        code = USERS_DB.ensure_user_referral_code(call.message.chat.id) or str(call.message.chat.id)
        try:
            me = bot.get_me()
            bot_username = me.username
        except Exception:
            bot_username = None
        link = f"https://t.me/{bot_username}?start={code}" if bot_username else f"/start {code}"

        # Share text is configurable by admin. We always:
        # - prevent leaking placeholders like "{link}" / "{ref_link}"
        # - keep the inline button
        # - ALSO show the raw invite link at the end of the message (with one blank line before it)
        base = (share_text or "").strip()
        # Remove any known placeholders (some older templates used {ref_link}).
        for ph in ("{link}", "{ref_link}"):
            if ph in base:
                base = base.replace(ph, "")

        # Normalize whitespace after placeholder removal
        text = "\n".join([ln.rstrip() for ln in base.splitlines()]).strip()
        final_text = (text + "\n\n" + link) if text else link

        share_kb = InlineKeyboardMarkup(row_width=1)
        share_kb.add(InlineKeyboardButton("💳خرید اشتراک/🎁 تست رایگان", url=link))

        bot.send_message(
            call.message.chat.id,
            final_text,
            reply_markup=share_kb,
            disable_web_page_preview=True,
        )

    elif key == 'referral_income_history':
        enabled, _ref_bonus_rial, _new_bonus_rial, _percent, _dash, _share = _get_referral_settings()
        if not enabled:
            return
        rows = USERS_DB.get_referral_earnings(call.message.chat.id, limit=10)
        if not rows:
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text="🧾 تاریخچه درآمد\n\nهیچ تراکنشی ثبت نشده است.",
                                  reply_markup=referral_history_markup())
            return

        def _toman_str(rial_val: int) -> str:
            try:
                return utils.rial_to_toman(int(rial_val))
            except Exception:
                return str(int(rial_val) // 10)

        def _display_user(telegram_id: int) -> str:
            """Return a clean display name for a telegram user id.

            Preference order:
            1) cached user row in DB (username/full_name)
            2) Telegram get_chat (username/first+last)
            3) fallback to numeric id
            """
            try:
                u = USERS_DB.find_user(telegram_id=int(telegram_id))
                if u and isinstance(u, list) and len(u) > 0:
                    row = u[0]
                    username = (row.get('username') or '').strip()
                    full_name = (row.get('full_name') or '').strip()
                    if username:
                        return f"@{username.lstrip('@')}" if not username.startswith('@') else username
                    if full_name:
                        return full_name
            except Exception:
                pass

            try:
                ch = bot.get_chat(int(telegram_id))
                # Prefer @username if exists
                if getattr(ch, 'username', None):
                    return f"@{ch.username}"
                fn = (getattr(ch, 'first_name', '') or '').strip()
                ln = (getattr(ch, 'last_name', '') or '').strip()
                nm = (fn + (" " + ln if ln else "")).strip()
                if nm:
                    return nm
            except Exception:
                pass

            return str(telegram_id)

        # Visual separators to improve readability in Telegram message bubbles
        sep = "━━━━━━━━━━━━━━"
        lines = ["🧾 تاریخچه درآمد (۱۰ تراکنش آخر)"]

        for idx, r in enumerate(rows, start=1):
            typ = r.get('type')
            amount = _toman_str(int(r.get('amount') or 0))
            dt = str(r.get('created_at') or '')
            other = r.get('related_telegram_id')
            order_id = r.get('order_id')

            if typ == 'signup_bonus':
                title = "🎁 هدیه عضویت"
            elif typ == 'commission':
                title = "💸 پورسانت خرید"
            else:
                title = str(typ)

            lines.append(sep)
            lines.append(f"{idx}) {title}")
            lines.append(f"💰 مبلغ: {amount} تومان")

            if other:
                try:
                    lines.append(f"👤 کاربر: {_display_user(int(other))}")
                except Exception:
                    lines.append(f"👤 کاربر: {other}")

            if order_id:
                lines.append(f"🧾 شماره سفارش: {order_id}")

            lines.append(f"🕒 {dt}")
            lines.append("")

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="\n".join(lines),
            reply_markup=referral_history_markup(),
        )
            
    elif key == 'confirm_subscription':
        edit_status = USERS_DB.add_non_order_subscription(call.message.chat.id, value, )
        if edit_status:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, MESSAGES['SUBSCRIPTION_CONFIRMED'],
                             reply_markup=main_menu_keyboard_markup())
        else:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
    # Reject Link Subscription
    elif key == 'cancel_subscription':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['CANCEL_SUBSCRIPTION'],
                         reply_markup=main_menu_keyboard_markup())

    # ----------------------------------- Buy Plan Area -----------------------------------
    elif key == 'server_selected':
        if value == 'False':
            bot.send_message(call.message.chat.id, MESSAGES['SERVER_IS_FULL'], reply_markup=main_menu_keyboard_markup())
            return
        selected_server_id = int(value)
        plans = USERS_DB.find_plan(server_id=int(value))
        if not plans:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        settings = utils.all_configs_settings()

        # In "buy" flow we always show only full (monthly) plans in the list.
        # Optional: show an extra entry for add-on purchase (volume/time) depending on settings.
        # Some DBs store numeric columns as strings like "30.0". Be forgiving.
        monthly_plans = []
        try:
            monthly_plans = [
                p for p in plans
                if _as_int(p.get('size_gb'), 0) > 0 and _as_int(p.get('days'), 0) > 0
            ]
        except Exception:
            monthly_plans = plans

        # In Buy section, only show the add-on entry if:
        # 1) add-ons are enabled AND allowed to appear in buy section
        # 2) user already has (at least one) subscription on this server
        # This prevents confusing UX where add-ons appear "buyable" without a base subscription.
        try:
            has_sub = bool(USERS_DB.find_user_subscription_uuids_by_server(call.message.chat.id, selected_server_id))
        except Exception:
            has_sub = False
        show_addons_entry = bool(settings.get('addon_plans_enabled')) and bool(settings.get('addon_plans_show_in_buy')) and has_sub

        # Use a wider header (similar to renewal flow) so inline keyboard buttons appear full-width
        from UserBot.templates import plans_list_buy_header_template

        plan_markup = plans_list_buy_markup(monthly_plans, server_id=selected_server_id, show_addons_button=show_addons_entry)
        if not plan_markup:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return
        server_title = None
        try:
            srv = USERS_DB.find_server(id=selected_server_id)
            if srv:
                server_title = srv[0].get('title') or srv[0].get('name')
        except Exception:
            server_title = None

        bot.edit_message_text(
            plans_list_buy_header_template(server_title=server_title),
            call.message.chat.id,
            call.message.message_id,
            reply_markup=plan_markup,
            parse_mode='HTML'
        )

    elif key == 'buy_addon_start':
        # Entry from Buy section to purchase add-on plans.
        server_id = int(value)
        settings = utils.all_configs_settings()
        if not settings.get('addon_plans_enabled') or not settings.get('addon_plans_show_in_buy'):
            bot.send_message(call.message.chat.id, MESSAGES['ADDON_PLANS_DISABLED'], reply_markup=main_menu_keyboard_markup())
            return

        # Find user's subscriptions on this server
        server = USERS_DB.find_server(id=server_id)
        if not server:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        server = server[0]
        URL = server['url'] + API_PATH

        uuids = USERS_DB.find_user_subscription_uuids_by_server(call.message.chat.id, server_id)
        if not uuids:
            bot.send_message(call.message.chat.id, MESSAGES['NO_SUBSCRIPTION_FOR_ADDON'], reply_markup=main_menu_keyboard_markup())
            return

        # Build a compact list of subscriptions (only active ones that exist on panel)
        sub_items = []
        for u in uuids:
            try:
                user = api.find(URL, u)
                if not user:
                    continue
                # Remaining day might not exist in raw. Use package_days and start_date? keep simple.
                # Requirement: in buy-add-on flow, show subscriptions ONLY by name to keep list clean.
                title = user.get('name') or user.get('comment') or u
                sub_items.append({'uuid': u, 'title': title})
            except Exception:
                continue

        if not sub_items:
            bot.send_message(call.message.chat.id, MESSAGES['NO_SUBSCRIPTION_FOR_ADDON'], reply_markup=main_menu_keyboard_markup())
            return

        try:
            bot.edit_message_text(
                MESSAGES['SELECT_SUBSCRIPTION_FOR_ADDON'],
                call.message.chat.id,
                call.message.message_id,
                reply_markup=addon_subscriptions_list_markup(sub_items, server_id=server_id),
                parse_mode='HTML'
            )
        except Exception:
            # Fallback: if edit fails (e.g. message was deleted), send a fresh message.
            bot.send_message(
                call.message.chat.id,
                MESSAGES['SELECT_SUBSCRIPTION_FOR_ADDON'],
                reply_markup=addon_subscriptions_list_markup(sub_items, server_id=server_id),
                parse_mode='HTML'
            )

    elif key == 'buy_addon_select_sub':
        # value format: "server_id|uuid" (to avoid costly server scans)
        try:
            server_id_str, uuid = value.split('|', 1)
            selected_server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        server = USERS_DB.find_server(id=selected_server_id)
        if not server:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        URL = server[0]['url'] + API_PATH

        settings = utils.all_configs_settings()
        if not settings.get('addon_plans_enabled'):
            bot.send_message(call.message.chat.id, MESSAGES['ADDON_PLANS_DISABLED'], reply_markup=main_menu_keyboard_markup())
            return

        plans = USERS_DB.find_plan(server_id=selected_server_id)
        if not plans:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        addon_plans = []
        for p in plans:
            try:
                sz = _as_int(p.get('size_gb'), 0)
                dy = _as_int(p.get('days'), 0)
                if (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                    addon_plans.append(p)
            except Exception:
                continue

        # Use the same UX/pipeline as renewal add-ons to avoid buy-flow edge cases.
        # Store selected uuid into renewal session dict so renewal_plan_selected works.
        renew_subscription_dict[call.message.chat.id] = {
            'uuid': uuid,
        }
        markup = buy_addon_plans_list_markup(addon_plans, server_id=selected_server_id)
        if not markup:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        try:
            bot.edit_message_text(
                MESSAGES['SELECT_ADDON_PLAN'],
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode='HTML'
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                MESSAGES['SELECT_ADDON_PLAN'],
                reply_markup=markup,
                parse_mode='HTML'
            )

    elif key == 'buy_addon_plan_selected':
        # value: "plan_id|uuid"
        try:
            plan_id_str, uuid = value.split('|', 1)
            plan_id = int(plan_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        plan = USERS_DB.find_plan(id=plan_id)
        if not plan:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return
        plan = plan[0]

        # Reuse renewal flow for add-ons (applies on existing subscription)
        renew_subscription_dict[call.message.chat.id] = {
            'uuid': uuid,
            'plan_id': plan_id,
        }

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=plan_info_template(plan),
            reply_markup=confirm_buy_plan_markup(plan_id, renewal=True, uuid=uuid, gift_applied=False),
        )
        
    elif key == 'free_test_server_selected':
        if value == 'False':
            bot.send_message(call.message.chat.id, MESSAGES['SERVER_IS_FULL'], reply_markup=main_menu_keyboard_markup())
            return
        users = USERS_DB.find_user(telegram_id=call.message.chat.id)
        if users:
            user = users[0]
            if user['test_subscription']:
                bot.send_message(call.message.chat.id, MESSAGES['ALREADY_RECEIVED_FREE'],
                                reply_markup=main_menu_keyboard_markup())
                return
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, MESSAGES['REQUEST_SEND_NAME'], reply_markup=cancel_markup())
            bot.register_next_step_handler(call.message, next_step_send_name_for_get_free_test, value)
    # Send Asked Plan Info
    elif key == 'plan_selected':
        plan = USERS_DB.find_plan(id=value)[0]
        if not plan:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return

        # SAFETY GUARD (Buy flow):
        # Do NOT allow users to directly buy add-on plans (volume-only / time-only) as a standalone purchase.
        # Add-ons must be applied on an existing subscription via the dedicated add-on flow.
        try:
            sz = _as_int(plan.get('size_gb'), 0)
            dy = _as_int(plan.get('days'), 0)
            is_addon = (sz > 0 and dy == 0) or (sz == 0 and dy > 0)
        except Exception:
            is_addon = False

        if is_addon:
            settings = utils.all_configs_settings()
            # If add-ons are enabled in buy section, guide user to the correct entry point.
            if settings.get('addon_plans_enabled') and settings.get('addon_plans_show_in_buy'):
                # Reuse the existing guard inside buy_addon_start (it will refuse if user has no subscription).
                try:
                    call.data = f"buy_addon_start:{plan.get('server_id')}"
                    return callback_query(call)
                except Exception:
                    bot.send_message(call.message.chat.id, MESSAGES['NO_SUBSCRIPTION_FOR_ADDON'], reply_markup=main_menu_keyboard_markup())
                    return
            # Otherwise, just block.
            bot.send_message(call.message.chat.id, MESSAGES['NO_SUBSCRIPTION_FOR_ADDON'], reply_markup=main_menu_keyboard_markup())
            return

        sess = gift_code_session.get(call.message.chat.id)
        gift_applied = False
        if sess and sess.get("kind") == "buy" and sess.get("plan_id") == plan.get("id") and sess.get("final_price") is not None:
            gift_applied = True
            text_msg = plan_info_template(
                plan,
                final_price=sess.get("final_price"),
                discount_amount=sess.get("discount_amount", 0),
                discount_code=sess.get("code"),
            )
        else:
            text_msg = plan_info_template(plan)

        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text=text_msg,
                              reply_markup=confirm_buy_plan_markup(plan['id'], gift_applied=gift_applied))


    elif key == 'gift_code':
        # Gift code flow (new purchase)
        plan_id = int(value)
        msg = bot.send_message(call.message.chat.id, MESSAGES['ENTER_GIFT_CODE'], reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, next_step_apply_gift_code, plan_id, call.message.message_id, False, None)

    elif key == 'gift_code_renewal':
        # Gift code flow (renewal)
        plan_id = int(value)
        uuid, _pid = get_renewal_context(call.message.chat.id)
        # If in-memory state is missing (auto-invoice), fall back to pending invoice
        # so gift code payment won't break.
        msg = bot.send_message(call.message.chat.id, MESSAGES['ENTER_GIFT_CODE'], reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, next_step_apply_gift_code, plan_id, call.message.message_id, True, uuid)

    elif key == 'gift_code_clear':
        # Clear gift code for new purchase and refresh invoice
        plan_id = int(value)
        gift_code_session.pop(call.message.chat.id, None)
        # Also clear any persisted pending invoice discount info (in case the bot restarts)
        USERS_DB.set_pending_invoice(telegram_id=call.message.chat.id, kind="buy", plan_id=plan_id)
        plan = USERS_DB.find_plan(id=plan_id)
        if plan:
            plan = plan[0]
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=plan_info_template(plan),
                reply_markup=confirm_buy_plan_markup(plan_id, gift_applied=False),
            )

    elif key == 'gift_code_clear_renewal':
        # Clear gift code for renewal and refresh invoice
        plan_id = int(value)
        gift_code_session.pop(call.message.chat.id, None)
        uuid, _pid = get_renewal_context(call.message.chat.id)
        if uuid:
            USERS_DB.set_pending_invoice(telegram_id=call.message.chat.id, kind="renewal", plan_id=plan_id, uuid=str(uuid))
        plan = USERS_DB.find_plan(id=plan_id)
        if plan:
            plan = plan[0]
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=plan_info_template(plan),
                reply_markup=confirm_buy_plan_markup(plan_id, renewal=True, uuid=uuid, gift_applied=False),
            )

    # Confirm To Buy From Wallet
    elif key == 'confirm_buy_from_wallet':
        # NOTE: This callback is also used in the "auto invoice" message that is sent after
        # wallet top-up approval by admin. In that case, the value is always a string and
        # some global session state (like selected_server_id) might not be set.
        try:
            plan_id = int(str(value).strip())
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        plan_rows = USERS_DB.find_plan(id=plan_id)
        if not plan_rows:
            # If the plan no longer exists or callback got corrupted, clear pending invoice to avoid loops.
            try:
                USERS_DB.clear_pending_invoice(call.message.chat.id)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return
        plan = plan_rows[0]
        # Set selected_server_id so "Back" in invoice works even when invoice was auto-sent.
        try:
            selected_server_id = int(plan.get('server_id') or 0) or selected_server_id
        except Exception:
            pass
        buy_from_wallet_confirm(call.message, plan)
    elif key == 'confirm_renewal_from_wallet':
        # renewal uses renew_subscription_dict; keep robust parsing to avoid silent failures
        try:
            _ = int(str(value).strip())
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        # plan existence check (optional) – renewal flow relies on renew_subscription_dict state
        renewal_from_wallet_confirm(call.message)

    # Ask To Send Screenshot
    elif key == 'send_screenshot':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['REQUEST_SEND_SCREENSHOT'])
        bot.register_next_step_handler(call.message, next_step_send_screenshot, charge_wallet)

    #Answer to Admin After send Screenshot
    elif key == 'answer_to_admin':
        #bot.delete_message(call.message.chat.id,call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['ANSWER_TO_ADMIN'],
                        reply_markup=cancel_markup())
        bot.register_next_step_handler(call.message, next_step_answer_to_admin, value)

    #Send Ticket to Admin 
    elif key == 'send_ticket_to_support':
        bot.delete_message(call.message.chat.id,call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['SEND_TICKET_TO_ADMIN'],
                        reply_markup=cancel_markup())
        bot.register_next_step_handler(call.message, next_step_send_ticket_to_admin)

    # ----------------------------------- User Subscriptions Info Area -----------------------------------
    # Unlink non-order subscription
    elif key == 'unlink_subscription':
        delete_status = USERS_DB.delete_non_order_subscription(uuid=value)
        if delete_status:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, MESSAGES['SUBSCRIPTION_UNLINKED'],
                             reply_markup=main_menu_keyboard_markup())
        else:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())

    elif key == 'update_info_subscription':
        update_info_subscription(call.message, value)

    # ----------------------------------- wallet Area -----------------------------------
    # INCREASE WALLET BALANCE
    elif key == 'increase_wallet_balance':
        bot.send_message(call.message.chat.id, MESSAGES['INCREASE_WALLET_BALANCE_AMOUNT'], reply_markup=cancel_markup())

        bot.register_next_step_handler(call.message, next_step_increase_wallet_balance)
    elif key == 'increase_wallet_balance_specific':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        increase_wallet_balance_specific(call.message,value)
    elif key == 'renewal_subscription':
        settings = utils.all_configs_settings()
        if not settings['renewal_subscription_status']:
            bot.send_message(call.message.chat.id, MESSAGES['RENEWAL_SUBSCRIPTION_CLOSED'],
                             reply_markup=main_menu_keyboard_markup())
            return
        servers = USERS_DB.select_servers()
        server_id = 0
        user= []
        URL = "url"
        if servers:
            for server in servers:
                user = api.find(server['url'] + API_PATH, value)
                if user:
                    selected_server_id = server['id']
                    URL = server['url'] + API_PATH
                    break
        if not user:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return
        user_info = utils.users_to_dict([user])
        if not user_info:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return

        user_info_process = utils.dict_process(URL, user_info)
        if not user_info_process:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return
        user_info_process = user_info_process[0]
        if settings['renewal_method'] == 2:
            if user_info_process['remaining_day'] > settings['advanced_renewal_days'] and user_info_process['usage']['remaining_usage_GB'] > settings['advanced_renewal_usage']:
                bot.send_message(call.message.chat.id, renewal_unvalable_template(settings),
                                 reply_markup=main_menu_keyboard_markup())
                return
        

        # Renewal session state (also used by add-on flow)
        renew_subscription_dict[call.message.chat.id] = {
            'uuid': None,
            'plan_id': None,
            'server_id': int(selected_server_id),
            'last_list': 'base',  # 'base' or 'addon' (for correct back navigation)
        }
        plans = USERS_DB.find_plan(server_id=selected_server_id)
        if not plans:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'],
                             reply_markup=main_menu_keyboard_markup())
            return

        # Split base (monthly) plans and add-ons (volume/time) so renewal list doesn't get cluttered.
        base_plans = []
        addon_plans = []
        for p in plans:
            try:
                sz = int(p.get('size_gb') or 0)
                dy = int(p.get('days') or 0)
                if sz > 0 and dy > 0:
                    base_plans.append(p)
                elif (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                    addon_plans.append(p)
            except Exception:
                continue

        show_addons_button = bool(settings.get('addon_plans_enabled')) and len(addon_plans) > 0
        renew_subscription_dict[call.message.chat.id]['uuid'] = value
        renew_subscription_dict[call.message.chat.id]['last_list'] = 'base'

        # Show renewal base plans immediately. Add-ons are available via a separate button.
        # Using update_info_subscription is more reliable than edit_message_reply_markup and prevents the
        # "plans appear only after back" glitch.
        update_info_subscription(
            call.message,
            value,
            renewal_plans_list_markup(base_plans, uuid=user_info_process['uuid'], server_id=int(selected_server_id), show_addons_button=show_addons_button)
        )

    elif key == 'renewal_addon_start':
        # value format: "server_id|uuid". Shows add-on plans separately in renewal flow.
        try:
            server_id_str, uuid = value.split('|', 1)
            server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        settings = utils.all_configs_settings()
        if not settings.get('addon_plans_enabled'):
            bot.send_message(call.message.chat.id, MESSAGES['ADDON_PLANS_DISABLED'], reply_markup=main_menu_keyboard_markup())
            return

        plans = USERS_DB.find_plan(server_id=server_id)
        if not plans:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        addon_plans = []
        for p in plans:
            sz = _as_int(p.get('size_gb'), 0)
            dy = _as_int(p.get('days'), 0)
            if (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                addon_plans.append(p)

        markup = renewal_addon_plans_list_markup(addon_plans, uuid=uuid, server_id=server_id)
        # Remember that user is currently browsing add-on plans (affects Back from plan info screen)
        try:
            if call.message.chat.id in renew_subscription_dict:
                renew_subscription_dict[call.message.chat.id]['last_list'] = 'addon'
                renew_subscription_dict[call.message.chat.id]['server_id'] = server_id
                renew_subscription_dict[call.message.chat.id]['uuid'] = uuid
        except Exception:
            pass
        if not markup:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        # Mark current list for correct back navigation from plan detail page.
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'addon'

        try:
            bot.edit_message_text(
                MESSAGES['SELECT_ADDON_PLAN'],
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode='HTML'
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                MESSAGES['SELECT_ADDON_PLAN'],
                reply_markup=markup,
                parse_mode='HTML'
            )

    elif key == 'renewal_addon_back':
        # value format: "server_id|uuid". Return to base plans.
        try:
            server_id_str, uuid = value.split('|', 1)
            server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        settings = utils.all_configs_settings()
        plans = USERS_DB.find_plan(server_id=server_id) or []

        base_plans = []
        addon_plans = []
        for p in plans:
            try:
                sz = int(p.get('size_gb') or 0)
                dy = int(p.get('days') or 0)
                if sz > 0 and dy > 0:
                    base_plans.append(p)
                elif (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                    addon_plans.append(p)
            except Exception:
                continue

        show_addons_button = bool(settings.get('addon_plans_enabled')) and len(addon_plans) > 0
        markup = renewal_plans_list_markup(base_plans, uuid=uuid, server_id=server_id, show_addons_button=show_addons_button)
        if not markup:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        # Mark current list for correct back navigation.
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'base'

        # Restore the original renewal screen (user info + base plans list).
        update_info_subscription(call.message, uuid, markup)

    elif key == 'back_to_renewal_base':
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'base'

        # value format: "server_id|uuid". Back from plan info to the base plans list.
        try:
            server_id_str, uuid = value.split('|', 1)
            server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        settings = utils.all_configs_settings()
        plans = USERS_DB.find_plan(server_id=server_id) or []

        base_plans = []
        addon_plans = []
        for p in plans:
            sz = _as_int(p.get('size_gb'), 0)
            dy = _as_int(p.get('days'), 0)
            if sz > 0 and dy > 0:
                base_plans.append(p)
            elif (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                addon_plans.append(p)

        show_addons_button = bool(settings.get('addon_plans_enabled')) and len(addon_plans) > 0
        markup = renewal_plans_list_markup(base_plans, uuid=uuid, server_id=server_id, show_addons_button=show_addons_button)
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'base'
        update_info_subscription(call.message, uuid, markup)

    elif key == 'back_to_renewal_addons':
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'addon'

        # value format: "server_id|uuid". Back from plan info to the add-on plans list.
        try:
            server_id_str, uuid = value.split('|', 1)
            server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return

        plans = USERS_DB.find_plan(server_id=server_id) or []
        addon_plans = []
        for p in plans:
            sz = _as_int(p.get('size_gb'), 0)
            dy = _as_int(p.get('days'), 0)
            if (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                addon_plans.append(p)

        markup = renewal_addon_plans_list_markup(addon_plans, uuid=uuid, server_id=server_id)
        if not markup:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'addon'

        # Ensure the header is consistent and list isn't mixed with base plans.
        try:
            bot.edit_message_text(
                MESSAGES['SELECT_ADDON_PLAN'],
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode='HTML'
            )
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['SELECT_ADDON_PLAN'], reply_markup=markup, parse_mode='HTML')

    elif key == 'renewal_plan_selected':
        plan = USERS_DB.find_plan(id=value)[0]
        if not plan:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'],
                             reply_markup=main_menu_keyboard_markup())
            return
        renew_subscription_dict[call.message.chat.id]['plan_id'] = plan['id']
        uuid = renew_subscription_dict[call.message.chat.id]['uuid']

        sess = gift_code_session.get(call.message.chat.id)
        gift_applied = False
        if sess and sess.get("kind") == "renewal" and sess.get("plan_id") == plan.get("id") and sess.get("uuid") == uuid and sess.get("final_price") is not None:
            gift_applied = True
            text_msg = plan_info_template(
                plan,
                final_price=sess.get("final_price"),
                discount_amount=sess.get("discount_amount", 0),
                discount_code=sess.get("code"),
            )
        else:
            text_msg = plan_info_template(plan)

        # Choose correct "back" target based on where the user came from.
        sess_state = renew_subscription_dict.get(call.message.chat.id, {})
        server_id = sess_state.get('server_id')
        last_list = sess_state.get('last_list', 'base')
        if server_id is None:
            # best effort fallback
            try:
                server_id = int(selected_server_id)
            except Exception:
                server_id = 0
        back_cb = f"back_to_renewal_addons:{server_id}|{uuid}" if last_list == 'addon' else f"back_to_renewal_base:{server_id}|{uuid}"

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text_msg,
            reply_markup=confirm_buy_plan_markup(plan['id'], renewal=True, uuid=uuid, gift_applied=gift_applied, back_callback=back_cb),
        )

    elif key == 'cancel_increase_wallet_balance':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, MESSAGES['CANCEL_INCREASE_WALLET_BALANCE'],
                         reply_markup=main_menu_keyboard_markup())
    # ----------------------------------- User Configs Area -----------------------------------
    # User Configs - Main Menu
    elif key == 'configs_list':
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=sub_url_user_list_markup(value))
    # User Configs - Direct Link
    elif key == 'conf_dir':
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        configs = utils.sub_parse(sub['sub_link'])
        if not configs:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=sub_user_list_markup(value,configs))
        
    # User Configs - Vless Configs Callback
    elif key == "conf_dir_vless":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        configs = utils.sub_parse(sub['sub_link'])
        if not configs:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        if not configs['vless']:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        msgs = configs_template(configs['vless'])
        for message in msgs:
            if message:
                bot.send_message(call.message.chat.id, f"{message}",
                                 reply_markup=main_menu_keyboard_markup())
    # User Configs - VMess Configs Callback
    elif key == "conf_dir_vmess":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        configs = utils.sub_parse(sub['sub_link'])
        if not configs:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        if not configs['vmess']:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        msgs = configs_template(configs['vmess'])
        for message in msgs:
            if message:
                bot.send_message(call.message.chat.id, f"{message}",
                                 reply_markup=main_menu_keyboard_markup())
    # User Configs - Trojan Configs Callback
    elif key == "conf_dir_trojan":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        configs = utils.sub_parse(sub['sub_link'])
        if not configs:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        if not configs['trojan']:
            bot.send_message(call.message.chat.id, MESSAGES['ERROR_CONFIG_NOT_FOUND'])
            return
        msgs = configs_template(configs['trojan'])
        for message in msgs:
            if message:
                bot.send_message(call.message.chat.id, f"{message}",
                                 reply_markup=main_menu_keyboard_markup())

    # User Configs - Subscription Configs Callback
    elif key == "conf_sub_url":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['sub_link'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_SUB']}\n<code>{sub['sub_link']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )
    # User Configs - Base64 Subscription Configs Callback
    elif key == "conf_sub_url_b64":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['sub_link_b64'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_SUB_B64']}\n<code>{sub['sub_link_b64']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )
    # User Configs - Subscription Configs For Clash Callback
    elif key == "conf_clash":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['clash_configs'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_CLASH']}\n<code>{sub['clash_configs']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )
    # User Configs - Subscription Configs For Hiddify Callback
    elif key == "conf_hiddify":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['hiddify_configs'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_HIDDIFY']}\n<code>{sub['hiddify_configs']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )

    elif key == "conf_sub_auto":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['sub_link_auto'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_SUB_AUTO']}\n<code>{sub['sub_link_auto']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )

    elif key == "conf_sub_sing_box":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['sing_box'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_SING_BOX']}\n<code>{sub['sing_box']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )

    elif key == "conf_sub_full_sing_box":
        sub = utils.sub_links(value)
        if not sub:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        qr_code = utils.txt_to_qr(sub['sing_box_full'])
        if not qr_code:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'])
            return
        bot.send_photo(
            call.message.chat.id,
            photo=qr_code,
            caption=f"{KEY_MARKUP['CONFIGS_FULL_SING_BOX']}\n<code>{sub['sing_box_full']}</code>",
            reply_markup=main_menu_keyboard_markup()
        )

    # manual
    elif key == "msg_manual":
        settings = utils.all_configs_settings()
        android_msg = settings['msg_manual_android'] if settings['msg_manual_android'] else MESSAGES['MANUAL_ANDROID']
        ios_msg = settings['msg_manual_ios'] if settings['msg_manual_ios'] else MESSAGES['MANUAL_IOS']
        win_msg = settings['msg_manual_windows'] if settings['msg_manual_windows'] else MESSAGES['MANUAL_WIN']
        mac_msg = settings['msg_manual_mac'] if settings['msg_manual_mac'] else MESSAGES['MANUAL_MAC']
        linux_msg = settings['msg_manual_linux'] if settings['msg_manual_linux'] else MESSAGES['MANUAL_LIN']
        if value == 'android':
            bot.send_message(call.message.chat.id, android_msg, reply_markup=main_menu_keyboard_markup())
        elif value == 'ios':
            bot.send_message(call.message.chat.id, ios_msg, reply_markup=main_menu_keyboard_markup())
        elif value == 'win':
            bot.send_message(call.message.chat.id, win_msg, reply_markup=main_menu_keyboard_markup())
        elif value == 'mac':
            bot.send_message(call.message.chat.id, mac_msg, reply_markup=main_menu_keyboard_markup())
        elif value == 'lin':
            bot.send_message(call.message.chat.id, linux_msg, reply_markup=main_menu_keyboard_markup())





    # ----------------------------------- Back Area -----------------------------------
    # Back To User Menu
    elif key == "back_to_user_panel":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=user_info_markup(value))
        

    # Back To Plans
    elif key == "back_to_plans":
        # Return from payment (gift/wallet) back to the plans list for the last selected server (buy flow).
        if not selected_server_id:
            # This can happen when the invoice was auto-sent (after wallet top-up approval) and
            # user presses Back without any prior "server_selected" interaction in this session.
            # Try to recover server_id from pending invoice.
            try:
                pending = USERS_DB.get_pending_invoice(call.message.chat.id)
                if pending and pending.get('plan_id') is not None:
                    plan_rows = USERS_DB.find_plan(id=int(pending.get('plan_id')))
                    if plan_rows:
                        selected_server_id = int(plan_rows[0].get('server_id') or 0) or selected_server_id
            except Exception:
                pass
        if not selected_server_id:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        plans = USERS_DB.find_plan(server_id=selected_server_id)
        if not plans:
            bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return

        # IMPORTANT:
        # In buy flow, we must NOT mix add-on plans (volume-only / time-only) with base monthly plans.
        # Otherwise users can accidentally "buy" add-ons without an existing subscription.
        settings = utils.all_configs_settings()
        monthly_plans = []
        try:
            monthly_plans = [
                p for p in plans
                if _as_int(p.get('size_gb'), 0) > 0 and _as_int(p.get('days'), 0) > 0
            ]
        except Exception:
            # If anything goes wrong, keep behaviour safe by showing only status-enabled plans
            monthly_plans = [p for p in plans if p.get('status')]

        # Same rule as server_selected: only show add-on entry in buy list if user already has a subscription.
        try:
            has_sub = bool(USERS_DB.find_user_subscription_uuids_by_server(call.message.chat.id, selected_server_id))
        except Exception:
            has_sub = False
        show_addons_entry = bool(settings.get('addon_plans_enabled')) and bool(settings.get('addon_plans_show_in_buy')) and has_sub

        # Build full-width header (same UI as after selecting a server)
        try:
            from UserBot.templates import plans_list_buy_header_template
            server_title = None
            try:
                srv = USERS_DB.find_server(id=selected_server_id)
                # find_server may return list/tuple of dicts or a dict depending on dbManager version
                if isinstance(srv, list) and srv:
                    server_title = srv[0].get('title') or srv[0].get('name')
                elif isinstance(srv, tuple) and len(srv) > 0 and isinstance(srv[0], dict):
                    server_title = srv[0].get('title') or srv[0].get('name')
                elif isinstance(srv, dict):
                    server_title = srv.get('title') or srv.get('name')
            except Exception:
                server_title = None

            text = plans_list_buy_header_template(server_title=server_title)
            # Use the dedicated buy-list markup (optionally with "add-on" entry)
            plan_markup = plans_list_buy_markup(monthly_plans, server_id=selected_server_id, show_addons_button=show_addons_entry)
            if not plan_markup:
                bot.send_message(call.message.chat.id, MESSAGES['PLANS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
                return

            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                reply_markup=plan_markup,
                parse_mode="HTML",
            )
        except Exception:
            # Fallback to legacy behaviour if template import is missing for any reason
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=MESSAGES['PLANS_LIST'],
                reply_markup=plans_list_buy_markup(monthly_plans, server_id=selected_server_id, show_addons_button=show_addons_entry),
            )

    elif key == "back_to_renewal_plans":
        # Legacy back handler kept for compatibility. Route to the new split base-plans view.
        sess_state = renew_subscription_dict.get(call.message.chat.id, {})
        server_id = sess_state.get('server_id')
        if server_id is None:
            try:
                server_id = int(selected_server_id)
            except Exception:
                server_id = 0
        # Reuse the dedicated base view logic.
        call.data = f"back_to_renewal_base:{server_id}|{value}"
        call_split = call.data.split(':', 1)
        # Prevent recursion if something goes wrong.
        try:
            key2, value2 = call_split[0], call_split[1]
        except Exception:
            key2, value2 = 'back_to_renewal_base', f"{server_id}|{value}"
        # Execute the same logic inline
        # (Copy of back_to_renewal_base handler)
        try:
            server_id_str, uuid = value2.split('|', 1)
            server_id = int(server_id_str)
        except Exception:
            bot.send_message(call.message.chat.id, MESSAGES['UNKNOWN_ERROR'], reply_markup=main_menu_keyboard_markup())
            return
        settings = utils.all_configs_settings()
        plans = USERS_DB.find_plan(server_id=server_id) or []
        base_plans = []
        addon_plans = []
        for p in plans:
            sz = _as_int(p.get('size_gb'), 0)
            dy = _as_int(p.get('days'), 0)
            if sz > 0 and dy > 0:
                base_plans.append(p)
            elif (sz > 0 and dy == 0) or (sz == 0 and dy > 0):
                addon_plans.append(p)
        show_addons_button = bool(settings.get('addon_plans_enabled')) and len(addon_plans) > 0
        markup = renewal_plans_list_markup(base_plans, uuid=uuid, server_id=server_id, show_addons_button=show_addons_button)
        if call.message.chat.id in renew_subscription_dict:
            renew_subscription_dict[call.message.chat.id]['last_list'] = 'base'
        update_info_subscription(call.message, uuid, markup)
    
    elif key == "back_to_servers":
        servers = USERS_DB.select_servers()
        server_list = []
        if not servers:
            bot.send_message(message.chat.id, MESSAGES['SERVERS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
            return
        for server in servers:
            user_index = 0
            #if server['status']:
            users_list = api.select(server['url'] + API_PATH)
            if users_list:
                user_index = len(users_list)
            if server['user_limit'] > user_index:
                server_list.append([server,True])
            else:
                server_list.append([server,False])
                
        # bad request telbot api
        # bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id,
        #                                   text= MESSAGES['SERVERS_LIST'], reply_markup=servers_list_markup(server_list))
        #bot.delete_message(message.chat.id, msg_wait.message_id)
        bot.edit_message_text(reply_markup=servers_list_markup(server_list), chat_id=call.message.chat.id, message_id=call.message.message_id,
                                      text=MESSAGES['SERVERS_LIST'])
        

    # Delete Message
    elif key == "del_msg":
        bot.delete_message(call.message.chat.id, call.message.message_id)

    # Invalid Command
    else:
        bot.answer_callback_query(call.id, MESSAGES['ERROR_INVALID_COMMAND'])


# *********************************** Message Handler Area ***********************************
# Bot Start Message Handler
@bot.message_handler(commands=['start'])
def start_bot(message: Message):
    if is_user_banned(message.chat.id):
        return
    settings = utils.all_configs_settings()

    MESSAGES['WELCOME'] = MESSAGES['WELCOME'] if not settings['msg_user_start'] else settings['msg_user_start']
    
    # Parse optional referral code: /start <code>
    parts = (message.text or "").strip().split(maxsplit=1)
    ref_code = parts[1].strip() if len(parts) > 1 else None

    if USERS_DB.find_user(telegram_id=message.chat.id):
        edit_name= USERS_DB.edit_user(telegram_id=message.chat.id,full_name=message.from_user.full_name)
        edit_username = USERS_DB.edit_user(telegram_id=message.chat.id,username=message.from_user.username)
        # Ensure referral_code exists for existing users
        USERS_DB.ensure_user_referral_code(message.chat.id)
        bot.send_message(message.chat.id, MESSAGES['WELCOME'], reply_markup=main_menu_keyboard_markup())
    else:
        created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = USERS_DB.add_user(telegram_id=message.chat.id,username=message.from_user.username, full_name=message.from_user.full_name, created_at=created_at)
        if not status:
            bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                             reply_markup=main_menu_keyboard_markup())
            return
        wallet_status = USERS_DB.find_wallet(telegram_id=message.chat.id)
        if not wallet_status:
            status = USERS_DB.add_wallet(telegram_id=message.chat.id)
            if not status:
                bot.send_message(message.chat.id, f"{MESSAGES['UNKNOWN_ERROR']}:Wallet",
                                 reply_markup=main_menu_keyboard_markup())
                return
            bot.send_message(message.chat.id, MESSAGES['WELCOME'], reply_markup=main_menu_keyboard_markup())

        # Referral: bind inviter and (maybe) pay signup bonus
        enabled, ref_bonus_rial, new_bonus_rial, _percent, _dash, _share = _get_referral_settings()
        if enabled and ref_code:
            try:
                # Ensure new user has a referral code of their own
                USERS_DB.ensure_user_referral_code(message.chat.id)
                inviter = USERS_DB.find_user_by_referral_code(ref_code)
                if inviter:
                    inviter_id = int(inviter[0]['telegram_id'])
                    if inviter_id != int(message.chat.id):
                        # If force-join is enabled and user is not yet in channel, postpone payout.
                        join_ok = user_channel_status(message.chat.id)
                        settings_now = utils.all_configs_settings()
                        pending = bool(int(settings_now.get('force_join_channel') or 0) == 1 and not join_ok)
                        USERS_DB.set_user_referred_by(message.chat.id, inviter_id, pending_bonus=pending)
                        if not pending:
                            process_referral_signup_bonus(message.chat.id, inviter_id, ref_bonus_rial, new_bonus_rial)
            except Exception as e:
                logging.error(f"Referral bind failed for {message.chat.id}: {e}")

    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return


# If user is not in users table, request /start
@bot.message_handler(func=lambda message: not USERS_DB.find_user(telegram_id=message.chat.id))
def not_in_users_table(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['REQUEST_START'])


# User Subscription Status Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['SUBSCRIPTION_STATUS'])
def subscription_status(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    non_order_subs = utils.non_order_user_info(message.chat.id)
    order_subs = utils.order_user_info(message.chat.id)

    if not non_order_subs and not order_subs:
        bot.send_message(message.chat.id, MESSAGES['SUBSCRIPTION_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
        return

    if non_order_subs:
        for non_order_sub in non_order_subs:
            if non_order_sub:
                server_id = non_order_sub['server_id']
                server = USERS_DB.find_server(id=server_id)
                if not server:
                    bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                                    reply_markup=main_menu_keyboard_markup())
                    return
                server = server[0]
                api_user_data = user_info_template(non_order_sub['sub_id'], server, non_order_sub, MESSAGES['INFO_USER'])
                bot.send_message(message.chat.id, api_user_data,
                                 reply_markup=user_info_non_sub_markup(non_order_sub['uuid']))
    if order_subs:
        for order_sub in order_subs:
            if order_sub:
                server_id = order_sub['server_id']
                server = USERS_DB.find_server(id=server_id)
                if not server:
                    bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'],
                                    reply_markup=main_menu_keyboard_markup())
                    return
                server = server[0]
                api_user_data = user_info_template(order_sub['sub_id'], server, order_sub, MESSAGES['INFO_USER'])
                bot.send_message(message.chat.id, api_user_data,
                                 reply_markup=user_info_markup(order_sub['uuid']))


# User Buy Subscription Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['BUY_SUBSCRIPTION'])
def buy_subscription(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    settings = utils.all_configs_settings()
    if not settings['buy_subscription_status']:
        bot.send_message(message.chat.id, MESSAGES['BUY_SUBSCRIPTION_CLOSED'], reply_markup=main_menu_keyboard_markup())
        return
    #msg_wait = bot.send_message(message.chat.id, MESSAGES['WAIT'], reply_markup=main_menu_keyboard_markup())
    servers = USERS_DB.select_servers()
    server_list = []
    if not servers:
        bot.send_message(message.chat.id, MESSAGES['SERVERS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
        return
    for server in servers:
        user_index = 0
        #if server['status']:
        users_list = api.select(server['url'] + API_PATH)
        if users_list:
            user_index = len(users_list)
        if server['user_limit'] > user_index:
            server_list.append([server,True])
        else:
            server_list.append([server,False])
    # bad request telbot api
    # bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id,
    #                                   text= MESSAGES['SERVERS_LIST'], reply_markup=servers_list_markup(server_list))
    #bot.delete_message(message.chat.id, msg_wait.message_id)
    bot.send_message(message.chat.id, MESSAGES['SERVERS_LIST'], reply_markup=servers_list_markup(server_list))


# Config To QR Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['TO_QR'])
def to_qr(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_TO_QR'], reply_markup=cancel_markup())
    bot.register_next_step_handler(message, next_step_to_qr)


# Help Guide Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['MANUAL'])
def help_guide(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['MANUAL_HDR'],
                     reply_markup=users_bot_management_settings_panel_manual_markup())
    
# Help Guide Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['FAQ'])
def faq(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    settings = utils.all_configs_settings()
    faq_msg = settings['msg_faq'] if settings['msg_faq'] else MESSAGES['UNKNOWN_ERROR']
    bot.send_message(message.chat.id, faq_msg, reply_markup=main_menu_keyboard_markup())


# Ticket To Support Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['SEND_TICKET'])
def send_ticket(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['SEND_TICKET_TO_ADMIN_TEMPLATE'], reply_markup=send_ticket_to_admin())


# Link Subscription Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['LINK_SUBSCRIPTION'])
def link_subscription(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['ENTER_SUBSCRIPTION_INFO'], reply_markup=cancel_markup())
    bot.register_next_step_handler(message, next_step_link_subscription)


# User Buy Subscription Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['WALLET'])
def wallet_balance(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    user = USERS_DB.find_user(telegram_id=message.chat.id)
    if user:
        wallet_status = USERS_DB.find_wallet(telegram_id=message.chat.id)
        if not wallet_status:
            status = USERS_DB.add_wallet(telegram_id=message.chat.id)
            if not status:
                bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'])
                return

        wallet = USERS_DB.find_wallet(telegram_id=message.chat.id)
        wallet = wallet[0]
        telegram_user_data = wallet_info_template(wallet['balance'])

        bot.send_message(message.chat.id, telegram_user_data,
                         reply_markup=wallet_info_markup())
    else:
        bot.send_message(message.chat.id, MESSAGES['UNKNOWN_ERROR'])


# Referral dashboard
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP.get('REFERRAL', ''))
def referral_dashboard(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return

    enabled, ref_bonus_rial, _new_bonus_rial, percent, dashboard_tpl, _share_text = _get_referral_settings()
    if not enabled:
        bot.send_message(message.chat.id, "این بخش در حال حاضر غیرفعال است.", reply_markup=main_menu_keyboard_markup())
        return

    USERS_DB.ensure_user_referral_code(message.chat.id)

    ref_count = USERS_DB.count_referred_users(message.chat.id)
    buy_count, buy_total = USERS_DB.referral_purchase_stats(message.chat.id)
    signup_total, commission_total = USERS_DB.referral_earnings_totals(message.chat.id)

    # Convert rial -> toman string
    def _toman_str(rial_val: int) -> str:
        try:
            return utils.rial_to_toman(int(rial_val))
        except Exception:
            return str(int(rial_val) // 10)

    text = dashboard_tpl or ""
    try:
        text = text.format(
            signup_bonus_toman=_toman_str(ref_bonus_rial),
            commission_percent=int(percent),
            ref_count=int(ref_count),
            buy_count=int(buy_count),
            buy_total_toman=_toman_str(buy_total),
            signup_total_toman=_toman_str(signup_total),
            commission_total_toman=_toman_str(commission_total),
        )
    except Exception:
        # Fallback if template formatting fails
        text = (
            f"📊 آمار دعوت از دوستان\n\n"
            f"• 👥 زیرمجموعه‌ها: {ref_count} نفر\n"
            f"• 🛒 خریدها: {buy_count} عدد\n"
            f"• 💵 مجموع خرید: {_toman_str(buy_total)} تومان\n\n"
            f"• 🎉 مجموع هدیه عضویت: {_toman_str(signup_total)} تومان\n"
            f"• 💸 مجموع پورسانت: {_toman_str(commission_total)} تومان"
        )

    bot.send_message(message.chat.id, text, reply_markup=referral_dashboard_markup())


# User Buy Subscription Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['FREE_TEST'])
def free_test(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    settings = utils.all_configs_settings()
    if not settings['test_subscription']:
        bot.send_message(message.chat.id, MESSAGES['FREE_TEST_NOT_AVAILABLE'], reply_markup=main_menu_keyboard_markup())
        return
    users = USERS_DB.find_user(telegram_id=message.chat.id)
    if users:
        user = users[0]
        if user['test_subscription']:
            bot.send_message(message.chat.id, MESSAGES['ALREADY_RECEIVED_FREE'],
                             reply_markup=main_menu_keyboard_markup())
            return
        else:
            # bot.send_message(message.chat.id, MESSAGES['REQUEST_SEND_NAME'], reply_markup=cancel_markup())
            # bot.register_next_step_handler(message, next_step_send_name_for_get_free_test)
            msg_wait = bot.send_message(message.chat.id, MESSAGES['WAIT'])
            servers = USERS_DB.select_servers()
            server_list = []
            if not servers:
                bot.send_message(message.chat.id, MESSAGES['SERVERS_NOT_FOUND'], reply_markup=main_menu_keyboard_markup())
                return
            for server in servers:
                user_index = 0
                #if server['status']:
                users_list = api.select(server['url'] + API_PATH)
                if users_list:
                    user_index = len(users_list)
                if server['user_limit'] > user_index:
                    server_list.append([server,True])
                else:
                    server_list.append([server,False])
            # bad request telbot api
            # bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id,
            #                                   text= MESSAGES['SERVERS_LIST'], reply_markup=servers_list_markup(server_list))
            bot.delete_message(message.chat.id, msg_wait.message_id)
            bot.send_message(message.chat.id, MESSAGES['SERVERS_LIST'], reply_markup=servers_list_markup(server_list, True))



# Cancel Message Handler
@bot.message_handler(func=lambda message: message.text == KEY_MARKUP['CANCEL'])
def cancel(message: Message):
    if is_user_banned(message.chat.id):
        return
    join_status = is_user_in_channel(message.chat.id)
    if not join_status:
        return
    bot.send_message(message.chat.id, MESSAGES['CANCELED'], reply_markup=main_menu_keyboard_markup())


# *********************************** Main Area ***********************************
def start():
    # Bot Start Commands
    try:
        bot.set_my_commands([
            telebot.types.BotCommand("/start", BOT_COMMANDS['START']),
        ])
    except telebot.apihelper.ApiTelegramException as e:
        if e.result.status_code == 401:
            logging.error("Invalid Telegram Bot Token!")
            exit(1)
    # Welcome to Admin
    for admin in ADMINS_ID:
        try:
            bot.send_message(admin, MESSAGES['WELCOME_TO_ADMIN'])
        except Exception as e:
            logging.warning(f"Error in send message to admin {admin}: {e}")
    bot.enable_save_next_step_handlers()
    bot.load_next_step_handlers()
    bot.infinity_polling()
