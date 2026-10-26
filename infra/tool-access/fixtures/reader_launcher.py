"""Actual Reader HTTP, with a test-only transport; NOT public-network proof.

Only fixture.public.example:8080 resolves to the global synthetic address.
Only that checked numeric destination is redirected to the owned HTML fixture.
The actual Reader validation, redirect and checked-connect paths remain intact.
"""

import importlib.util
import socket

spec = importlib.util.spec_from_file_location("actual_reader", "/candidate/reader.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
original_getaddrinfo = socket.getaddrinfo
original_socket = socket.socket
fixture_address = original_getaddrinfo("fixture", 8080, socket.AF_INET, socket.SOCK_STREAM)[0][4]


def fixture_dns(host, port, *args, **kwargs):
    if host == "fixture.public.example" and int(port) == 8080:
        return original_getaddrinfo("93.184.216.34", 8080, *args, **kwargs)
    return original_getaddrinfo(host, port, *args, **kwargs)


class FixtureSocket(original_socket):
    def connect(self, address):
        if address == ("93.184.216.34", 8080):
            address = fixture_address
        return super().connect(address)


socket.getaddrinfo = fixture_dns
socket.socket = FixtureSocket

if __name__ == "__main__":
    reader.main()
