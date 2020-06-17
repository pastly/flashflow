# Deploying FlashFlow

## Generating keys

FlashFlow coordinators and measurers all maintain TLS identify keys.
`scripts/gen-cert.sh` can be used to help generate them.

### Coordinator

    $ ./scripts/gen-cert.sh coord
    Generating a RSA private key
    ............................+++++
    ..+++++
    writing new private key to 'coord.pem'
    -----
    $ cat coord.pem
    -----BEGIN PRIVATE KEY-----
    [... base64 stuff ...]
    -----END PRIVATE KEY-----
    -----BEGIN CERTIFICATE-----
    [... base64 stuff ...]
    -----END CERTIFICATE-----

As you can see, both the private key and the certificate are in the same file.
This is how Python's SSL library like it (devs: they *can* be seperate, but
this is easier).

Put this file in your key directory. By default your key directory is a
subdirectory of your data directory. By default it is thus `data-coord/keys/`.

You need the certificates for all the measurers you trust. When the measurers
run this script, they should provide you with the bottom half of their output
file: just the certificate part. You put each measurer's certificate in its own
file in your keys directory in a file ending with `.pem`.

    $ ls data-coord/keys
    coord.pem  # By default this is the file read for our own key/cert.
               # It contains our own private key and cert.
    measurer1.pem           # Contains measurer 1's cert
    measurer2.pem           # Contains measurer 2's cert
    measurer3.pem.disabled  # Not read
    notes.txt               # Not read

Running FlashFlow as a coordinator with the above keys directory loads two
measurer certs. The third measurer's cert file was skipped because the file
name doesn't end with `.pem`. Measurer 3, were it to try to connect, would not
be allowed to complete the TLS handshake with us.

### Measurer

Run the same `gen-cert.sh` script.

    $ ./scripts/gen-cert.sh measurer1
    Generating a RSA private key
    ............................+++++
    ..+++++
    writing new private key to 'measurer1.pem'
    -----
    $ cat measurer1.pem
    -----BEGIN PRIVATE KEY-----
    [... base64 stuff ...]
    -----END PRIVATE KEY-----
    -----BEGIN CERTIFICATE-----
    [... base64 stuff ...]
    -----END CERTIFICATE-----

You need to hold on to the entire file: you need your private key. But the
coordinator needs your certificate. Copy the certificate part of the file into
a new file and send it to the coordinator. **That means just the lines between
BEGIN CERTIFICATE and END CERTIFICATE, inclusively**.
