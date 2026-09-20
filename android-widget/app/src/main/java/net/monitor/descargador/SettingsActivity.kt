package net.monitor.descargador

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

/** Pantalla mínima para configurar la dirección del servidor y el token. */
class SettingsActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val inputUrl = findViewById<EditText>(R.id.input_url)
        val inputToken = findViewById<EditText>(R.id.input_token)
        val result = findViewById<TextView>(R.id.result)

        inputUrl.setText(Prefs.baseUrl(this))
        inputToken.setText(Prefs.token(this))

        findViewById<Button>(R.id.btn_save).setOnClickListener {
            val url = inputUrl.text.toString().trim()
            if (url.isBlank()) {
                result.text = getString(R.string.settings_need_url)
                return@setOnClickListener
            }

            Prefs.save(this, url, inputToken.text.toString())
            result.text = getString(R.string.settings_saved)

            // Aplicar de inmediato: redibujar y pedir el primer dato.
            WidgetProvider.updateAll(this)
            MonitorWorker.enqueueRefresh(this)
            MonitorWorker.schedulePeriodic(this)
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
}
