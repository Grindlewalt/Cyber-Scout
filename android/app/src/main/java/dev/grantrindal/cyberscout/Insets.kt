package dev.grantrindal.cyberscout

import android.os.Build
import android.view.View
import android.view.WindowInsets

/** Edge-to-edge (enforced from targetSdk 35): keep content clear of the status / navigation bars and the keyboard. */
object Insets {
    fun padForSystemBars(v: View) {
        v.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val i = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                view.setPadding(i.left, i.top, i.right, i.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        v.requestApplyInsets()
    }
}
