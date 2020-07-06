#!/usr/bin/env bash
set -eu

source 00-common.sh

for torrc in */torrc; do
	echo $torrc
    sock=$(realpath $(dirname $torrc)/control_socket)
    $tor_bin -f $torrc --ControlSocket $sock --quiet &
done
