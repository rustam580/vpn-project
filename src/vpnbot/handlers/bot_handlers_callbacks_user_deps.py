from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UserCallbackDeps:
    settings: Any
    repo: Any
    marzban: Any
    guard_callback_rate_limit: Any
    list_replaceable_devices: Any
    get_bot_username: Any
    build_user_faq_text: Any
    normalize_channel_url: Any
    pending_issue: set[int]
    pending_device_rename: dict[int, int]
    replace_device_slot: Any
    send_status: Any
    send_device_links: Any
    collect_device_links: Any
    send_configs_in_chat: Any
    render_config_block: Any
    plans_list_text: Any
    buy_plan_keyboard: Any
    find_plan: Any
    plan_title: Any
    plan_gb_text: Any
    payment_methods_keyboard: Any
    cryptobot_create_invoice: Any
    yookassa_create_payment: Any
    track_event: Any
    pay_action_keyboard: Any
    next_device_slot: Any
    check_and_apply_payment: Any
    device_methods_keyboard: Any
    devices_replace_keyboard: Any
    devices_rename_keyboard: Any
    device_replace_confirm_keyboard: Any
    device_label: Any
