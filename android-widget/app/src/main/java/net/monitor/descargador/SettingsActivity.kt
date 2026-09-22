package net.monitor.descargador

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

/** Pantalla mínima para configurar la dirección del servidor y el token. */
class SettingsActivity : Activity() {

    companion object {
        /** Esquema del enlace de conexión que publica el monitor web. */
        private const val PAIRING_SCHEME = "vdl"
        private const val PAIRING_HOST = "pair"
    }

    private lateinit var inputUrl: EditText
    private lateinit var inputToken: EditText
    private lateinit var result: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        inputUrl = findViewById(R.id.input_url)
        inputToken = findViewById(R.id.input_token)
        result = findViewById(R.id.result)

        inputUrl.setText(Prefs.baseUrl(this))
        inputToken.setText(Prefs.token(this))

        // Al abrir la app desde un enlace de conexión, los datos llegan ya
        // rellenos: no hay que copiar nada a mano.
        handlePairingLink(intent?.data)

        findViewById<Button>(R.id.btn_save).setOnClickListener {
            val url = inputUrl.text.toString().trim()
            if (url.isBlank()) {
                result.text = getString(R.string.settings_need_url)
                return@setOnClickListener
            }

            Prefs.save(this, url, inputToken.text.toString())
            result.text = getString(R.string.settings_saved)
            applyConfiguration()
        }

        findViewById<Button>(R.id.btn_test).setOnClickListener {
            val url = inputUrl.text.toString().trim().trimEnd('/')
            if (url.isBlank()) {
                result.text = getString(R.string.settings_need_url)
                return@setOnClickListener
            }

            result.text = getString(R.string.settings_testing)

            // La red nunca en el hilo de interfaz.
            Thread {
                val outcome = StatusRepository.request(url, "/api/widget")
                runOnUiThread {
                    result.text = when (outcome) {
                        is FetchResult.Ok ->
                            getString(
                                R.string.settings_test_ok,
                                outcome.snapshot.statusLabel,
                                outcome.snapshot.successRate
                            )
                        is FetchResult.Failed ->
                            getString(R.string.settings_test_fail, outcome.message)
                    }
                }
            }.start()
        }
    }

    /**
     * La app ya estaba abierta y llega un enlace nuevo.
     *
     * Ocurre al tocar «Configurar la app» en el monitor web con la app en
     * segundo plano: sin esto, el enlace se abriría en una pantalla nueva y
     * esta se quedaría con los datos viejos.
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (handlePairingLink(intent.data)) {
            result.text = getString(R.string.settings_paired)
        }
    }

    /**
     * Aplica un enlace `vdl://pair?url=…&token=…`.
     *
     * Devuelve true si el enlace era válido y se aplicó. Si no lo es, no se
     * toca la configuración que ya hubiera: un enlace roto no debe poder
     * desconfigurar un widget que funcionaba.
     */
    private fun handlePairingLink(data: Uri?): Boolean {
        if (data == null) return false
        if (!data.scheme.equals(PAIRING_SCHEME, ignoreCase = true)) return false
        if (!data.host.equals(PAIRING_HOST, ignoreCase = true)) return false

        val url = data.getQueryParameter("url")?.trim()?.trimEnd('/').orEmpty()
        if (url.isBlank()) return false

        val token = data.getQueryParameter("token").orEmpty().trim()

        inputUrl.setText(url)
        inputToken.setText(token)
        Prefs.save(this, url, token)
        applyConfiguration()

        return true
    }

    /** Deja la configuración en vigor sin esperar al próximo ciclo. */
    private fun applyConfiguration() {
        WidgetProvider.updateAll(this)
        MonitorWorker.enqueueRefresh(this)
        MonitorWorker.schedulePeriodic(this)
    }
}
