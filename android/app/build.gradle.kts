plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.hotshare"
    compileSdk = 34
    defaultConfig { applicationId = "com.hotshare"; minSdk = 29; targetSdk = 34; versionCode = 3; versionName = "3.0" }
    buildTypes {
        release {
            isMinifyEnabled = true          // R8 shrink + optimize
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            signingConfig = signingConfigs.getByName("debug")   // sideloadable; swap for your own key for Play Store
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    lint { abortOnError = false; checkReleaseBuilds = false }   // a lint warning must never fail the CI build
}
dependencies { implementation("androidx.documentfile:documentfile:1.0.1") }
