# Description: This file contains all the reply and inline keyboard markups used in the bot.
from telebot import types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from UserBot.content import KEY_MARKUP, MESSAGES
from UserBot.content import MESSAGES
from Utils.utils import rial_to_toman,all_configs_settings
from Utils.api import *


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

# Main Menu Reply Keyboard Markup
def main_menu_keyboard_markup():
    markup = ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
    markup.add(KeyboardButton(KEY_MARKUP['SUBSCRIPTION_STATUS']))
    markup.add(KeyboardButton(KEY_MARKUP['LINK_SUBSCRIPTION']), KeyboardButton(KEY_MARKUP['BUY_SUBSCRIPTION']))
    # Keep wallet/test on the same row, and show Referral as a full-width button on the next row.
    markup.add(KeyboardButton(KEY_MARKUP['FREE_TEST']), KeyboardButton(KEY_MARKUP['WALLET']))
    markup.row(KeyboardButton(KEY_MARKUP.get('REFERRAL', '👥 دعوت از دوستان')))
    # KeyboardButton(KEY_MARKUP['TO_QR']),
    settings = all_configs_settings()
    if settings['msg_faq']:
        markup.add(KeyboardButton(KEY_MARKUP['SEND_TICKET']),
                   KeyboardButton(KEY_MARKUP['MANUAL']), KeyboardButton(KEY_MARKUP['FAQ']))
    else:
        markup.add(KeyboardButton(KEY_MARKUP['SEND_TICKET']),
                   KeyboardButton(KEY_MARKUP['MANUAL']))
    return markup


def user_info_markup(uuid):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_LIST'], callback_data=f"configs_list:{uuid}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['RENEWAL_SUBSCRIPTION'], callback_data=f"renewal_subscription:{uuid}"))
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['UPDATE_SUBSCRIPTION_INFO'], callback_data=f"update_info_subscription:{uuid}"))
    return markup


# Subscription URL Inline Keyboard Markup
def sub_url_user_list_markup(uuid):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    settings = all_configs_settings()
    if settings['visible_conf_dir']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_DIR'], callback_data=f"conf_dir:{uuid}"))
    if settings['visible_conf_sub_auto']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_SUB_AUTO'], callback_data=f"conf_sub_auto:{uuid}"))
    if settings['visible_conf_sub_url']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_SUB'], callback_data=f"conf_sub_url:{uuid}"))
    if settings['visible_conf_sub_url_b64']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_SUB_B64'], callback_data=f"conf_sub_url_b64:{uuid}"))
    if settings['visible_conf_clash']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_CLASH'], callback_data=f"conf_clash:{uuid}"))
    if settings['visible_conf_hiddify']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_HIDDIFY'], callback_data=f"conf_hiddify:{uuid}"))
    if settings['visible_conf_sub_sing_box']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_SING_BOX'], callback_data=f"conf_sub_sing_box:{uuid}"))
    if settings['visible_conf_sub_full_sing_box']:
        markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_FULL_SING_BOX'],
                                        callback_data=f"conf_sub_full_sing_box:{uuid}"))

    markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_user_panel:{uuid}"))

    return markup

# Subscription Configs Inline Keyboard Markup
def sub_user_list_markup(uuid,configs):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    if configs['vless']:
        markup.add(InlineKeyboardButton('Vless', callback_data=f"conf_dir_vless:{uuid}"))
    if configs['vmess']:
        markup.add(InlineKeyboardButton('Vmess', callback_data=f"conf_dir_vmess:{uuid}"))
    if configs['trojan']:
        markup.add(InlineKeyboardButton('Trojan', callback_data=f"conf_dir_trojan:{uuid}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_user_panel:{uuid}"))
    # markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_user_panel:{uuid}"))
    # markup.add(InlineKeyboardButton('Vmess', callback_data=f"conf_dir_vmess:{uuid}"))
    # markup.add(InlineKeyboardButton('Trojan', callback_data=f"conf_dir_trojan:{uuid}"))

    return markup

def user_info_non_sub_markup(uuid):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton(KEY_MARKUP['CONFIGS_LIST'], callback_data=f"configs_list:{uuid}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['RENEWAL_SUBSCRIPTION'], callback_data=f"renewal_subscription:{uuid}"))
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['UPDATE_SUBSCRIPTION_INFO'], callback_data=f"update_info_subscription:{uuid}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['UNLINK_SUBSCRIPTION'], callback_data=f"unlink_subscription:{uuid}"))
    return markup


def confirm_subscription_markup(uuid):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(KEY_MARKUP['YES'], callback_data=f"confirm_subscription:{uuid}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['NO'], callback_data=f"cancel_subscription:{uuid}"))
    return markup


def confirm_buy_plan_markup(plan_id, renewal=False, uuid=None, gift_applied: bool = False, back_callback: str | None = None):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1

    # Gift code (buy + renewal). When applied, allow clearing.
    if gift_applied:
        clear_cb = "gift_code_clear" if not renewal else "gift_code_clear_renewal"
        markup.add(InlineKeyboardButton(KEY_MARKUP['CLEAR_GIFT_CODE'], callback_data=f"{clear_cb}:{plan_id}"))
    else:
        gift_cb = "gift_code" if not renewal else "gift_code_renewal"
        markup.add(InlineKeyboardButton(KEY_MARKUP['GIFT_CODE'], callback_data=f"{gift_cb}:{plan_id}"))

    callback = "confirm_buy_from_wallet" if not renewal else "confirm_renewal_from_wallet"
    markup.add(InlineKeyboardButton(KEY_MARKUP['BUY_FROM_WALLET'], callback_data=f"{callback}:{plan_id}"))

    if renewal:
        # When renewing, we may want to go back to either the base plans list or the add-on plans list.
        # If a custom callback is provided, use it.
        cb = back_callback or f"back_to_renewal_plans:{uuid}"
        markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=cb))
    else:
        markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_plans:None"))
    return markup


def send_screenshot_markup(plan_id):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(KEY_MARKUP['SEND_SCREENSHOT'], callback_data=f"send_screenshot:{plan_id}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['CANCEL'], callback_data=f"cancel_increase_wallet_balance:{plan_id}"))
    return markup


def plans_list_markup(plans, renewal=False,uuid=None):
    markup = InlineKeyboardMarkup(row_width=1)
    callback = "renewal_plan_selected" if renewal else "plan_selected"
    keys = []
    for plan in plans:
        if plan['status']:
            # Support plan categories:
            # - Monthly (size_gb>0 and days>0)
            # - Volume-only (days==0, size_gb>0)
            # - Time-only (size_gb==0, days>0)
            size_gb = _as_int(plan.get('size_gb'), 0)
            days = _as_int(plan.get('days'), 0)
            price = rial_to_toman(plan['price'])
            if size_gb > 0 and days > 0:
                title = f"{size_gb}{MESSAGES['GB']} | {days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
            elif size_gb > 0 and days == 0:
                # Volume-only
                title = f"+{size_gb}{MESSAGES['GB']} | {price} {MESSAGES['TOMAN']}"
            elif size_gb == 0 and days > 0:
                # Time-only
                title = f"+{days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
            else:
                # Fallback (should not happen)
                title = f"{price} {MESSAGES['TOMAN']}"

            keys.append(InlineKeyboardButton(title, callback_data=f"{callback}:{plan['id']}"))
    if len(keys) == 0:
        return None
    if renewal:
        keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_user_panel:{uuid}"))
    else:
        keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_servers:None"))
    markup.add(*keys)
    return markup


# Buy flow plans list with an optional "add-on" entry.
def plans_list_buy_markup(plans, server_id: int, show_addons_button: bool = False):
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    for plan in plans:
        if plan['status']:
            size_gb = _as_int(plan.get('size_gb'), 0)
            days = _as_int(plan.get('days'), 0)
            price = rial_to_toman(plan['price'])
            # Buy list shows only full plans (monthly) here
            title = f"{size_gb}{MESSAGES['GB']} | {days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
            keys.append(InlineKeyboardButton(title, callback_data=f"plan_selected:{plan['id']}"))

    if show_addons_button:
        keys.append(InlineKeyboardButton(KEY_MARKUP['BUY_ADDON_PLANS'], callback_data=f"buy_addon_start:{server_id}"))

    if len(keys) == 0:
        return None
    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_servers:None"))
    markup.add(*keys)
    return markup


def addon_subscriptions_list_markup(sub_items, server_id: int):
    """sub_items: list of dicts: {uuid, title}"""
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    for it in sub_items:
        # Encode server_id alongside uuid to avoid costly panel-scans on callback.
        # callback_data length stays well under Telegram limits.
        title = it.get('title') or it['uuid']
        keys.append(InlineKeyboardButton(title, callback_data=f"buy_addon_select_sub:{server_id}|{it['uuid']}"))
    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK_TO_BUY_PLANS'], callback_data=f"server_selected:{server_id}"))
    markup.add(*keys)
    return markup


def renewal_plans_list_markup(base_plans, uuid: str, server_id: int, show_addons_button: bool = False):
    """Renewal list: show base (monthly) plans only, with an optional separate entry for add-ons."""
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    callback = "renewal_plan_selected"

    for plan in base_plans:
        if not plan.get('status'):
            continue
        size_gb = _as_int(plan.get('size_gb'), 0)
        days = _as_int(plan.get('days'), 0)
        # Base (monthly) only
        if not (size_gb > 0 and days > 0):
            continue
        price = rial_to_toman(plan['price'])
        title = f"{size_gb}{MESSAGES['GB']} | {days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
        keys.append(InlineKeyboardButton(title, callback_data=f"{callback}:{plan['id']}"))

    if show_addons_button:
        keys.append(InlineKeyboardButton(KEY_MARKUP['BUY_ADDON_PLANS'], callback_data=f"renewal_addon_start:{server_id}|{uuid}"))

    if len(keys) == 0:
        return None

    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"back_to_user_panel:{uuid}"))
    markup.add(*keys)
    return markup


def renewal_addon_plans_list_markup(addon_plans, uuid: str, server_id: int):
    """Renewal list: show add-on plans only (volume/time)."""
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    callback = "renewal_plan_selected"

    for plan in addon_plans:
        if not plan.get('status'):
            continue
        size_gb = _as_int(plan.get('size_gb'), 0)
        days = _as_int(plan.get('days'), 0)
        price = rial_to_toman(plan['price'])
        if size_gb > 0 and days == 0:
            title = f"+{size_gb}{MESSAGES['GB']} | {price} {MESSAGES['TOMAN']}"
        elif size_gb == 0 and days > 0:
            title = f"+{days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
        else:
            continue
        keys.append(InlineKeyboardButton(title, callback_data=f"{callback}:{plan['id']}"))

    if len(keys) == 0:
        return None

    # Go back to base renewal plans without re-fetching from panel
    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"renewal_addon_back:{server_id}|{uuid}"))
    markup.add(*keys)
    return markup


def addon_plans_list_markup(plans, uuid: str, server_id: int):
    """Show only add-on plans. Callback encodes plan_id|uuid."""
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    for plan in plans:
        if not plan.get('status'):
            continue
        size_gb = _as_int(plan.get('size_gb'), 0)
        days = _as_int(plan.get('days'), 0)
        price = rial_to_toman(plan['price'])
        if size_gb > 0 and days == 0:
            title = f"+{size_gb}{MESSAGES['GB']} | {price} {MESSAGES['TOMAN']}"
        elif size_gb == 0 and days > 0:
            title = f"+{days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
        else:
            continue
        keys.append(InlineKeyboardButton(title, callback_data=f"buy_addon_plan_selected:{plan['id']}|{uuid}"))

    if len(keys) == 0:
        return None
    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"buy_addon_start:{server_id}"))
    markup.add(*keys)
    return markup


def buy_addon_plans_list_markup(addon_plans, server_id: int):
    """Buy flow: show add-on plans only.

    Important: we intentionally reuse the *renewal* callback (renewal_plan_selected)
    so the rest of the purchase pipeline (gift code, confirm markup, etc.) stays
    identical to renewal and avoids buy-only edge cases.
    The selected subscription UUID must be stored in renew_subscription_dict
    before this markup is used.
    """
    markup = InlineKeyboardMarkup(row_width=1)
    keys = []
    callback = "renewal_plan_selected"

    for plan in addon_plans:
        if not plan.get('status'):
            continue
        size_gb = _as_int(plan.get('size_gb'), 0)
        days = _as_int(plan.get('days'), 0)
        price = rial_to_toman(plan['price'])

        if size_gb > 0 and days == 0:
            title = f"+{size_gb}{MESSAGES['GB']} | {price} {MESSAGES['TOMAN']}"
        elif size_gb == 0 and days > 0:
            title = f"+{days}{MESSAGES['DAY_EXPIRE']} | {price} {MESSAGES['TOMAN']}"
        else:
            continue
        keys.append(InlineKeyboardButton(title, callback_data=f"{callback}:{plan['id']}"))

    if len(keys) == 0:
        return None

    # Back to subscription picker for add-ons (buy flow)
    keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"buy_addon_start:{server_id}"))
    markup.add(*keys)
    return markup


# Server List - Server List - Inline Keyboard Markup
def servers_list_markup(servers, free_test=False):
    markup = InlineKeyboardMarkup(row_width=1)
    callback = "free_test_server_selected" if free_test else "server_selected"
    keys = []
    if servers:
        for server in servers:
            server_title = server[0]['title'] if server[1] else f"{server[0]['title']}⛔️"
            callback_2 = f"{server[0]['id']}" if server[1] else "False"
            keys.append(InlineKeyboardButton(f"{server_title}",
                                             callback_data=f"{callback}:{callback_2}"))
        keys.append(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"del_msg:None"))
    if len(keys) == 0:
        return None
    markup.add(*keys)
    return markup

def confirm_payment_by_admin(order_id):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['CONFIRM_PAYMENT'], callback_data=f"confirm_payment_by_admin:{order_id}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['NO'], callback_data=f"cancel_payment_by_admin:{order_id}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['SEND_MESSAGE'], callback_data=f"send_message_by_admin:{order_id}"))
    return markup

def notify_to_admin_markup(user):
    name = user['full_name'] if user['full_name'] else user['telegram_id']
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(f"{name}", callback_data=f"bot_user_info:{user['telegram_id']}"))
    return markup

def send_ticket_to_admin():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['SEND_TICKET_TO_SUPPORT'], callback_data=f"send_ticket_to_support:None"))
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['CANCEL'], callback_data=f"del_msg:None"))
    
    return markup

def answer_to_user_markup(user,user_id):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    name = user['full_name'] if user['full_name'] else user['telegram_id']
    markup.add(InlineKeyboardButton(f"{name}", callback_data=f"bot_user_info:{user['telegram_id']}"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['ANSWER'], callback_data=f"users_bot_send_message_by_admin:{user_id}"))
    return markup

def cancel_markup():
    markup = ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
    markup.add(KeyboardButton(KEY_MARKUP['CANCEL']))
    return markup


def wallet_info_markup():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['INCREASE_WALLET_BALANCE'], callback_data=f"increase_wallet_balance:wallet"))
    return markup

def wallet_info_specific_markup(amount):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['INCREASE_WALLET_BALANCE'], callback_data=f"increase_wallet_balance_specific:{amount}"))
    return markup

def force_join_channel_markup(channel_id):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    channel_id = channel_id.replace("@", "")
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['JOIN_CHANNEL'], url=f"https://t.me/{channel_id}",)
    )
    markup.add(
        InlineKeyboardButton(KEY_MARKUP['FORCE_JOIN_CHANNEL_ACCEPTED'], callback_data=f"force_join_status:None")
    )
    return markup


# -------------------- Referral (Invite Friends) --------------------
def referral_dashboard_markup():
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("📎 اشتراک گذاری لینک", callback_data="referral_share:None"),
        InlineKeyboardButton("🧾 تاریخچه درآمد", callback_data="referral_income_history:None"),
    )
    markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data="referral_back:None"))
    return markup


def referral_history_markup():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data="referral_dashboard:None"))
    return markup


def users_bot_management_settings_panel_manual_markup():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton(KEY_MARKUP['MANUAL_ANDROID'],
                                    callback_data=f"msg_manual:android"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['MANUAL_IOS'],
                                    callback_data=f"msg_manual:ios"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['MANUAL_WIN'],
                                    callback_data=f"msg_manual:win"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['MANUAL_MAC'],
                                    callback_data=f"msg_manual:mac"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['MANUAL_LIN'],
                                    callback_data=f"msg_manual:lin"))
    markup.add(InlineKeyboardButton(KEY_MARKUP['BACK'], callback_data=f"del_msg:None"))
    return markup