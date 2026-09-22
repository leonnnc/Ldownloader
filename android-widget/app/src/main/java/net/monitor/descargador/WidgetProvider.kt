package net.monitor.descargador

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.View
import android.widget.RemoteViews
import java.util.concurrent.TimeUnit

/**
 * Dibuja el widget en la pantalla de inicio.
 *
 * No hace red: pinta lo que haya en la caché y, en paralelo, pide un refresco.
 * Así el widget aparece al instante con el último dato y se actualiza solo
 * cuando llega el nuevo.
 */
class WidgetProvider : AppWidgetProvider() {

    companion object {
        const val ACTION_WIDGET_REFRESH = "net.monitor.descargador.ACTION_WIDGET_REFRESH"

        /**
         * A partir de aquí, el dato se considera viejo.
         *
         * Es la regla más importante de todo el widget: **no saber nunca debe
         * parecer "todo bien"**. Si el último dato tiene más de 45 minutos, se
         * pinta el punto en gris y se dice cuándo se supo por última vez, en
         * lugar de mostrar un verde tranquilizador y falso.
         */
        private val STALE_AFTER_MS = TimeUnit.MINUTES.toMillis(45)

        fun updateAll(ctx: Context) {
            val manager = AppWidgetManager.getInstance(ctx)
            val ids = manager.getAppWidgetIds(ComponentName(ctx, WidgetProvider::class.java))
            ids.forEach { render(ctx, manager, it) }
        }

        fun render(ctx: Context, manager: AppWidgetManager, widgetId: Int) {
            val views = RemoteViews(ctx.packageName, R.layout.widget_status)

            if (!Prefs.isConfigured(ctx)) {
                views.setImageViewResource(R.id.status_dot, R.drawable.dot_unknown)
                views.setTextViewText(
                    R.id.status_label,
                    ctx.getString(R.string.widget_not_configured)
                )
                views.setTextViewText(R.id.updated, "")
                views.setTextViewText(R.id.metric_rate, "—")
                views.setTextViewText(R.id.metric_circuits, "—")
                views.setTextViewText(R.id.metric_engine, "—")
                views.setViewVisibility(R.id.problem, View.GONE)
                views.setViewVisibility(R.id.advice, View.GONE)
                bindClicks(ctx, views)
                manager.updateAppWidget(widgetId, views)
                return
            }

            val snapshot = StatusRepository.cached(ctx)

            if (snapshot == null) {
                // Configurado pero sin ningún dato todavía: es el caso de «el
                // enlace o el token no son los correctos», así que se ofrece
                // reconectar desde el primer momento.
                views.setImageViewResource(R.id.status_dot, R.drawable.dot_unknown)
                views.setTextViewText(R.id.status_label, ctx.getString(R.string.widget_offline))
                views.setTextViewText(R.id.updated, "")
                views.setTextViewText(R.id.metric_rate, "—")
                views.setTextViewText(R.id.metric_circuits, "—")
                views.setTextViewText(R.id.metric_engine, "—")
                views.setViewVisibility(R.id.problem, View.GONE)
                views.setViewVisibility(R.id.advice, View.GONE)
                bindClicks(ctx, views, reconnect = true)
                manager.updateAppWidget(widgetId, views)
                return
            }

            val offline = Prefs.isOffline(ctx)
            val ageMs = System.currentTimeMillis() - snapshot.fetchedAt
            val stale = ageMs > STALE_AFTER_MS

            val dot = if (offline) {
                // Sin conexión no se sabe nada: gris, nunca verde ni rojo.
                // El rojo diría que el servicio está caído, y no es el caso:
                // lo que falla es el enlace hasta el servicio.
                R.drawable.dot_unknown
            } else if (stale) {
                R.drawable.dot_unknown
            } else {
                when (snapshot.status) {
                    "ok" -> R.drawable.dot_ok
                    "degradado" -> R.drawable.dot_warn
                    "caido" -> R.drawable.dot_down
                    else -> R.drawable.dot_unknown
                }
            }

            val label = when {
                offline -> ctx.getString(R.string.widget_offline)
                stale && snapshot.statusLabel.isNotBlank() ->
                    "${snapshot.statusLabel} · ${ctx.getString(R.string.widget_stale, humanAge(ageMs))}"
                stale -> ctx.getString(R.string.widget_stale, humanAge(ageMs))
                snapshot.statusLabel.isNotBlank() -> snapshot.statusLabel
                else -> ctx.getString(R.string.widget_unknown)
            }

            views.setImageViewResource(R.id.status_dot, dot)
            views.setTextViewText(R.id.status_label, label)
            views.setTextViewText(R.id.updated, humanAge(ageMs))

            views.setTextViewText(
                R.id.metric_rate,
                ctx.getString(R.string.widget_metric_rate, snapshot.successRate)
            )
            views.setTextViewText(
                R.id.metric_circuits,
                ctx.getString(R.string.widget_metric_circuits, snapshot.circuits)
            )
            views.setTextViewText(
                R.id.metric_engine,
                ctx.getString(R.string.widget_metric_engine, snapshot.engine)
            )

            if (snapshot.problem.isNullOrBlank()) {
                views.setViewVisibility(R.id.problem, View.GONE)
            } else {
                views.setViewVisibility(R.id.problem, View.VISIBLE)
                views.setTextViewText(R.id.problem, snapshot.problem)
            }

            // El aviso muestra primero el resultado de una acción reciente
            // (que es lo que el usuario acaba de pedir) y, si no hay, la
            // recomendación de reiniciar.
            val notice = Prefs.notice(ctx)
            val advice = when {
                notice != null -> notice
                snapshot.restartAdvised -> snapshot.restartReason
                    ?.let { ctx.getString(R.string.widget_restart_advice, it) }
                    ?: ctx.getString(R.string.widget_restart_advice_short)
                else -> null
            }

            if (advice.isNullOrBlank()) {
                views.setViewVisibility(R.id.advice, View.GONE)
            } else {
                views.setViewVisibility(R.id.advice, View.VISIBLE)
                views.setTextViewText(R.id.advice, advice)
            }

            bindClicks(ctx, views, reconnect = offline)
            manager.updateAppWidget(widgetId, views)
        }

        /**
         * Conecta los botones y decide cuáles tienen sentido ahora.
         *
         * `reconnect = true` enseña «Reconectar» y esconde «Circuitos» y
         * «Reiniciar»: sin servidor, esas dos órdenes no pueden llegar a
         * ninguna parte, y ofrecerlas solo confunde.
         */
        private fun bindClicks(ctx: Context, views: RemoteViews, reconnect: Boolean = false) {
            views.setViewVisibility(
                R.id.btn_reconnect,
                if (reconnect) View.VISIBLE else View.GONE
            )
            val adminVisibility = if (reconnect) View.GONE else View.VISIBLE
            views.setViewVisibility(R.id.btn_reset, adminVisibility)
            views.setViewVisibility(R.id.btn_restart, adminVisibility)

            views.setOnClickPendingIntent(
                R.id.widget_root,
                pending(ctx, ActionReceiver.ACTION_OPEN)
            )
            views.setOnClickPendingIntent(
                R.id.btn_refresh,
                pending(ctx, ActionReceiver.ACTION_REFRESH)
            )
            views.setOnClickPendingIntent(
                R.id.btn_reconnect,
                pending(ctx, ActionReceiver.ACTION_RECONNECT)
            )
            views.setOnClickPendingIntent(
                R.id.btn_reset,
                pending(ctx, ActionReceiver.ACTION_RESET_CIRCUITS)
            )
            views.setOnClickPendingIntent(
                R.id.btn_restart,
                pending(ctx, ActionReceiver.ACTION_RESTART)
            )
        }

        /**
         * PendingIntent para un botón.
         *
         * El `data` distinto es imprescindible: sin él, Android considera los
         * cuatro PendingIntent iguales y todos los botones acaban disparando
         * la misma acción (la última registrada).
         */
        private fun pending(ctx: Context, action: String): PendingIntent {
            val intent = Intent(ctx, ActionReceiver::class.java).apply {
                this.action = action
                data = Uri.parse("monitor://action/$action")
            }
            return PendingIntent.getBroadcast(
                ctx,
                action.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }

        /** "ahora", "5m", "2h", "3d". */
        private fun humanAge(ms: Long): String {
            val minutes = TimeUnit.MILLISECONDS.toMinutes(ms)
            return when {
                minutes < 1 -> "ahora"
                minutes < 60 -> "${minutes}m"
                minutes < 1440 -> "${minutes / 60}h"
                else -> "${minutes / 1440}d"
            }
        }
    }

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        // Primero se pinta lo que hay en caché (instantáneo)…
        appWidgetIds.forEach { render(context, appWidgetManager, it) }
        // …y después se pide el dato nuevo en segundo plano.
        MonitorWorker.enqueueRefresh(context)
        MonitorWorker.schedulePeriodic(context)
    }

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        // Primer widget colocado: arranca el ciclo periódico.
        MonitorWorker.schedulePeriodic(context)
        MonitorWorker.enqueueRefresh(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == ACTION_WIDGET_REFRESH) {
            MonitorWorker.enqueueRefresh(context)
        }
    }
}
