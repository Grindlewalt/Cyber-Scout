package dev.grantrindal.cyberscout

import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress

class ConnectionTestTest {
    private lateinit var srv: HttpServer
    private var answer: Pair<Int, String> = 200 to ""

    @Before
    fun start() {
        srv = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        srv.createContext("/") { ex ->
            val (code, body) = if (ex.requestURI.path == "/app/v1/info") answer else 404 to "{\"detail\":\"not found\"}"
            val b = body.toByteArray()
            ex.responseHeaders.add("Content-Type", "application/json")
            ex.sendResponseHeaders(code, b.size.toLong())
            ex.responseBody.use { it.write(b) }
        }
        srv.start()
    }

    @After
    fun stop() = srv.stop(0)

    private val addr get() = "127.0.0.1:${srv.address.port}"

    @Test
    fun okShowsNameVersionLatency() {
        answer = 200 to """{"app":"cyber-scout","api":1,"version":"4c43baf","name":"Cyber-Scout · Team 4911","time":1}"""
        val r = ConnectionTest.run(addr)
        assertTrue(r.toString(), r is ConnectionTest.Result.Ok)
        r as ConnectionTest.Result.Ok
        assertEquals("http://$addr", r.server)
        assertEquals(ConnectionTest.Info("Cyber-Scout · Team 4911", "4c43baf", 1), r.info)
        assertTrue(r.latencyMs >= 0)
    }

    @Test
    fun otherServersAreRecognised() {
        answer = 200 to """{"hello":"world"}"""
        val r = ConnectionTest.run(addr) as ConnectionTest.Result.Failed
        assertTrue(r.message, r.message.contains("not a Cyber-Scout server"))
        answer = 502 to "<html>bad gateway</html>"
        val r2 = ConnectionTest.run(addr) as ConnectionTest.Result.Failed
        assertTrue(r2.message, r2.message.contains("HTTP 502"))
    }

    @Test
    fun refusedAndBadAddresses() {
        val port = srv.address.port
        srv.stop(0)
        val r = ConnectionTest.run("127.0.0.1:$port") as ConnectionTest.Result.Failed
        assertTrue(r.message, r.message.contains("refused"))
        val r2 = ConnectionTest.run("ftp://x.org") as ConnectionTest.Result.Failed
        assertTrue(r2.message, r2.message.contains("not a server address"))
        val r3 = ConnectionTest.run("http://8.8.8.8:9") as ConnectionTest.Result.Failed
        assertTrue(r3.message, r3.message.contains("team network"))
        srv = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).also { it.start() }   // for @After
    }

    @Test
    fun parseInfo() {
        assertEquals(ConnectionTest.Info(null, "x", null), ConnectionTest.parseInfo(200, """{"app":"cyber-scout","version":"x"}"""))
    }
}
