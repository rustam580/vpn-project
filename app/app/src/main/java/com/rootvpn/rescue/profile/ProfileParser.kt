package com.rootvpn.rescue.profile

import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

enum class ProfileKind {
    VLESS,
    OLCRTC,
    UNKNOWN
}

data class ImportResult(
    val scheme: String,
    val host: String,
    val channel: String? = null,
    val token: String? = null,
    val fps: String? = null,
    val batch: String? = null,
    val raw: String,
    val kind: ProfileKind = ProfileKind.UNKNOWN,
    val title: String = "Unknown profile",
    val transport: String? = null,
    val roomId: String? = null,
    val clientId: String? = null,
    val label: String? = null,
    val server: String? = null,
)

object ProfileParser {
    fun parse(raw: String): ImportResult {
        val value = raw.trim()
        if (value.startsWith("olcrtc://", ignoreCase = true)) {
            return parseOlcRtc(value)
        }
        if (value.startsWith("vless://", ignoreCase = true)) {
            return parseVless(value)
        }
        return ImportResult(
            scheme = value.substringBefore(":", ""),
            host = "",
            raw = value,
        )
    }

    private fun parseVless(raw: String): ImportResult {
        return try {
            val uri = URI(raw)
            val label = uri.fragment?.takeIf { it.isNotBlank() }?.let(::decode)
            val server = uri.host.orEmpty()
            val port = if (uri.port > 0) ":${uri.port}" else ""
            ImportResult(
                scheme = "vless",
                host = server,
                raw = raw,
                kind = ProfileKind.VLESS,
                title = label ?: "VLESS profile",
                label = label,
                server = if (server.isBlank()) null else "$server$port",
            )
        } catch (_: Throwable) {
            ImportResult(scheme = "vless", host = "", raw = raw, kind = ProfileKind.VLESS, title = "VLESS profile")
        }
    }

    private fun parseOlcRtc(raw: String): ImportResult {
        val withoutScheme = raw.removePrefix("olcrtc://")
        val carrier = withoutScheme.substringBefore("?", missingDelimiterValue = "")
        val afterCarrier = withoutScheme.substringAfter("?", missingDelimiterValue = "")
        val transportSpec = afterCarrier.substringBefore("@", missingDelimiterValue = "")
        val roomId = afterCarrier.substringAfter("@", missingDelimiterValue = "").substringBefore("#")
        val secretSpec = afterCarrier.substringAfter("#", missingDelimiterValue = "")

        val transport = transportSpec.substringBefore("<").ifBlank { null }
        val paramsText = transportSpec.substringAfter("<", missingDelimiterValue = "").substringBefore(">")
        val params = paramsText
            .split("&")
            .mapNotNull { item ->
                val key = item.substringBefore("=", missingDelimiterValue = "").trim()
                val value = item.substringAfter("=", missingDelimiterValue = "").trim()
                if (key.isBlank()) null else key to value
            }
            .toMap()

        val secretAndClient = secretSpec.substringBefore("\$", missingDelimiterValue = secretSpec)
        val label = secretSpec.substringAfter("\$", missingDelimiterValue = "").ifBlank { null }?.let(::decode)
        val key = secretAndClient.substringBefore("%", missingDelimiterValue = secretAndClient).ifBlank { null }
        val clientId = secretAndClient.substringAfter("%", missingDelimiterValue = "").ifBlank { null }

        return ImportResult(
            scheme = "olcrtc",
            host = carrier,
            channel = carrier.ifBlank { null },
            token = key,
            fps = params["vp8-fps"],
            batch = params["vp8-batch"],
            raw = raw,
            kind = ProfileKind.OLCRTC,
            title = label ?: "RootVPN Rescue",
            transport = transport,
            roomId = roomId.ifBlank { null },
            clientId = clientId,
            label = label,
        )
    }

    private fun decode(value: String): String = runCatching {
        URLDecoder.decode(value, StandardCharsets.UTF_8.name())
    }.getOrDefault(value)
}
