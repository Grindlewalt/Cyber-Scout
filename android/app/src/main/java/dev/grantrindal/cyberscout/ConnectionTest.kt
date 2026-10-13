package dev.grantrindal.cyberscout

import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException

/** "Test connection": GET <server>/app/v1/info (public, no sign-in; docs/SYNC.md section 1). */
object ConnectionTest {
    data class Info(val name: String?, val version: String?, val api: Int?)

    sealed class Result {
        data class Ok(val server: String, val info: Info, val latencyMs: Long) : Result()
        data class Failed(val server: String?, val message: String) : Result()
    }

    /** Parses an /app/v1/info answer; throws IllegalStateException when it is not a Cyber-Scout server. */
    fun parseInfo(status: Int, body: String): Info {
        val notUs = "this address answers, but it is not a Cyber-Scout server (GET /app/v1/info: HTTP $status)"
        if (status != 200) throw IllegalStateException(notUs)
        val o = try {
            JSONObject(body)
        } catch (e: Exception) {
            throw IllegalStateException(notUs)
        }
        if (o.optString("app") != "cyber-scout") throw IllegalStateException(notUs)
        return Info(
            name = o.optString("name").ifEmpty { null },
            version = o.optString("version").ifEmpty { null },
            api = if (o.has("api")) o.optInt("api") else null,
        )
    }

    fun cleartextRefusal(base: String): String? {
        if (ServerAddress.isHttps(base)) return null
        return if (ServerAddress.cleartextAllowed(ServerAddress.hostOfUrl(base))) null
        else "plain http:// is only allowed on the team network (10.x, 172.16-31.x, 192.168.x, 100.64-127.x, *.local). " +
            "Use the https:// address."
    }

    /** Blocking: call it off the main thread. */
    fun run(input: String, timeoutMs: Int = 8000): Result {
        val base = ServerAddress.normalize(input)
            ?: return Result.Failed(null, "that is not a server address (e.g. scout.example.org or 10.0.0.58:7560)")
        cleartextRefusal(base)?.let { return Result.Failed(base, it) }
        val t0 = System.nanoTime()
        var c: HttpURLConnection? = null
        return try {
            c = (URL("$base/app/v1/info").openConnection() as HttpURLConnection).apply {
                connectTimeout = timeoutMs
                readTimeout = timeoutMs
                instanceFollowRedirects = false       // only the address the user typed is contacted
                useCaches = false
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "CyberScoutAndroid/${BuildInfo.VERSION}")
            }
            val status = c.responseCode
            val body = (if (status in 200..299) c.inputStream else c.errorStream)
                ?.use { it.readBytes().take(64 * 1024).toByteArray().toString(Charsets.UTF_8) } ?: ""
            val ms = (System.nanoTime() - t0) / 1_000_000
            Result.Ok(base, parseInfo(status, body), ms)
        } catch (e: IllegalStateException) {
            Result.Failed(base, e.message ?: "not a Cyber-Scout server")
        } catch (e: IOException) {
            Result.Failed(base, describe(e))
        } catch (e: SecurityException) {
            Result.Failed(base, "the app may not use this address: ${e.message}")
        } finally {
            c?.disconnect()
        }
    }

    fun describe(e: IOException): String = when (e) {
        is UnknownHostException -> "server name not found (DNS): check the address and your connection"
        is ConnectException -> "connection refused (is the server running at that address?)"
        is SocketTimeoutException -> "timed out: the server did not answer"
        is SSLHandshakeException -> "the server's certificate could not be verified"
        is SSLException -> "secure connection failed (${e.message})"
        else -> if ((e.message ?: "").contains("CLEARTEXT", ignoreCase = true))
            "plain http:// is not allowed for this address: use https://"
        else "can't reach the server (${e.javaClass.simpleName}: ${e.message})"
    }
}

/** Kept apart from BuildConfig so the pure-JVM unit tests don't need the Android build. */
object BuildInfo {
    const val VERSION = "1.0.0"
}
