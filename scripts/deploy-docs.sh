#/usr/bin/env bash
set -eu
rsync_host=piggie
rsync_dest=/var/www/flashflow.pastly.xyz

cd docs
sphinx-apidoc -fo . ../flashflow
make clean html
rsync -air --delete --exclude coverage _build/html/ $rsync_host:$rsync_dest/

cd ..
tox -e py37
rsync -air --delete htmlcov/ $rsync_host:$rsync_dest/coverage/
