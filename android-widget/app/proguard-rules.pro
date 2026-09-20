# Reglas de ProGuard/R8 para la versión release.
#
# minifyEnabled está desactivado en este proyecto, así que estas reglas son
# solo una red de seguridad por si algún día se activa.

# WorkManager se instancia por reflexión desde su propia inicialización.
-keep class androidx.work.** { *; }
-keepclassmembers class * extends androidx.work.ListenableWorker {
    <init>(android.content.Context, androidx.work.WorkerParameters);
}

# El receptor del widget lo instancia el sistema.
-keep class net.monitor.descargador.WidgetProvider { *; }
-keep class net.monitor.descargador.ActionReceiver { *; }
