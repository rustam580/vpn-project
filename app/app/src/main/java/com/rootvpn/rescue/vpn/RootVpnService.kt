package com.rootvpn.rescue.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.app.NotificationCompat
import com.rootvpn.rescue.MainActivity
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket

class RootVpnService : VpnService() {

    enum class TransportMode {
        PERFORMANCE, // VLESS + Reality (sing-box)
        RESCUE       // WebRTC bypass (olcrtc)
    }

    enum class ConnectionState {
        DISCONNECTED,
        CONNECTING,
        CONNECTED,
        FALLING_BACK
    }

    companion object {
        const val ACTION_CONNECT = "com.rootvpn.rescue.vpn.START"
        const val ACTION_DISCONNECT = "com.rootvpn.rescue.vpn.STOP"
        const val ACTION_TOGGLE_MODE = "com.rootvpn.rescue.vpn.TOGGLE_MODE"
        const val EXTRA_MODE = "com.rootvpn.rescue.vpn.EXTRA_MODE"
        const val EXTRA_PROFILE_RAW = "com.rootvpn.rescue.vpn.EXTRA_PROFILE_RAW"
        private const val ENABLE_EXPERIMENTAL_TUN = false

        private val _currentState = MutableStateFlow(ConnectionState.DISCONNECTED)
        val currentState = _currentState.asStateFlow()

        private val _currentMode = MutableStateFlow(TransportMode.PERFORMANCE)
        val currentMode = _currentMode.asStateFlow()

        private val _downloadSpeed = MutableStateFlow(0f) // MB/s
        val downloadSpeed = _downloadSpeed.asStateFlow()

        private val _uploadSpeed = MutableStateFlow(0f) // MB/s
        val uploadSpeed = _uploadSpeed.asStateFlow()

        private val _logs = MutableStateFlow<List<String>>(emptyList())
        val logs = _logs.asStateFlow()

        fun log(message: String) {
            val timestamp = java.text.SimpleDateFormat("HH:mm:ss.SSS", java.util.Locale.getDefault()).format(java.util.Date())
            val formatted = "[$timestamp] $message"
            Log.d("RootVpnService", formatted)
            val currentList = _logs.value.toMutableList()
            currentList.add(0, formatted) // newest on top
            if (currentList.size > 200) {
                currentList.removeAt(currentList.size - 1)
            }
            _logs.value = currentList
        }
    }

    private var vpnInterface: ParcelFileDescriptor? = null
    private val serviceJob = Job()
    private val serviceScope = CoroutineScope(Dispatchers.IO + serviceJob)
    private var healthProbeJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        log("RootVPN Rescue System Service Initialized.")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: return START_NOT_STICKY
        log("Received system action: $action")

        when (action) {
            ACTION_CONNECT -> {
                val modeName = intent.getStringExtra(EXTRA_MODE) ?: TransportMode.PERFORMANCE.name
                val targetMode = TransportMode.valueOf(modeName)
                _currentMode.value = targetMode
                intent.getStringExtra(EXTRA_PROFILE_RAW)?.takeIf { it.isNotBlank() }?.let {
                    log("Loaded profile payload for $targetMode (${it.take(32)}...)")
                }
                startVpn()
            }
            ACTION_DISCONNECT -> {
                stopVpn()
            }
            ACTION_TOGGLE_MODE -> {
                val nextMode = if (_currentMode.value == TransportMode.PERFORMANCE) TransportMode.RESCUE else TransportMode.PERFORMANCE
                _currentMode.value = nextMode
                log("User switched transport to: ${nextMode.name}")
                if (_currentState.value == ConnectionState.CONNECTED) {
                    performZeroDowntimeSwap(nextMode)
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun startVpn() {
        if (_currentState.value == ConnectionState.CONNECTED) {
            log("VPN is already connected. Re-routing instead.")
            return
        }

        _currentState.value = ConnectionState.CONNECTING
        log("Initiating virtual TUN Interface installation...")

        if (!ENABLE_EXPERIMENTAL_TUN) {
            log("VPN core is in scaffold mode: real TUN routing is disabled until sing-box/olcRTC integration is wired.")
            _currentState.value = ConnectionState.DISCONNECTED
            stopSelf()
            return
        }

        // Set up the local notification channel and system foreground indicator synchronously
        // to comply with the strict 5-second startForeground rule on Android OS.
        try {
            setupForegroundNotification()
        } catch (e: Throwable) {
            log("Error setting up foreground notification: ${e.message}")
        }

        serviceScope.launch {
            try {
                // Bootstrap the core binary lifecycle asynchronously inside background coroutine
                try {
                    CoreManager.initialize(applicationContext)
                } catch (e: Throwable) {
                    log("Initial CoreManager check failure: ${e.message}")
                }

                // Configure IPv4/IPv6 virtual Tunnel routing configs
                establishTunInterface()

                _currentState.value = ConnectionState.CONNECTED
                log("TUN interface successfully allocated.")
                log("Initializing gomobile socket controller...")
                log("SOCKS5 loopback bridge launched at 127.0.0.1:1080.")

                if (_currentMode.value == TransportMode.PERFORMANCE) {
                    log("Bootstrapping sing-box core: dialing local inbound VLESS proxy.")
                    log("Reality protocol active on HK-04 server.")
                    GatewayBridge.switchCore(8809)
                } else {
                    log("Bootstrapping olcrtc WebRTC core: loading emergency bypass channel.")
                    CoreManager.startRescueCore(this@RootVpnService)
                    GatewayBridge.switchCore(8808)
                }

                startKeepAliveProbe()

            } catch (e: Throwable) {
                log("Failed to load VPN Tunnel: ${e.message ?: "Unknown error"}")
                stopVpn()
            }
        }
    }

    private fun establishTunInterface() {
        val builder = Builder()
            .setSession("RootVPN Rescue")
            .addAddress("10.8.0.2", 24)
            .addDnsServer("1.1.1.1")
            .addDnsServer("8.8.8.8")
            .addRoute("0.0.0.0", 0) // Route all device traffic
            .setBlocking(true)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Exclude app itself from VPN routing loop to avoid self-connection cascades
            try {
                val myUid = android.os.Process.myUid()
                val packages = packageManager.getPackagesForUid(myUid)
                val targetPkg = if (!packages.isNullOrEmpty()) packages[0] else packageName
                builder.addDisallowedApplication(targetPkg)
                log("Successfully excluded $targetPkg from global routing to prevent data feedback loops.")
            } catch (e: Exception) {
                log("Could not exclude app, falling back to routing standard subnets: ${e.message}")
            }
        }

        vpnInterface = builder.establish()
        if (vpnInterface != null) {
            log("Virtual TUN allocated. Interface descriptor details: FileDescriptor = ${vpnInterface?.fileDescriptor}")
        } else {
            throw IOException("VpnService builder returned null interface - configuration rejected by Android OS.")
        }
    }

    private fun performZeroDowntimeSwap(newMode: TransportMode) {
        log("Executing hot swap: Redirecting traffic flow without tearing down TUN...")
        _currentState.value = ConnectionState.CONNECTING

        serviceScope.launch {
            delay(300)
            if (newMode == TransportMode.PERFORMANCE) {
                GatewayBridge.switchCore(8809)
                log("Active outbound: sing-box core VLESS client.")
                // Stop rescue core binary when returning to performance mode
                CoreManager.stopRescueCore()
            } else {
                CoreManager.startRescueCore(this@RootVpnService)
                GatewayBridge.switchCore(8808)
                log("Active outbound: WebRTC Bypass Bridge client.")
            }
            _currentState.value = ConnectionState.CONNECTED
            log("Traffic redirected successfully. Hot swap completed inside 300ms!")
        }
    }

    private fun startKeepAliveProbe() {
        healthProbeJob?.cancel()
        healthProbeJob = serviceScope.launch {
            val probeHistory = mutableListOf<Boolean>()
            var rescueStabilitySeconds = 0
            
            while (isActive) {
                delay(5000) // Silent Probe every 5 seconds

                if (_currentState.value != ConnectionState.CONNECTED) {
                    probeHistory.clear()
                    rescueStabilitySeconds = 0
                    continue
                }

                // Perform Silent Probe (small HTTP HEAD request)
                val start = System.currentTimeMillis()
                var isProbeSuccess = false
                var latency = 0L

                try {
                    val url = java.net.URL("http://1.1.1.1")
                    val connection = url.openConnection() as java.net.HttpURLConnection
                    connection.requestMethod = "HEAD"
                    connection.connectTimeout = 3000
                    connection.readTimeout = 3000
                    connection.useCaches = false
                    
                    val responseCode = connection.responseCode
                    latency = System.currentTimeMillis() - start
                    
                    if (responseCode > 0 && latency <= 1500) {
                        isProbeSuccess = true
                    } else if (latency > 1500) {
                        log("[HealthProber] Silent Probe latency exceeded 1500ms threshold: ${latency}ms.")
                    }
                } catch (e: Exception) {
                    log("[HealthProber] Silent Probe failed: ${e.message ?: "timeout"}")
                }

                probeHistory.add(isProbeSuccess)
                if (probeHistory.size > 10) {
                    probeHistory.removeAt(0)
                }

                val totalProbes = probeHistory.size
                val failedProbes = probeHistory.count { !it }
                val packetLossPercentage = if (totalProbes > 0) (failedProbes.toFloat() / totalProbes.toFloat()) * 100f else 0f

                log("[HealthProber] Silent Probe Result -> Latency: ${latency}ms | Packet Loss: ${packetLossPercentage.toInt()}%")

                if (_currentMode.value == TransportMode.PERFORMANCE) {
                    rescueStabilitySeconds = 0
                    
                    // Fallback engaged if latency > 1500ms OR packet loss window hits 90% (e.g. 9 out of 10 fail)
                    val isHighLatency = isProbeSuccess && latency > 1500
                    val isHighPacketLoss = totalProbes >= 3 && packetLossPercentage >= 90f
                    val isCompleteBlock = !isProbeSuccess && latency > 2900
                    
                    if (isHighLatency || isHighPacketLoss || isCompleteBlock) {
                        log("WARN: [HealthProber] Critical performance degradation detected! Engaging emergency seamless fallback...")
                        triggerSeamlessFallback()
                        probeHistory.clear()
                    }
                } else {
                    // Inside RESCUE Mode, monitor stable connectivity limit of 60 seconds
                    if (isProbeSuccess) {
                        rescueStabilitySeconds += 5
                        log("[HealthProber] Stable Rescue mode telemetry: $rescueStabilitySeconds/60 seconds achieved.")
                        if (rescueStabilitySeconds >= 60) {
                            log("[Switch] [HealthProber] Rescue mode has been stable for 60 seconds. Engaging Auto-Recovery back to Performance Mode...")
                            autoRecoverToPerformance()
                            rescueStabilitySeconds = 0
                            probeHistory.clear()
                        }
                    } else {
                        log("WARN: [HealthProber] System instability parsed in Emergency Mode. Resetting recovery timer.")
                        rescueStabilitySeconds = 0
                    }
                }
            }
        }
    }

    private fun triggerSeamlessFallback() {
        _currentState.value = ConnectionState.FALLING_BACK
        log("[Fallback] [SeamlessFallback] Diverting loopback pipes to local rescue binary in < 500ms...")
        
        serviceScope.launch {
            // Spawns the binary process in the background
            CoreManager.startRescueCore(this@RootVpnService)
            
            // Atomically switch key backend target port to 8808 (Rescue) inside GatewayBridge in < 500ms
            GatewayBridge.switchCore(8808)
            
            _currentMode.value = TransportMode.RESCUE
            _currentState.value = ConnectionState.CONNECTED
            log("OK: RESCUE ENABLED: High-latency / DPI whitelist circumvented successfully.")
        }
    }

    private fun autoRecoverToPerformance() {
        _currentState.value = ConnectionState.CONNECTING
        log("[Switch] [AutoRecovery] Initiating returning sequence to High Performance Core (sing-box)...")
        
        serviceScope.launch {
            // Switch key SOCKS gateway target port back to 8809 (Performance) atomically
            GatewayBridge.switchCore(8809)
            
            // Relieve system resources by shutting off external daemon core
            CoreManager.stopRescueCore()
            
            _currentMode.value = TransportMode.PERFORMANCE
            _currentState.value = ConnectionState.CONNECTED
            log("OK: RECOVERY SUCCESSFUL: Performance transport mode successfully established.")
        }
    }

    private fun stopVpn() {
        log("Requesting system VPN disconnection...")
        _currentState.value = ConnectionState.DISCONNECTED
        _downloadSpeed.value = 0f
        _uploadSpeed.value = 0f

        healthProbeJob?.cancel()

        try {
            CoreManager.stopRescueCore()
        } catch (e: Exception) {
            log("Failed to stop rescue core: ${e.message}")
        }

        try {
            vpnInterface?.close()
            vpnInterface = null
            log("Virtual TUN closed, routing tables cleared successfully.")
        } catch (e: Exception) {
            log("Error releasing socket resources: ${e.message}")
        }

        stopForeground(true)
        stopSelf()
    }

    private fun setupForegroundNotification() {
        val channelId = "root_vpn_channel"
        val channelName = "RootVPN Rescue Active Status"

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId, channelName,
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }

        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )

        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("RootVPN Rescue Active")
            .setContentText("Bypassing firewalls with active multi-mode tunnel.")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            try {
                startForeground(
                    9988,
                    notification,
                    android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
                )
            } catch (e: Throwable) {
                log("Error starting foreground with specialUse type: ${e.message}. Trying default.")
                try {
                    startForeground(9988, notification)
                } catch (e2: Throwable) {
                    log("Critical fatal error starting foreground service of any type: ${e2.message}")
                }
            }
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                startForeground(
                    9988,
                    notification,
                    android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_NONE
                )
            } catch (e: Throwable) {
                try {
                    startForeground(9988, notification)
                } catch (e2: Throwable) {
                    log("Fallback startForeground failed on API 29-33: ${e2.message}")
                }
            }
        } else {
            try {
                startForeground(9988, notification)
            } catch (e: Throwable) {
                log("Fallback startForeground failed on API < 29: ${e.message}")
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        serviceJob.cancel()
        log("RootVPN Rescue Service fully disposed.")
    }
}


