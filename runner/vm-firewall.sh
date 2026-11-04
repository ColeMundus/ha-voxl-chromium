#!/bin/sh
set -eu
if /usr/sbin/nft list table inet gh_voxl_vm >/dev/null 2>&1; then
    /usr/sbin/nft delete table inet gh_voxl_vm
fi
exec /usr/sbin/nft -f /etc/gh-voxl-vm.nft
