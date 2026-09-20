package net.monitor.descargador

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/** Lo que el widget necesita pintar. Sin lógica: solo cadenas y números. */
data class Snapshot(
    val status: String = "desconocido",
    val statusLabel: String = "",
    val successRate: String = "—",
    val circuits: Int = 0,
    val jobs: Int = 0,
    val engine: String = "—",
    val problem: String? = null,
    val restartAdvised: Boolean = false,
    val restartReason: String? = null,
    val lastCheck: String = "—",
    val fetchedAt: Long = 0L,
)

/** Resultado de una operación de red. */
sealed class FetchResult {
    data class Ok(val snapshot: Snapshot) : FetchResult()
    data class Failed(val message: String) : FetchResult()
}

object StatusRepository {

    private const val CACHE = "monitor_cache"
    private const val TIMEOUT_MS = 12_000

    // ---------------------------------------------------------------------
    // Red
    // ---------------------------------------------------------------------

    /**
     * Consulta el servidor.
     *
     * `path` se resuelve contra la URL base configurada. Si se pasa `token`,
     * se envía como cabecera X-Admin-Token (necesario para las acciones de
     * administración).
     */
    fun request(
        url: String,
        path: String,
        token: String? = null,
        method: String = "GET",
    ): FetchResult {
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(url + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = TIMEOUT_MS
                readTimeout = TIMEOUT_MS
                setRequestProperty("Accept", "application/json")
                if (!token.isNullOrBlank()) {
                    setRequestProperty("X-Admin-Token", token)
                }
                if (method == "POST") {
                    doOutput = true
                    // POST sin cuerpo: el backend lee el token de la cabecera.
                    setFixedLengthStreamingMode(0)
                }
            }

            val code = connection.responseCode
            val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()
                ?.use(BufferedReader::readText)
                .orEmpty()

            if (code !in 200..299) {
                return FetchResult.Failed("HTTP $code — ${extractDetail(body)}")
            }

            FetchResult.Ok(parseSnapshot(body))
        } catch (e: Exception) {
            FetchResult.Failed(e.message ?: e.javaClass.simpleName)
        } finally {
            connection?.disconnect()
        }
    }

    /** El backend devuelve {"detail": "..."} en los errores. */
    private fun extractDetail(body: String): String =
        try {
            JSONObject(body).optString("detail").takeIf { it.isNotBlank() } ?: body.take(120)
        } catch (e: Exception) {
            body.take(120).ifBlank { "sin detalle" }
        }

    private fun parseSnapshot(body: String): Snapshot {
        val json = JSONObject(body)

        return Snapshot(
            status = json.optString("status", "desconocido"),
            statusLabel = json.optString("status_label", ""),
            successRate = json.optString("success_rate", "—"),
            circuits = json.optInt("circuits", 0),
            jobs = json.optInt("jobs", 0),
            engine = json.optString("engine", "—"),
            // org.json devuelve la cadena "null" para valores nulos: hay que
            // comprobarlo explícitamente o el widget mostraría "null".
            problem = json.stringOrNull("problem"),
            restartAdvised = json.optBoolean("restart_advised", false),
            restartReason = json.stringOrNull("restart_reason"),
            lastCheck = json.optString("last_check", "—"),
            fetchedAt = System.currentTimeMillis(),
        )
    }

    private fun JSONObject.stringOrNull(key: String): String? {
        if (!has(key) || isNull(key)) return null
        val value = optString(key, "").trim()
        return value.ifBlank { null }
    }

    // ---------------------------------------------------------------------
    // Caché local
    //
    // El widget SIEMPRE debe poder pintar algo. Si no hay red, muestra el
    // último estado conocido junto con la hora, en lugar de quedarse en
    // blanco — pero dejando claro que el dato es viejo. Un widget vacío o
    // que finge estar al día es peor que uno que dice "no lo sé".
    // ---------------------------------------------------------------------

    fun save(ctx: Context, snapshot: Snapshot) {
        ctx.getSharedPreferences(CACHE, Context.MODE_PRIVATE).edit()
            .putString("status", snapshot.status)
            .putString("statusLabel", snapshot.statusLabel)
            .putString("successRate", snapshot.successRate)
            .putInt("circuits", snapshot.circuits)
            .putInt("jobs", snapshot.jobs)
            .putString("engine", snapshot.engine)
            .putString("problem", snapshot.problem)
            .putBoolean("restartAdvised", snapshot.restartAdvised)
            .putString("restartReason", snapshot.restartReason)
            .putString("lastCheck", snapshot.lastCheck)
            .putLong("fetchedAt", snapshot.fetchedAt)
            .apply()
    }

    fun cached(ctx: Context): Snapshot? {
        val sp = ctx.getSharedPreferences(CACHE, Context.MODE_PRIVATE)
        val fetchedAt = sp.getLong("fetchedAt", 0L)
        if (fetchedAt == 0L) return null

        return Snapshot(
            status = sp.getString("status", "desconocido") ?: "desconocido",
            statusLabel = sp.getString("statusLabel", "") ?: "",
            successRate = sp.getString("successRate", "—") ?: "—",
            circuits = sp.getInt("circuits", 0),
            jobs = sp.getInt("jobs", 0),
            engine = sp.getString("engine", "—") ?: "—",
            problem = sp.getString("problem", null),
            restartAdvised = sp.getBoolean("restartAdvised", false),
            restartReason = sp.getString("restartReason", null),
            lastCheck = sp.getString("lastCheck", "—") ?: "—",
            fetchedAt = fetchedAt,
        )
    }
}
