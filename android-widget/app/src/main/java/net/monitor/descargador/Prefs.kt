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
    private const val KEY_OFFLINE = "offline"
    private const val KEY_STREAK = "failure_streak"

    /** Fallos seguidos antes de dar la conexión por perdida. */
    const val OFFLINE_AFTER_FAILURES = 2

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

    /**
     * Estado de la conexión con el servidor.
     *
     * No se marca como perdida al primer fallo: un corte de red de un segundo
     * no debe cambiar el widget a rojo. A partir de [OFFLINE_AFTER_FAILURES]
     * fallos seguidos sí, y entonces el widget enseña el botón de reconectar.
     */
    fun isOffline(ctx: Context): Boolean = sp(ctx).getBoolean(KEY_OFFLINE, false)

    fun setOffline(ctx: Context, value: Boolean) {
        sp(ctx).edit().putBoolean(KEY_OFFLINE, value).apply()
    }

    fun failureStreak(ctx: Context): Int = sp(ctx).getInt(KEY_STREAK, 0)

    /** Suma un fallo y devuelve la racha resultante. */
    fun registerFailure(ctx: Context): Int {
        val streak = failureStreak(ctx) + 1
        sp(ctx).edit().putInt(KEY_STREAK, streak).apply()
        return streak
    }

    fun clearFailures(ctx: Context) {
        sp(ctx).edit().putInt(KEY_STREAK, 0).putBoolean(KEY_OFFLINE, false).apply()
    }

    fun notice(ctx: Context): String? {
        val text = sp(ctx).getString(KEY_NOTICE, null) ?: return null
        val at = sp(ctx).getLong(KEY_NOTICE_AT, 0L)
        if (System.currentTimeMillis() - at > NOTICE_TTL_MS) return null
        return text
    }
}
