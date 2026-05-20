# RootVPN Android

Android-клиент RootVPN для двух типов профилей:

- `vless://...` - основной быстрый режим через VLESS/Reality, будущая интеграция через sing-box/libbox.
- `olcrtc://...` - аварийный Rescue-режим через olcRTC/WB Stream, будущая интеграция через локальный olcRTC client core.

## Current State

Это рабочий каркас приложения, а не production VPN-клиент.

Уже есть:

- Android `VpnService` shell.
- Deep links для `vless://`, `rootvpn://`, `olcrtc://`.
- Парсер RootVPN Rescue URI и VLESS URI.
- Локальное сохранение последнего импортированного профиля.
- Compose UI-прототип.

Еще нужно подключить:

- реальный sing-box/libbox core для VLESS;
- реальный olcRTC Android/client binary или gomobile library;
- TUN -> core routing через готовый TUN stack/tun2socks/libbox, без самописного TCP/IP стека.

Пока реальный TUN routing намеренно выключен предохранителем в `RootVpnService`, чтобы приложение не blackhole'ило интернет на устройстве.

## Open In Android Studio

1. Open Android Studio.
2. Open this `app/` directory.
3. Let Gradle sync.
4. Run `:app:testDebugUnitTest` after sync.

## Next Engineering Step

Recommended path: integrate sing-box Android/libbox first, because it gives a real TUN engine for VLESS and later can route Rescue via local SOCKS if olcRTC exposes `127.0.0.1:8808`.
