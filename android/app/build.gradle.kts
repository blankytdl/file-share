plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.hotshare"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.hotshare"; minSdk = 26; targetSdk = 34
        // always increases with each GitHub run, so a new build can be installed over an older one
        versionCode = 1000 + (System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 0)
        versionName = "3.1"
    }
    signingConfigs {
        // Fixed key committed with the project: every build is signed identically, so updates install cleanly.
        create("hotshare") { storeFile = file("../hotshare.keystore"); storePassword = "hotshare"; keyAlias = "hotshare"; keyPassword = "hotshare"; storeType = "pkcs12" }
    }
    buildTypes {
        release {
            isMinifyEnabled = true          // R8 shrink + optimize
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            signingConfig = signingConfigs.getByName("hotshare")
        }
        debug { signingConfig = signingConfigs.getByName("hotshare") }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    lint { abortOnError = false; checkReleaseBuilds = false }   // a lint warning must never fail the CI build
}
dependencies { implementation("androidx.documentfile:documentfile:1.0.1") }
