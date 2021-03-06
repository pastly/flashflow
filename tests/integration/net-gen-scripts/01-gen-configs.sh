#!/usr/bin/env bash
set -eu

source 00-common.sh

which $tor_bin || exit 1
which $tor_gencert_bin || exit 1

guards="$(echo guard{1,2,3,4,5})"
middles="$(echo middle{1,2,3,4,5})"
exits="$(echo exit{1,2,3,4,5})"

function get_fingerprint {
	dir=$1
	[ -f $dir/torrc ] || exit 2
	$tor_bin --ignore-missing-torrc -f $dir/torrc  --Address 8.8.8.8 \
		--list-fingerprint | tail -n 1 | cut -d ' ' -f 2- \
		| sed 's|\ ||g'
}

function get_v3ident {
	dir=$1
	cert=$dir/keys/authority_certificate
	[ -f $cert ] || exit 2
	grep fingerprint $cert | cut -d ' ' -f 2
}

rm -fr auth?/ relay?/ guard?/ middle?/ exit?/ config*.ini datadir/ tor/ *.log torrc-common* $tmp_dir/{auth,relay}*

echo "
# Everyone includes this torrc first. Authorities. All relays. Clients.
ShutdownWaitLength 0
CookieAuthentication 1
ContactInfo pastly@torproject.org
LogTimeGranularity 1
SafeLogging 0
" > torrc-common

echo "
# All relays include this torrc, including exits. Authorities don't, nor do
# clients.
SocksPort 0
ControlPort 0
ORPort auto
#DirPort auto
" > torrc-common-relay

echo "
# Only exits include this torrc.
ExitRelay 1
IPv6Exit 1
ExitPolicy accept *:*
" > torrc-common-exit

next_auth_port=$start_auth_port

for A in auth1 auth2 auth3
do
	mkdir -pv $A/keys
	chmod 700 $A
	#mkdir -pv $tmp_dir/$A
	#chmod 700 $tmp_dir/$A
	orport=$((next_auth_port+0))
	dirport=$((next_auth_port+1))
    next_auth_port=$((next_auth_port+2))
	echo -n '' | $tor_gencert_bin --create-identity-key --passphrase-fd 0 -m 24 -a $ip:$dirport
	echo "
%include torrc-common
DataDirectory $A
PidFile $A/tor.pid
Address $ip
SocksPort 0
ControlPort 0
#ControlSocket $(pwd)/$A/control_socket
ORPort $ip:$orport
DirPort $ip:$dirport
Nickname $A
#CacheDirectory $tmp_dir/$A
	" > $A/torrc
	mv -v authority_* $A/keys/
	fp=$(get_fingerprint $A)
	v3ident=$(get_v3ident $A)
	echo "DirAuthority $A orport=$orport no-v2 v3ident=$v3ident $ip:$dirport $fp" \
	>> torrc-common

done

for A in $guards $middles $exits
do
	mkdir -pv $A
	chmod 700 $A
	#mkdir -pv $tmp_dir/$A
	#chmod 700 $tmp_dir/$A
	echo "%include torrc-common" > $A/torrc
	echo "%include torrc-common-relay" >> $A/torrc
    if [[ $A == exit* ]]; then
        echo "%include torrc-common-exit" >> $A/torrc
    fi
    echo "
DataDirectory $A
PidFile $A/tor.pid
#Log notice file $tmp_dir/$A/notice.log
Log notice file $A/notice.log
Address $ip
#ControlSocket $(pwd)/$A/control_socket
Nickname $A
#CacheDirectory $tmp_dir/$A
" >> $A/torrc
done

for A in client1
do
    mkdir -pv $A
    chmod 700 $A
    echo "
%include torrc-common
DataDirectory $A
PidFile $A/tor.pid
Log notice file $A/notice.log
SocksPort auto
ControlPort 4545
#ControlSocket $(pwd)/$A/control_socket
CookieAuthentication 0
" > $A/torrc
done

echo "
TestingTorNetwork 1
" >> torrc-common

echo "
AuthoritativeDirectory 1
V3AuthoritativeDirectory 1
TestingV3AuthInitialVotingInterval 5
V3AuthVotingInterval 1 minutes
TestingV3AuthInitialVoteDelay 2
V3AuthVoteDelay 2
TestingV3AuthInitialDistDelay 2
V3AuthDistDelay 2
ConsensusParams KISTSchedRunInterval=10
TestingDirAuthVoteGuard $(echo $guards | tr ' ' ',')
TestingDirAuthVoteGuardIsStrict 1
TestingDirAuthVoteExit $(echo $exits | tr ' ' ',')
TestingDirAuthVoteExitIsStrict 1
" > torrc-common-auth
for A in auth*/torrc
do
	echo "%include torrc-common-auth" >> $A
done

for A in auth* $guards $middles $exits; do
    echo $A $(get_fingerprint $A)
done
