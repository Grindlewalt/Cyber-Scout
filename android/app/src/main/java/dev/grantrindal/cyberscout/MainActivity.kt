package dev.grantrindal.cyberscout

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ServiceWorkerClient
import android.webkit.ServiceWorkerController
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.io.ByteArrayInputStream
import java.util.concurrent.ConcurrentHashMap

/**
 * The Cyber-Scout web app of the configured server in a WebView: JavaScript, DOM storage (localStorage, IndexedDB)
 * and service workers on, so the web app's own offline layer (save an event for offline, outbox, connection pill)
 * works as in Chrome. Service workers only run on https:// origins, so offline copies need the https address.
 */
class MainActivity : ComponentActivity() {
    companion object {
        const val ACTION_CHANGE_SERVER = "dev.grantrindal.cyberscout.CHANGE_SERVER"
    }

    private lateinit var server: String
    private lateinit var web: WebView
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var progress: ProgressBar
    private lateinit var errorBox: View
    private lateinit var errorText: TextView
    private lateinit var fullscreen: FrameLayout
    private var customView: View? = null
    private var customCallback: WebChromeClient.CustomViewCallback? = null
    private var mainFrameError = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private val policy = CleartextPolicy()

    private val back = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            when {
                customView != null -> customCallback?.onCustomViewHidden()
                web.canGoBack() -> web.goBack()
            }
            updateBack()
        }
    }

    private val pickFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(r.resultCode, r.data))
        fileCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val saved = Prefs.server(this)
        if (saved == null || intent?.action == ACTION_CHANGE_SERVER) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        server = saved
        setContentView(R.layout.activity_main)
        Insets.padForSystemBars(findViewById(R.id.main_root))
        web = findViewById(R.id.web)
        swipe = findViewById(R.id.swipe)
        progress = findViewById(R.id.progress)
        errorBox = findViewById(R.id.error)
        errorText = findViewById(R.id.error_text)
        fullscreen = findViewById(R.id.fullscreen)
        findViewById<Button>(R.id.retry).setOnClickListener { retry() }
        findViewById<Button>(R.id.change_server).setOnClickListener { changeServer() }
        findViewById<TextView>(R.id.error_server).text = server

        swipe.setColorSchemeResources(R.color.brand)
        swipe.setOnRefreshListener { retry() }
        onBackPressedDispatcher.addCallback(this, back)

        setupWebView()
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) web.loadUrl("$server/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        WebView.setWebContentsDebuggingEnabled(applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        CookieManager.getInstance().setAcceptCookie(true)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true                      // localStorage / sessionStorage (IndexedDB is always on)
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)              // target=_blank -> shouldOverrideUrlLoading below
            mediaPlaybackRequiresUserGesture = true
            userAgentString = "$userAgentString CyberScoutAndroid/${BuildInfo.VERSION}"
        }
        // service workers (the web app's sw.js): same http rule as the page's own requests
        ServiceWorkerController.getInstance().apply {
            serviceWorkerWebSettings.allowContentAccess = false
            serviceWorkerWebSettings.allowFileAccess = false
            setServiceWorkerClient(object : ServiceWorkerClient() {
                override fun shouldInterceptRequest(request: WebResourceRequest): WebResourceResponse? =
                    policy.block(request.url)
            })
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                if (isOurs(url)) return false
                if (!request.isForMainFrame) return false     // embedded frames (e.g. YouTube) stay embedded
                openExternal(request.url)
                return true
            }

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                policy.block(request.url)

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                mainFrameError = false
                progress.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String?) {
                progress.visibility = View.GONE
                swipe.isRefreshing = false
                errorBox.visibility = if (mainFrameError) View.VISIBLE else View.GONE
                CookieManager.getInstance().flush()
                updateBack()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                mainFrameError = true
                errorText.text = getString(R.string.load_failed, error.description)
                errorBox.visibility = View.VISIBLE
                swipe.isRefreshing = false
            }

            override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) = updateBack()
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
            }

            // full-screen match videos
            override fun onShowCustomView(view: View, callback: CustomViewCallback) {
                if (customView != null) {
                    callback.onCustomViewHidden(); return
                }
                customView = view
                customCallback = callback
                fullscreen.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                fullscreen.visibility = View.VISIBLE
                swipe.visibility = View.GONE
                updateBack()
            }

            override fun onHideCustomView() {
                fullscreen.removeAllViews()
                fullscreen.visibility = View.GONE
                swipe.visibility = View.VISIBLE
                customView = null
                customCallback = null
                updateBack()
            }

            // <input type=file> (e.g. importing a file)
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    pickFile.launch(params.createIntent())
                    true
                } catch (e: ActivityNotFoundException) {
                    fileCallback = null
                    false
                }
            }
        }
        // downloads (CSV exports, videos, ...) open in the browser
        web.setDownloadListener { url, _, _, _, _ -> openExternal(Uri.parse(url)) }
        // pull-to-refresh only when the page itself is scrolled to the top
        swipe.setOnChildScrollUpCallback { _, _ -> web.scrollY > 0 }
    }

    private fun isOurs(url: String) = url == server || url.startsWith("$server/") || url.startsWith("$server?") ||
        url.startsWith("$server#")

    private fun updateBack() {
        back.isEnabled = customView != null || (this::web.isInitialized && web.canGoBack())
    }

    private fun retry() {
        errorBox.visibility = View.GONE
        val u = web.url
        if (u.isNullOrEmpty() || u == "about:blank" || mainFrameError) web.loadUrl("$server/") else web.reload()
    }

    private fun changeServer() {
        startActivity(Intent(this, SetupActivity::class.java))
    }

    private fun openExternal(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE))
        } catch (e: ActivityNotFoundException) {
            // nothing can open it: ignore
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == ACTION_CHANGE_SERVER) changeServer()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (this::web.isInitialized) web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
        if (this::web.isInitialized) web.onPause()
    }

    override fun onResume() {
        super.onResume()
        if (this::web.isInitialized) web.onResume()
    }

    override fun onDestroy() {
        if (this::web.isInitialized) {
            (web.parent as? ViewGroup)?.removeView(web)
            web.destroy()
        }
        super.onDestroy()
    }
}

/**
 * Plain http:// only to the team network (ServerAddress.cleartextAllowed): applied to every request of the page and
 * of its service worker. Name lookups are cached for a minute. https:// is never touched.
 */
class CleartextPolicy {
    private val cache = ConcurrentHashMap<String, Pair<Boolean, Long>>()

    fun allowed(host: String?): Boolean {
        val h = host?.lowercase() ?: return false
        val now = System.currentTimeMillis()
        cache[h]?.let { (ok, at) -> if (now - at < 60_000) return ok }
        val ok = ServerAddress.cleartextAllowed(h)
        cache[h] = ok to now
        return ok
    }

    fun block(url: Uri): WebResourceResponse? {
        if (url.scheme != "http" || allowed(url.host)) return null
        return WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", mapOf("Cache-Control" to "no-store"),
            ByteArrayInputStream("Cyber-Scout: plain http:// is only allowed on the team network; use https://".toByteArray()))
    }
}
