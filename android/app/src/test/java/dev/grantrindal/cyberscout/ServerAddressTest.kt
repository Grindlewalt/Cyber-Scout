package dev.grantrindal.cyberscout

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.InetAddress

class ServerAddressTest {
    /** Expected values produced by web/sync.js normServer itself (node), so the app reads addresses like the web app. */
    private val parity: List<Pair<String, String?>> = listOf(
        "scout.grantrindal.dev" to "https://scout.grantrindal.dev",
        "https://scout.grantrindal.dev" to "https://scout.grantrindal.dev",
        "https://scout.grantrindal.dev/" to "https://scout.grantrindal.dev",
        "  Scout.GrantRindal.dev  " to "https://scout.grantrindal.dev",
        "10.0.0.58:7560" to "http://10.0.0.58:7560",
        "http://10.0.0.58:7560/" to "http://10.0.0.58:7560",
        "192.168.1.20" to "http://192.168.1.20",
        "localhost:7560" to "http://localhost:7560",
        "scoutbox" to "http://scoutbox",
        "scoutbox.local" to "http://scoutbox.local",
        "pit.lan:8080" to "http://pit.lan:8080",
        "atomos.tail1234.ts.net" to "http://atomos.tail1234.ts.net",
        "https://scout.example.org/app/v1/info" to "https://scout.example.org",
        "https://scout.example.org/index.html" to "https://scout.example.org",
        "https://example.org/cs/" to "https://example.org/cs",
        "http://scout.example.org" to "http://scout.example.org",
        "HTTPS://SCOUT.EXAMPLE.ORG:443/" to "https://scout.example.org",
        "http://10.0.0.58:80" to "http://10.0.0.58",
        "[fd00::1]:7560" to "http://[fd00::1]:7560",
        "ftp://x.org" to null,
        "https://user:pw@x.org" to null,
        "" to null,
        "   " to null,
        "https://" to null,
        "https://x.org?a=1#b" to "https://x.org",
        "100.101.102.103:7560" to "http://100.101.102.103:7560"

    )

    @Test
    fun normalizeMatchesTheWebApp() {
        for ((input, want) in parity) assertEquals("normalize($input)", want, ServerAddress.normalize(input))
    }

    @Test
    fun cleartextOnlyOnTheTeamNetwork() {
        val noDns: (String) -> List<InetAddress> = { fail("unexpected DNS lookup of $it"); emptyList() }
        for (h in listOf("10.0.0.58", "10.255.255.255", "172.16.0.1", "172.31.255.254", "192.168.1.20", "100.64.0.1",
            "100.127.255.254", "127.0.0.1", "169.254.1.1", "[fd7a:115c:a1e0::1]", "fe80::1", "localhost",
            "scoutbox.local", "pit.LOCAL")) {
            assertTrue(h, ServerAddress.cleartextAllowed(h, noDns))
        }
        for (h in listOf("8.8.8.8", "172.15.0.1", "172.32.0.1", "192.169.0.1", "100.63.255.255", "100.128.0.1",
            "11.0.0.1", "[2606:4700::1]", "999.1.1.1")) {
            assertFalse(h, ServerAddress.cleartextAllowed(h) { emptyList() })
        }
        assertFalse(ServerAddress.cleartextAllowed(null))
        assertFalse(ServerAddress.cleartextAllowed(""))
    }

    @Test
    fun namesAreJudgedByWhatTheyResolveTo() {
        fun ips(vararg a: String): (String) -> List<InetAddress> = { a.map { ServerAddress.literal(it)!! } }
        assertTrue(ServerAddress.cleartextAllowed("pit.lan", ips("192.168.4.2")))
        assertTrue(ServerAddress.cleartextAllowed("atomos.tail1234.ts.net", ips("100.101.102.103")))
        assertFalse(ServerAddress.cleartextAllowed("scout.example.org", ips("203.0.113.9")))
        assertFalse(ServerAddress.cleartextAllowed("mixed.lan", ips("10.0.0.2", "203.0.113.9")))
        assertFalse(ServerAddress.cleartextAllowed("nowhere.lan") { throw java.net.UnknownHostException(it) })
    }

    @Test
    fun cleartextRefusalOnlyForHttp() {
        assertNull(ConnectionTest.cleartextRefusal("https://scout.grantrindal.dev"))
        assertNull(ConnectionTest.cleartextRefusal("http://10.0.0.58:7560"))
        assertTrue(ConnectionTest.cleartextRefusal("http://8.8.8.8")!!.contains("https://"))
    }
}
