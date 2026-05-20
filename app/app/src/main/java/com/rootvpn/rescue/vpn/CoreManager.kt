package com.rootvpn.rescue.vpn

import android.content.Context
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

object CoreManager {
    private const val RESCUE_BINARY_NAME = "olcrtc"
    private var process: Process? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var stdoutJob: Job? = null

    @Synchronized
    fun initialize(context: Context) {
        val targetFile = File(context.filesDir, RESCUE_BINARY_NAME)
        if (targetFile.exists() && targetFile.canExecute()) {
            RootVpnService.log("Rescue core found at ${targetFile.absolutePath}")
            return
        }
        RootVpnService.log("Rescue core is not bundled yet. Add a real Android olcRTC binary/library before enabling Rescue routing.")
    }

    @Synchronized
    fun startRescueCore(context: Context) {
        if (process != null) {
            RootVpnService.log("Rescue core start ignored: process is already running.")
            return
        }

        val targetFile = File(context.filesDir, RESCUE_BINARY_NAME)
        if (!targetFile.exists() || !targetFile.canExecute()) {
            RootVpnService.log("Rescue core cannot start: executable ${targetFile.absolutePath} is missing.")
            return
        }

        try {
            val processBuilder = ProcessBuilder(targetFile.absolutePath)
                .directory(context.filesDir)
                .redirectErrorStream(true)
            val started = processBuilder.start()
            process = started
            stdoutJob?.cancel()
            stdoutJob = scope.launch {
                try {
                    val reader = BufferedReader(InputStreamReader(started.inputStream))
                    while (isActive) {
                        val line = reader.readLine() ?: break
                        RootVpnService.log("[olcrtc] $line")
                    }
                } catch (e: Throwable) {
                    RootVpnService.log("Rescue core log reader stopped: ${e.message}")
                }
            }
            RootVpnService.log("Rescue core process started.")
        } catch (e: Throwable) {
            RootVpnService.log("Failed to start Rescue core: ${e.message}")
        }
    }

    @Synchronized
    fun stopRescueCore() {
        stdoutJob?.cancel()
        stdoutJob = null
        process?.let { started ->
            try {
                started.destroy()
            } catch (e: Throwable) {
                RootVpnService.log("Error stopping Rescue core: ${e.message}")
            }
            process = null
            RootVpnService.log("Rescue core process stopped.")
        }
    }
}
