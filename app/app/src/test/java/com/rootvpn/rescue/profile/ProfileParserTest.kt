package com.rootvpn.rescue.profile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ProfileParserTest {
    @Test
    fun parsesOlcrtcRescueUri() {
        val profile = ProfileParser.parse(
            "olcrtc://wbstream?vp8channel<vp8-fps=60&vp8-batch=64>@019e447c-8b21-7e5a-862a-6b3c3024122a#abcdef%tg_930736233\$RootVPN Rescue Beta"
        )

        assertEquals(ProfileKind.OLCRTC, profile.kind)
        assertEquals("wbstream", profile.channel)
        assertEquals("vp8channel", profile.transport)
        assertEquals("019e447c-8b21-7e5a-862a-6b3c3024122a", profile.roomId)
        assertEquals("abcdef", profile.token)
        assertEquals("tg_930736233", profile.clientId)
        assertEquals("60", profile.fps)
        assertEquals("64", profile.batch)
        assertEquals("RootVPN Rescue Beta", profile.label)
    }

    @Test
    fun parsesVlessUri() {
        val profile = ProfileParser.parse(
            "vless://uuid@example.com:443?security=reality&type=tcp#RootVPN FI"
        )

        assertEquals(ProfileKind.VLESS, profile.kind)
        assertEquals("example.com", profile.host)
        assertEquals("example.com:443", profile.server)
        assertEquals("RootVPN FI", profile.label)
        assertNull(profile.roomId)
    }
}
