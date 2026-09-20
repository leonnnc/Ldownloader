package net.monitor.descargador

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit

/**
 * El único que habla con el servidor.
 *
 * Un widget de Android no puede hacer peticiones de red por sí mismo: solo
 * pinta datos que alguien le haya preparado antes. Este Worker es ese
 * "alguien", y se ejecuta de tres formas:
 *
 *  - Periódicamente cada 15 minutos (el mínimo que Android respeta de verdad;
 *    `updatePeriodMillis` por debajo de 30 min se ignora).
 *  - Al pulsar «Actualizar» en el widget.
 *  - Al pulsar «Reiniciar» o «Circuitos», que además lanzan la orden al servidor.
 */
class MonitorWorker(
    appContext: Context,
    params: WorkerParameters,
) : Worker(appContext, params) {

    companion object {
        const val KEY_ACTION = "action"
        const val ACTION_REFRESH = "refresh"
        const val ACTION_RESTART = "restart"
        const val ACTION_RESET_CIRCUITS = "reset_circuits"

        private const val UNIQUE_REFRESH = "monitor-refresh"
        private const val UNIQUE_ACTION = "monitor-action"
        private const val UNIQUE_PERIODIC = "monitor-periodic"

        private fun networkOnly() = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        /** Refresco inmediato del estado. */
        fun enqueueRefresh(ctx: Context) {
            val request = OneTimeWorkRequestBuilder<MonitorWorker>()
                .setInputData(workDataOf(KEY_ACTION to ACTION_REFRESH))
                .setConstraints(networkOnly())
                .build()
            WorkManager.getInstance(ctx)
                .enqueueUniqueWork(UNIQUE_REFRESH, ExistingWorkPolicy.REPLACE, request)
        }

        /** Acción de administración; el refresco del estado va incluido. */
        fun enqueueAction(ctx: Context, action: String) {
            val request = OneTimeWorkRequestBuilder<MonitorWorker>()
                .setInputData(workDataOf(KEY_ACTION to action))
                .setConstraints(networkOnly())
                .build()
            // REPLACE: si el usuario pulsa dos botones seguidos, interesa el
            // último, no ejecutar los dos.
            WorkManager.getInstance(ctx)
                .enqueueUniqueWork(UNIQUE_ACTION, ExistingWorkPolicy.REPLACE, request)
        }

        /** Refresco periódico. KEEP: no reinicia la cuenta en cada arranque. */
        fun schedulePeriodic(ctx: Context) {
            val request = PeriodicWorkRequestBuilder<MonitorWorker>(15, TimeUnit.MINUTES)
                .setInputData(workDataOf(KEY_ACTION to ACTION_REFRESH))
                .setConstraints(networkOnly())
                .build()
            WorkManager.getInstance(ctx)
                .enqueueUniquePeriodicWork(UNIQUE_PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        private fun successMessage(action: String): String = when (action) {
            ACTION_RESTART -> "Reinicio solicitado"
            ACTION_RESET_CIRCUITS -> "Circuitos reiniciados"
            else -> "Hecho"
        }
    }

    override fun doWork(): Result {
        val ctx = applicationContext

        // Sin configurar no hay nada que hacer; no se reintenta en bucle.
        if (!Prefs.isConfigured(ctx)) return Result.success()

        val action = inputData.getString(KEY_ACTION) ?: ACTION_REFRESH
        val baseUrl = Prefs.baseUrl(ctx)

        // 1. Orden de administración, si la hay.
        if (action != ACTION_REFRESH) {
            val path = when (action) {
                ACTION_RESTART -> "/api/admin/restart"
                ACTION_RESET_CIRCUITS -> "/api/admin/circuits/reset"
                else -> null
            }

            val notice = when {
                path == null -> null
                !Prefs.hasToken(ctx) -> "Falta el token en la app"
                else -> when (
                    val result = StatusRepository.request(baseUrl, path, Prefs.token(ctx), "POST")
                ) {
                    is FetchResult.Ok -> successMessage(action)
                    is FetchResult.Failed -> "Error: ${result.message}"
                }
            }
            Prefs.setNotice(ctx, notice)
        } else {
            // Un refresco normal limpia el mensaje anterior.
            Prefs.setNotice(ctx, null)
        }

        // 2. Estado actualizado.
        val result = StatusRepository.request(baseUrl, "/api/widget")
        if (result is FetchResult.Ok) {
            StatusRepository.save(ctx, result.snapshot)
        }
        // Si falla, se conserva la caché a propósito: el widget mostrará el
        // último dato conocido junto con su antigüedad. Es mejor que un widget
        // en blanco, siempre que deje claro que el dato es viejo.

        // 3. Redibujar con lo que haya.
        WidgetProvider.updateAll(ctx)

        return if (result is FetchResult.Ok) Result.success() else Result.retry()
    }
}
