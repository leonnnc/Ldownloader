package net.monitor.descargador

import android.content.Context

/**
 * Configuración del widget.
 *
 * Se guarda en SharedPreferences privadas de la app. Están protegidas por el
 * sandbox de Android (ninguna otra app puede leerlas en un dispositivo sin
 * root), pero NO van cifradas: en un móvil con root o comprometido, el token
 * sería legible. Para eso está el consejo de usar un token propio y revocable.
 */
object Prefs {

    private const val FILE = "monitor_prefs"
    private const val KEY_URL = "base_url"
    private const val KEY_TOKEN = "admin_token"
    private const val KEY_NOTICE = "last_notice"
    private const val KEY_NOTICE_AT = "last_notice_at"

    /** Cuánto tiempo se muestra el resultado de una acción en el widget. */
    const val NOTICE_TTL_MS = 90_000L

    private fun sp(ctx: Context) =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun baseUrl(ctx: Context): String =
        (sp(ctx).getString(KEY_URL, "") ?: "").trim().trimEnd('/')

    fun token(ctx: Context): String =
        (sp(ctx).getString(KEY_TOKEN, "") ?: "").trim()

    fun save(ctx: Context, url: String, token: String) {
        sp(ctx).edit()
            .putString(KEY_URL, url.trim().trimEnd('/'))
            .putString(KEY_TOKEN, token.trim())
            .apply()
    }

    fun isConfigured(ctx: Context): Boolean = baseUrl(ctx).isNotBlank()

    fun hasToken(ctx: Context): Boolean = token(ctx).isNotBlank()

    /** Mensaje temporal (resultado de reiniciar, cerrar circuitos…). */
    fun setNotice(ctx: Context, text: String?) {
        sp(ctx).edit()
            .putString(KEY_NOTICE, text)
            .putLong(KEY_NOTICE_AT, System.currentTimeMillis())
            .apply()
    }

    fun notice(ctx: Context): String? {
        val text = sp(ctx).getString(KEY_NOTICE, null) ?: return null
        val at = sp(ctx).getLong(KEY_NOTICE_AT, 0L)
        if (System.currentTimeMillis() - at > NOTICE_TTL_MS) return null
        return text
    }
}
