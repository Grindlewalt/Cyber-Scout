# Cyber-Scout for Android

A small native app that opens **your team's Cyber-Scout server** in a full-screen WebView. The app itself holds no
scouting code or data: everything (pages, sign-in, offline copies, the outbox) is the server's web app, so it is
always the version the server runs. No analytics, no accounts of its own, no other network traffic.

* **First run**: type the server address the way you would in a browser: `scout.grantrindal.dev`,
  `https://scout.grantrindal.dev` or `10.0.0.58:7560` (read exactly like the web app does: a name gets `https://`, a
  bare IP / `.local` / `.lan` / Tailscale name gets `http://`). **Test connection** calls the server's public
  `GET /app/v1/info` and shows its name, version and the round trip in ms, or a clear error (name not found,
  connection refused, certificate, "answers, but is not a Cyber-Scout server"). **Open** remembers the address.
* **Then** the app opens straight into the web app, with JavaScript, DOM storage (localStorage / IndexedDB) and
  service workers on, so the web app's offline layer works as in Chrome: **Save this event for offline**, the outbox
  of notes / picklist changes made offline, the connection pill.
* Back = the web app's history (then leaves the app). Pull down at the top of a page to reload. Links to other sites
  and file downloads open in your browser. Match videos can go full screen.
* **Change server**: long-press the app icon → *Change server*, or the button on the "could not load" screen.

### https vs http (offline copies)

Service workers only run on **https://** addresses, so *Save for offline* and offline browsing work only through the
https address (e.g. `https://scout.grantrindal.dev`, the Cloudflare tunnel). The plain `http://10.0.0.58:7560`
address (shop LAN / Tailscale) works while connected; the setup screen says so when you pick an http address.

Plain http:// is allowed **only to the team network**: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10
(Tailscale), loopback / link-local / IPv6 unique-local, `*.local`, and names that resolve only to such addresses.
Android's network security config (`res/xml/network_security_config.xml`) can list host names but not address
ranges, so it permits cleartext and the app enforces the ranges itself (`ServerAddress.cleartextAllowed`): for Test
connection / Open, and for every request of the page and its service worker (`CleartextPolicy`, answered 403).
https uses the system certificate authorities only.

## Install

**APK (Android 8.0+)**: download `cyberscout-android.apk` (GitHub Actions `android` workflow artifact, or a release),
open it on the phone and allow "Install unknown apps" for your browser / Files app when asked. It is signed with a
debug key, so Play Protect may warn ("unknown developer") → *Install anyway*. Updating needs an APK signed with the
same key (a CI build uses the runner's fresh debug key: uninstall first, or set up a release key, below).

**No APK: install the web app (PWA)**: open the server's https address in Chrome → ⋮ → *Add to Home screen* /
*Install app* (iPhone: Safari → Share → *Add to Home Screen*). The web app ships `manifest.webmanifest` + icons for
this. Same features as the APK except the native setup screen.

## Build

Needs JDK 17+ and the Android SDK (platform `android-37.0`, build-tools 37). Android Studio: open `android/`.

```bash
cd android
./gradlew testDebugUnitTest lintDebug       # JVM unit tests (address rules incl. parity with web/sync.js, Test connection)
./gradlew assembleDebug                     # app/build/outputs/apk/debug/app-debug.apk (debug-signed, installable)
./gradlew assembleRelease                   # app/build/outputs/apk/release/app-release-unsigned.apk

# instrumented smoke test on a device / emulator (setup -> Test connection -> Open -> web app in the WebView)
python3 tools/mock_server.py 8765 &         # stand-in server: /app/v1/info + a tiny page
./gradlew connectedDebugAndroidTest         # emulator reaches it as 10.0.2.2:8765 (default)
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.mockServer=192.168.1.20:8765  # a phone
```

Signing a release: create a keystore once (`keytool -genkeypair -v -keystore cyberscout.jks -alias cyberscout -keyalg
RSA -keysize 4096 -validity 10000`), then `apksigner sign --ks cyberscout.jks --out cyberscout.apk
app/build/outputs/apk/release/app-release-unsigned.apk` (after `zipalign -p 4`), or add a `signingConfig` fed from CI
secrets. Keep the keystore safe: every update must be signed with it.

**CI** (`.github/workflows/android.yml`): unit tests + lint + both APKs (artifacts `cyberscout-android.apk` and
`cyberscout-android-release-unsigned.apk`), and the instrumented smoke test on x86_64 emulators (API 30 and 34,
`reactivecircus/android-emulator-runner`) against `tools/mock_server.py` running on the runner.

## Code

`app/src/main/java/dev/grantrindal/cyberscout/`: `ServerAddress.kt` (address normalisation = web/sync.js
`normServer`, team-network rule), `ConnectionTest.kt` (`GET /app/v1/info`, error texts), `SetupActivity.kt`
(first-run screen, `Prefs`), `MainActivity.kt` (WebView, service-worker client, back, pull-to-refresh, downloads /
external links, full-screen video, file chooser, `CleartextPolicy`). Tests: `app/src/test` (JVM), `app/src/androidTest`
(Espresso + Espresso-Web). Kotlin, minSdk 26, targetSdk 37, AGP 9 (built-in Kotlin), Gradle wrapper 9.8.1.
