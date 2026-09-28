package io.github.mangocats.lempi

import org.json.JSONObject
import java.net.Socket
import java.net.URL
import java.security.Principal
import java.security.PrivateKey
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.X509TrustManager

/**
 * The members' channel from this phone [SPEC-MTR-200]: TLS to the hub's
 * members' port, trusting the hub by its key, never by a certificate
 * authority or a host name.
 *
 * `pinHub` is the hub's fingerprint once enrolled; any other server key is
 * refused. Before that -- at enrolment -- any key is accepted and recorded in
 * [seenHub], because the code a person compares covers that key: a device in
 * the middle would show a different one [SPEC-MTR-130].
 *
 * `present` offers this phone's own key as the client certificate, which is
 * what makes it a member at the other end. At enrolment it is not offered: the
 * hub would refuse a key its roster does not yet hold.
 */
class MeshClient(private val base: String, private val pinHub: String?, private val present: Boolean) {
    @Volatile var seenHub: String? = null
        private set

    private val trust = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("a client, not a server")

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val fp = NodeIdentity.fingerprint(chain[0])
            seenHub = fp
            if (pinHub != null && fp != pinHub) {
                throw CertificateException("not the hub this phone enrolled with")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    }

    private val ownKey = object : X509ExtendedKeyManager() {
        override fun chooseClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?) =
            NodeIdentity.ALIAS
        override fun chooseEngineClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, engine: SSLEngine?) =
            NodeIdentity.ALIAS
        override fun getCertificateChain(alias: String?): Array<X509Certificate> = arrayOf(NodeIdentity.certificate())
        override fun getPrivateKey(alias: String?): PrivateKey = NodeIdentity.privateKey()
        override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?) = arrayOf(NodeIdentity.ALIAS)
        override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? = null
        override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?): String? = null
    }

    private val context: SSLContext = SSLContext.getInstance("TLSv1.3").apply {
        init(if (present) arrayOf<KeyManager>(ownKey) else null, arrayOf(trust), null)
    }

    /** A GET whose answer is a file, streamed to `dest`. */
    fun download(path: String, dest: java.io.File) {
        val c = URL(base + path).openConnection() as HttpsURLConnection
        c.sslSocketFactory = context.socketFactory
        c.hostnameVerifier = HostnameVerifier { _, _ -> true }
        c.connectTimeout = 8000
        c.readTimeout = 120000
        if (c.responseCode != 200) throw IllegalStateException("the hub answered ${c.responseCode}")
        c.inputStream.use { input -> dest.outputStream().use { input.copyTo(it) } }
    }

    /** One request: its status and its JSON answer. */
    fun call(method: String, path: String, body: JSONObject? = null): Pair<Int, JSONObject> =
        send(method, path, body?.toString()?.toByteArray(), "application/json")

    /** One request with a body of bytes, and any headers of its own. */
    fun send(method: String, path: String, bytes: ByteArray?, type: String,
             headers: Map<String, String> = emptyMap()): Pair<Int, JSONObject> {
        val c = URL(base + path).openConnection() as HttpsURLConnection
        c.sslSocketFactory = context.socketFactory
        // Who answered is settled by the pinned key above; a host name proves nothing here.
        c.hostnameVerifier = HostnameVerifier { _, _ -> true }
        c.requestMethod = method
        c.connectTimeout = 8000
        c.readTimeout = 60000
        c.setRequestProperty("X-Lempi-Sender", "Lempi on ${android.os.Build.MODEL}")
        headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
        if (bytes != null) {
            c.doOutput = true
            c.setFixedLengthStreamingMode(bytes.size)
            c.setRequestProperty("Content-Type", type)
            c.outputStream.use { it.write(bytes) }
        }
        val code = c.responseCode
        val text = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: "{}"
        return code to JSONObject(text.ifBlank { "{}" })
    }
}
