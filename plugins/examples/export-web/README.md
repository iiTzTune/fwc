# Signed observation summary

Install this directory as `export-web`. Configure the plugin's `endpoint`
with the exact HTTPS URL of your receiver and grant `network.out` and
`networks.read`. Each invocation sends counts for the latest sample of up
to 16 network records, including the owned count and a count per channel.

Each device and destination has a dedicated 32-byte shared key. In the device
state directory, its path is `export-keys/<digest>.key`, where the digest is
the lowercase SHA-256 hex of the exact configured URL in ASCII (no newline).
Provision the same 32 random bytes on both device and receiver through a
private channel, with mode 0600 on the device file and 0700 on its enclosing
directory. If absent, the host creates the key on its first authorized upload.
Changing the URL selects a different key.

The receiver reads the exact HTTP request body as bytes. With the matching
key loaded as bytes, compute and compare the header before accepting the data.

```python
import hmac

expected = "sha256=" + hmac.digest(key, body, "sha256").hex()
valid = hmac.compare_digest(expected, headers.get("X-FWC-Signature", ""))
```

A 2xx status acknowledges the upload. Configure the final receiver URL directly.
