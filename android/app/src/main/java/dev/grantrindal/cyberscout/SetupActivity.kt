package dev.grantrindal.cyberscout

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.activity.ComponentActivity
import java.util.concurrent.Executors

/** Where the app keeps the server address (nothing else: no accounts, no analytics). */
object Prefs {
    private const val FILE = "cyberscout"
    private const val KEY_SERVER = "server"
    fun server(ctx: Context): String? = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_SERVER, null)
    fun setServer(ctx: Context, base: String?) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY_SERVER, base).apply()
    }
}

/** First run (and "Change server"): enter the server address, Test connection, Open. */
class SetupActivity : ComponentActivity() {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var address: EditText
    private lateinit var result: TextView
    private lateinit var note: TextView
    private lateinit var testBtn: Button
    private lateinit var openBtn: Button
    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)
        Insets.padForSystemBars(findViewById(R.id.setup_root))
        address = findViewById(R.id.address)
        result = findViewById(R.id.result)
        note = findViewById(R.id.note)
        testBtn = findViewById(R.id.test)
        openBtn = findViewById(R.id.open)
        if (savedInstanceState == null) address.setText(Prefs.server(this) ?: "")
        testBtn.setOnClickListener { test() }
        openBtn.setOnClickListener { open() }
        address.setOnEditorActionListener { _, id, ev ->
            if (id == EditorInfo.IME_ACTION_GO || (ev?.keyCode == KeyEvent.KEYCODE_ENTER && ev.action == KeyEvent.ACTION_DOWN)) {
                test(); true
            } else false
        }
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    private fun setBusy(b: Boolean) {
        busy = b
        testBtn.isEnabled = !b
        openBtn.isEnabled = !b
    }

    private fun showNote(base: String?) {
        if (base != null && !ServerAddress.isHttps(base)) {
            note.text = getString(R.string.note_http)
            note.visibility = View.VISIBLE
        } else note.visibility = View.GONE
    }

    private fun test() {
        if (busy) return
        val input = address.text.toString()
        setBusy(true)
        result.setTextColor(getColor(R.color.ink_2))
        result.text = getString(R.string.testing)
        io.execute {
            val r = ConnectionTest.run(input)
            main.post {
                if (isDestroyed) return@post
                setBusy(false)
                render(r)
            }
        }
    }

    private fun render(r: ConnectionTest.Result) {
        when (r) {
            is ConnectionTest.Result.Ok -> {
                address.setText(r.server)
                result.setTextColor(getColor(R.color.ok))
                result.text = getString(R.string.test_ok, r.info.name ?: "Cyber-Scout", r.info.version ?: "?", r.latencyMs)
                showNote(r.server)
            }
            is ConnectionTest.Result.Failed -> {
                result.setTextColor(getColor(R.color.bad))
                result.text = getString(R.string.test_failed, r.message)
                showNote(r.server)
            }
        }
    }

    private fun open() {
        if (busy) return
        val base = ServerAddress.normalize(address.text.toString())
        if (base == null) {
            result.setTextColor(getColor(R.color.bad))
            result.text = getString(R.string.test_failed, getString(R.string.not_an_address))
            return
        }
        setBusy(true)
        io.execute {
            val refusal = ConnectionTest.cleartextRefusal(base)      // may look up the name: off the main thread
            main.post {
                if (isDestroyed) return@post
                setBusy(false)
                if (refusal != null) {
                    result.setTextColor(getColor(R.color.bad))
                    result.text = getString(R.string.test_failed, refusal)
                    return@post
                }
                Prefs.setServer(this, base)
                startActivity(Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                finish()
            }
        }
    }
}
