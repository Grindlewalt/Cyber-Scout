plugins {
    // AGP 9 compiles Kotlin itself (built-in Kotlin): no separate kotlin-android plugin
    id("com.android.application")
}

android {
    namespace = "dev.grantrindal.cyberscout"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.grantrindal.cyberscout"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // where the instrumented smoke test finds the mock server (10.0.2.2 = the emulator's host);
        // override with -Pandroid.testInstrumentationRunnerArguments.mockServer=<address>
        testInstrumentationRunnerArguments["mockServer"] = "10.0.2.2:8765"
    }

    buildTypes {
        release {
            // a small app with no dependencies worth shrinking; keeps stack traces readable
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        abortOnError = true
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("androidx.activity:activity:1.13.0")              // back handling (predictive back), file picker
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.2.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")                      // real org.json for JVM unit tests

    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-web:3.7.0")
}
