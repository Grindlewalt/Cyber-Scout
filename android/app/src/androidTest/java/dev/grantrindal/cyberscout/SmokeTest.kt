package dev.grantrindal.cyberscout

import android.content.Context
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.action.ViewActions.closeSoftKeyboard
import androidx.test.espresso.action.ViewActions.replaceText
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.withId
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.espresso.web.assertion.WebViewAssertions.webMatches
import androidx.test.espresso.web.sugar.Web.onWebView
import androidx.test.espresso.web.webdriver.DriverAtoms.findElement
import androidx.test.espresso.web.webdriver.DriverAtoms.getText
import androidx.test.espresso.web.webdriver.Locator
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import org.hamcrest.Matchers.allOf
import org.hamcrest.Matchers.containsString
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * End to end on a device / emulator: first-run screen -> Test connection against a mock Cyber-Scout server
 * (android/tools/mock_server.py; address from the `mockServer` instrumentation argument, default 10.0.2.2:8765 =
 * the emulator's host) -> Open -> the server's web app is loaded in the WebView.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class SmokeTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()
    private val mock: String = InstrumentationRegistry.getArguments().getString("mockServer") ?: "10.0.2.2:8765"

    @Before
    fun forgetServer() = Prefs.setServer(ctx, null)

    /** Espresso doesn't know about our network thread or the page load: retry the check for a while. */
    private fun eventually(timeoutMs: Long = 30_000, check: () -> Unit) {
        val end = System.currentTimeMillis() + timeoutMs
        while (true) {
            try {
                check(); return
            } catch (t: Throwable) {
                if (System.currentTimeMillis() > end) throw t
                Thread.sleep(250)
            }
        }
    }

    @Test
    fun testConnectionThenOpenTheWebApp() {
        ActivityScenario.launch(SetupActivity::class.java)
        onView(withId(R.id.address)).perform(replaceText(mock), closeSoftKeyboard())
        onView(withId(R.id.test)).perform(click())
        eventually {
            onView(withId(R.id.result)).check(matches(withText(allOf(
                containsString("Connected: Cyber-Scout mock"), containsString("version mock"), containsString(" ms")))))
        }
        onView(withId(R.id.address)).check(matches(withText("http://$mock")))     // normalised like the web app
        onView(withId(R.id.open)).perform(click())
        eventually(60_000) {
            onWebView().withElement(findElement(Locator.ID, "mock-app"))
                .check(webMatches(getText(), containsString("Cyber-Scout mock web app")))
        }
        assertEquals("http://$mock", Prefs.server(ctx))
    }

    @Test
    fun plainHttpToAPublicAddressIsRefused() {
        ActivityScenario.launch(SetupActivity::class.java)
        onView(withId(R.id.address)).perform(replaceText("http://8.8.8.8:9"), closeSoftKeyboard())
        onView(withId(R.id.open)).perform(click())
        eventually { onView(withId(R.id.result)).check(matches(withText(containsString("team network")))) }
        assertEquals(null, Prefs.server(ctx))
    }

    @Test
    fun launcherWithoutServerShowsSetup() {
        ActivityScenario.launch(SetupActivity::class.java)
        onView(withId(R.id.test)).check(matches(withText(R.string.test_connection)))
    }
}
