#/usr/bin/env bash
set -eu
rsync_host=piggie
rsync_dest=/var/www/flashflow.pastly.xyz

cd docs
sphinx-apidoc -fo . ../flashflow
make clean html

rsync -air --delete _build/html/ $rsync_host:$rsync_dest/
