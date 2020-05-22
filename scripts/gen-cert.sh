#!/usr/bin/env bash
set -eu
# Generate a new self-signed certificate suitable for use in a FlashFlow
# deployment. This script can be used both for coordinator and measurer keys. 
#
# Specify a name for the output file as the only argument. For example, give
# "pastly" for the name, and the private key and certificate will be stored in
# "pastly.pem".

LIFE=36500 # days
NAME=$1
COMMENT="$NAME"

openssl req -x509 -newkey rsa:2048 -keyout "${NAME}.pem" -out "${NAME}.pem" -nodes -days "$LIFE" -subj "/O=$COMMENT"
