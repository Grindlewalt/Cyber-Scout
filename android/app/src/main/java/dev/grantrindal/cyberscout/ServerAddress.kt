package dev.grantrindal.cyberscout

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI

/**
 * Server addresses, the same way the web app reads them (web/sync.js `normServer`):
 * `scout.grantrindal.dev` -> `https://scout.grantrindal.dev`, `10.0.0.58:7560` -> `http://10.0.0.58:7560`,
 * `https://x.org/app/v1/info` -> `https://x.org`. Pure Kotlin (no Android classes) so it is unit-tested on the JVM.
 */
object ServerAddress {
    private val SCHEME = Regex("^[a-z][a-z0-9+.-]*://", RegexOption.IGNORE_CASE)
    private val IPV4 = Regex("^(\\d{1,3}\\.){3}\\d{1,3}$")
    private val LOCAL_NAME = Regex("\\.(local|lan|home|internal|home\\.arpa|ts\\.net)$")
    private val APP_PATH = Regex("/(index\\.html|app/v1(/.*)?)$")

    /** The normalised base address (no trailing slash), or null if [input] is not a server address. */
    fun normalize(input: String?): String? {
        var s = input?.trim().orEmpty()
        if (s.isEmpty()) return null
        if (!SCHEME.containsMatchIn(s)) {
            // a bare address on the team network (IP, IPv6, localhost, no dot, .local / .lan / Tailscale) is plain
            // http; names are https
            val host = hostOf("http://$s") ?: return null
            val local = IPV4.matches(host) || host.startsWith("[") || host == "localhost" || !host.contains('.') ||
                LOCAL_NAME.containsMatchIn(host)
            s = (if (local) "http://" else "https://") + s
        }
        val u = try {
            URI(s)
        } catch (e: Exception) {
            return null
        }
        val scheme = u.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        if (u.rawUserInfo != null) return null
        val host = u.host?.lowercase()
        if (host.isNullOrEmpty()) return null
        val port = when {
            u.port == -1 -> ""
            scheme == "http" && u.port == 80 -> ""
            scheme == "https" && u.port == 443 -> ""
            else -> ":${u.port}"
        }
        val path = (u.rawPath ?: "").replace(APP_PATH, "").trimEnd('/')
        return "$scheme://$host$port$path"
    }

    private fun hostOf(url: String): String? = try {
        URI(url).host?.lowercase()
    } catch (e: Exception) {
        null
    }

    fun hostOfUrl(url: String): String? = hostOf(url)

    fun isHttps(base: String): Boolean = base.startsWith("https://")

    /**
     * May plain http:// go to [host]? Only on the team network: 10/8, 172.16/12, 192.168/16, 100.64/10 (Tailscale,
     * carrier-grade NAT), loopback, link-local, IPv6 unique-local / link-local, `*.local` and `localhost`, or a name
     * that resolves only to such addresses (e.g. `scoutbox`, `pit.lan`, a Tailscale `*.ts.net` name).
     * `resolve` is only called for names (not for IP literals); a name that can't be resolved is refused.
     */
    fun cleartextAllowed(host: String?, resolve: (String) -> List<InetAddress> = { InetAddress.getAllByName(it).toList() }): Boolean {
        val h = host?.lowercase()?.removePrefix("[")?.removeSuffix("]")?.trimEnd('.') ?: return false
        if (h.isEmpty()) return false
        if (h == "localhost" || h.endsWith(".local")) return true
        literal(h)?.let { return isTeamNetwork(it) }
        return try {
            val addrs = resolve(h)
            addrs.isNotEmpty() && addrs.all { isTeamNetwork(it) }
        } catch (e: Exception) {
            false
        }
    }

    /** An IP literal parsed without any DNS lookup, or null for a name. */
    fun literal(h: String): InetAddress? {
        if (IPV4.matches(h)) {
            val parts = h.split('.').map { it.toInt() }
            if (parts.any { it > 255 }) return null
            return InetAddress.getByAddress(parts.map { it.toByte() }.toByteArray())
        }
        if (h.contains(':') && h.all { it.isLetterOrDigit() || it == ':' || it == '.' }) {
            return try {
                InetAddress.getByName(h)      // an IPv6 literal: parsed, never looked up
            } catch (e: Exception) {
                null
            }
        }
        return null
    }

    fun isTeamNetwork(a: InetAddress): Boolean {
        if (a.isLoopbackAddress || a.isLinkLocalAddress) return true
        val b = a.address.map { it.toInt() and 0xff }
        return when (a) {
            is Inet4Address -> b[0] == 10 ||
                (b[0] == 172 && b[1] in 16..31) ||
                (b[0] == 192 && b[1] == 168) ||
                (b[0] == 100 && b[1] in 64..127)
            is Inet6Address -> (b[0] and 0xfe) == 0xfc     // fc00::/7 unique local (Tailscale: fd7a:115c:a1e0::/48)
            else -> false
        }
    }
}
