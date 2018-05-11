# mesona

A TLS MITM proxy using GnuTLS's length hiding capability that adds additional record padding to mitigate length-based analysis against TLS streams.

## Usage

Edit `configuration.py` under directory `mesona` for configuration and run module `mesona.proxy`.
```
python -m mesona.proxy
```

## Dependencies

* [Python](https://www.python.org/)
* [GnuTLS](https://gnutls.org/)
* [python-gnutls](https://github.com/AGProjects/python-gnutls)

## Configuration

The Python script `configuration.py` is directly `import`ed as the configuration. Each key-value pair in dictionary `settings` declares a proxy instance and `default_settings` is the default value of settings for a proxy instance.

Key in `settings` should be the server address although it is currently ignored. Refer to the documentation of python-gnutls for usage of `X509Certificate`, `X509Credentials`, `X509CRL` and `X509PrivateKey`.