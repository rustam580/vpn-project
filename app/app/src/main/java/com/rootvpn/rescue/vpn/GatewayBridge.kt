package com.rootvpn.rescue.vpn

object GatewayBridge {
    private var activePort = 8809

    /**
     * Scaffold-only switch. A real build should replace this with libbox/tun2socks routing
     * or a gomobile bridge that points Android traffic at the selected local core.
     */
    fun switchCore(port: Int) {
        activePort = port
        RootVpnService.log("[GatewayBridge] Selected local backend port: $port")
    }

    fun getActivePort(): Int = activePort
}
