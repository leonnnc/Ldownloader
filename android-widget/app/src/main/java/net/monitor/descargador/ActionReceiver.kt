package net.monitor.descargador

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * Recibe los toques de los botones del widget.
 *
 * Los botones de un widget no ejecutan código: solo lanzan un PendingIntent.
 * Este receptor es el que traduce ese toque en trabajo real.
 */
class ActionReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_OPEN = "open"
        const val ACTION_REFRESH = "refresh"
        const val ACTION_RECONNECT = "reconnect"
        const val ACTION_RESTART = "restart"
        const val ACTION_RESET_CIRCUITS = "reset_circuits"
    }

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_OPEN -> openDashboard(context)
            ACTION_REFRESH -> MonitorWorker.enqueueRefresh(context)
            ACTION_RECONNECT -> reconnect(context)
            ACTION_RESTART -> MonitorWorker.enqueueAction(context, MonitorWorker.ACTION_RESTART)
            ACTION_RESET_CIRCUITS ->
                MonitorWorker.enqueueAction(context, MonitorWorker.ACTION_RESET_CIRCUITS)
        }
    }

    /**
     * Reintento pedido a mano.
     *
     * Se pinta el acuse antes de pedir el trabajo: si el servidor tarda en
     * responder o no responde, el usuario tiene que ver que su toque hizo algo.
     * Si no, parece que el botón está roto.
     */
    private fun reconnect(context: Context) {
        Prefs.setNotice(context, context.getString(R.string.widget_reconnecting))
        WidgetProvider.updateAll(context)
        MonitorWorker.enqueueRefresh(context)
    }

    /** Abre el panel completo en el navegador. */
    private fun openDashboard(context: Context) {
        if (!Prefs.isConfigured(context)) {
            context.startActivity(
                Intent(context, SettingsActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            return
        }

        try {
            context.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(Prefs.baseUrl(context) + "/monitor"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            // Sin navegador instalado no hay nada que abrir; no es un fallo grave.
        }
    }
}
