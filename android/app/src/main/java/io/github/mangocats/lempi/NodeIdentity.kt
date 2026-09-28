package io.github.mangocats.lempi

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.cert.Certificate
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.util.Date
import javax.security.auth.x500.X500Principal

/**
 * This phone's identity in a mesh [SPEC-MTR-100]: an ECDSA P-256 key made in
 * Android's Keystore on first use, which never leaves it, and the self-signed
 * certificate the Keystore makes for it. P-256 rather than Ed25519 because the
 * Keystore on Android 11, the floor, has no Ed25519, and because this key is
 * also the phone's TLS client certificate [SPEC-MTR-200].
 */
object NodeIdentity {
    const val ALIAS = "lempi-node"

    private fun store(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    fun certificate(): X509Certificate {
        val ks = store()
        if (!ks.containsAlias(ALIAS)) generate()
        return ks.getCertificate(ALIAS) as X509Certificate
    }

    fun privateKey(): PrivateKey {
        certificate()
        return store().getKey(ALIAS, null) as PrivateKey
    }

    private fun generate() {
        val now = System.currentTimeMillis()
        val day = 86_400_000L
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            // NONE as well as the named digests: TLS may ask the Keystore to
            // sign a digest it has computed itself.
            .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256,
                KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
            .setCertificateSubject(X500Principal("CN=lempi-node"))
            .setCertificateSerialNumber(BigInteger.valueOf(now))
            .setCertificateNotBefore(Date(now - day))
            .setCertificateNotAfter(Date(now + 36_500L * day))
            .build()
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply {
            initialize(spec)
            generateKeyPair()
        }
    }

    /** SHA-256 of the public key's DER (SubjectPublicKeyInfo), as the hub computes it. */
    fun fingerprint(cert: Certificate = certificate()): String = hex(sha256(cert.publicKey.encoded))

    /** An ECDSA signature by this phone's key, base64, as the hub verifies it. */
    fun sign(text: String): String {
        val s = Signature.getInstance("SHA256withECDSA")
        s.initSign(privateKey())
        s.update(text.toByteArray())
        return Base64.encodeToString(s.sign(), Base64.NO_WRAP)
    }

    fun pem(cert: X509Certificate = certificate()): String =
        "-----BEGIN CERTIFICATE-----\n" + Base64.encodeToString(cert.encoded, Base64.DEFAULT) +
            "-----END CERTIFICATE-----\n"

    /** Eight groups of four hex digits; the first two as the short form [SPEC-MTR-100]. */
    fun show(fp: String, short: Boolean = false): String =
        fp.take(32).chunked(4).take(if (short) 2 else 8).joinToString(" ")

    fun sha256(b: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(b)

    fun hex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }
}
