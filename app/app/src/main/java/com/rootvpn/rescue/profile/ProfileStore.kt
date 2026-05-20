package com.rootvpn.rescue.profile

import android.content.Context

object ProfileStore {
    private const val PREFS = "rootvpn_profiles"
    private const val KEY_LAST_PROFILE = "last_profile_raw"

    fun saveLast(context: Context, profile: ImportResult) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_LAST_PROFILE, profile.raw)
            .apply()
    }

    fun loadLast(context: Context): ImportResult? {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_LAST_PROFILE, null)
            ?.takeIf { it.isNotBlank() }
            ?: return null
        return ProfileParser.parse(raw)
    }

    fun clearLast(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_LAST_PROFILE)
            .apply()
    }
}
