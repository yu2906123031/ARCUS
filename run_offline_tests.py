"""Run all repository tests with external DNS and socket connections blocked."""
import argparse
import ipaddress
import os
from pathlib import Path
import sys
import unittest

def loopback(host):
    if host=="localhost":return True
    try:
        address=ipaddress.ip_address(host)
        return address.is_loopback or bool(getattr(address,"ipv4_mapped",None) and address.ipv4_mapped.is_loopback)
    except ValueError:return False

def network_guard(event,args):
    if event=="socket.getaddrinfo":
        if args[0] is not None and not loopback(args[0]):
            raise RuntimeError("external DNS blocked in offline tests")
    elif event=="socket.connect":
        address=args[1]
        if not isinstance(address,tuple) or not loopback(address[0]):
            raise RuntimeError("external connection blocked in offline tests")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v","--verbose",action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    os.chdir(root)
    sys.addaudithook(network_guard)
    suite=unittest.defaultTestLoader.discover(str(root))
    result=unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__=="__main__":raise SystemExit(main())
