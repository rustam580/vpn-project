from __future__ import annotations

from aiogram import Router

from src.vpnbot.handlers.bot_handlers_callbacks_user_configs import register_config_callbacks
from src.vpnbot.handlers.bot_handlers_callbacks_user_deps import UserCallbackDeps
from src.vpnbot.handlers.bot_handlers_callbacks_user_devices import register_device_callbacks
from src.vpnbot.handlers.bot_handlers_callbacks_user_payments import register_payment_callbacks
from src.vpnbot.handlers.bot_handlers_callbacks_user_quick import register_quick_callbacks


def register_user_callback_handlers(*, router: Router, deps: UserCallbackDeps) -> None:
    register_quick_callbacks(router=router, deps=deps)
    register_device_callbacks(router=router, deps=deps)
    register_config_callbacks(router=router, deps=deps)
    register_payment_callbacks(router=router, deps=deps)
