Deploying FlashFlow
===================

Generating keys
---------------

FlashFlow coordinators and measurers all maintain TLS identify keys.
``scripts/gen-cert.sh`` can be used to help generate them.

Coordinator
^^^^^^^^^^^

::

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
This is how Python's SSL library like it (devs: they *can* be separate, but
this is easier).

Put this file in your key directory. By default your key directory is a
subdirectory of your data directory. By default it is thus ``data-coord/keys/``.

You need the certificates for all the measurers you trust. When the measurers
run this script, they should provide you with the bottom half of their output
file: just the certificate part. You put each measurer's certificate in its own
file in your keys directory in a file ending with ``.pem``.

::

    $ ls data-coord/keys
    coord.pem  # By default this is the file read for our own key/cert.
               # It contains our own private key and cert.
    measurer1.pem           # Contains measurer 1's cert
    measurer2.pem           # Contains measurer 2's cert
    measurer3.pem.disabled  # Not read
    notes.txt               # Not read

Running FlashFlow as a coordinator with the above keys directory loads two
measurer certs. The third measurer's cert file was skipped because the file
name doesn't end with ``.pem``. Measurer 3, were it to try to connect, would not
be allowed to complete the TLS handshake with us.

Measurer
^^^^^^^^

Run the same ``gen-cert.sh`` script.

::

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

Disk Usage Mangement
--------------------

FlashFlow can use a significant amount of disk space if you let it. **TODO: how
much?** For per-second result storage, you can address this with ``logrotate``.
**TODO: what about logs? What about v3bw files?**

Per-second results
^^^^^^^^^^^^^^^^^^

**Note: The info in this section is only partially true until
pastly/flashflow#4 is implemented. If the issue is closed and this message
still exists, this section needs updating such that it definitely matches the
actual implemented reality.**

For Debian 10 (Buster), logrotate should come on your system and already be
running daily. If not, install its package and ensure it's running daily.

::

   $ sudo systemctl list-timers
   NEXT                         LEFT       LAST                         PASSED       UNIT                         ACTIVATES
   [...]
   Sat 2020-06-27 00:00:00 EDT  12h left   Fri 2020-06-26 00:00:27 EDT  11h ago      logrotate.timer              logrotate.service
   [...]

You can run it manually like this (``--debug`` performs a dry run and lets you
see what *would* happen)::

   $ sudo /usr/sbin/logrotate --debug /etc/logrotate.conf

Or like this, which will run the ``logrotate.service`` file, probably located
at ``/lib/systemd/system/logrotate.service``::

   sudo systemctl start logrotate

Here is an example logrotate configuration file. Copy this into, for example,
``/etc/logrotate.d/flashflow``::

   # The filename to which flashflow writes its per-second results
   /home/matt/work/flashflow/data-coord/results/results.log {
      # keep 30 historic logs (if daily rotation, then 30 days)
      rotate 30
      # rotate daily
      daily
      # but don't bother rotating if the file is empty
      notifempty
      # gz compress when rotating
      compress
      # if log is missing, that's not an error, just skip
      missingok
   }

And that's it. Logrotate will see the new configuration file and use it next
time it runs. See logrotate's man page for possible options; for example, you
can rotate based on file size instead of time.

When generating v3bw files, FlashFlow reads the most recent few per-second
results files until it has gone far enough back into history. **TODO: how far
back? It's expected to be no more than a few days.** *Recent* is defined as the
files' modification times, not the lexicographic sort order of the filenames.
FlashFlow does this so that it doesn't have to care what your rotate naming
scheme is: configuring logrotate to append integers (e.g. ``results.log.1.gz``)
results in newer files sorting *sooner*, while configuring logrotate to append
a date (e.g.  ``results.log.20200630.gz``) results in newer files sorting
*later*. Doing it based on modification time means FlashFlow doesn't care your
preference.

The one thing FlashFlow *does* care about is that simply appending a ``*``
after your configured results file path will correctly glob *all* results
files. Don't configure logrotate to move them somewhere else.

With this config::

   [coord]
   datadir = data-coord
   resultsdir = ${datadir}/results
   results_log = ${resultsdir}/results.log

Then ``ls data-coord/results/results.log*`` will find all results files::

   $ ls -l data-coord/results/results.log*
   -rw-r--r-- 1 matt matt    0 Jun 26 11:34 data-coord/results/results.log
   -rw-r--r-- 1 matt matt  634 Jun 26 11:34 data-coord/results/results.log.1.gz
   -rw-r--r-- 1 matt matt 8619 Jun 26 03:50 data-coord/results/results.log.2.gz
