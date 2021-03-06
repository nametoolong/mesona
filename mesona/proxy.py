import signal
import socket
import SocketServer
import sys
import threading
import traceback
import time

from gnutls.connection import TLSContext
from gnutls.errors import GNUTLSError

from mesona.lengthhiding import LengthHidingClientSession, LengthHidingServerSession

class ReaderError(Exception):
    pass

class WriterError(Exception):
    pass

def connection_reader(src, buffer_size):
    while True:
        try:
            data = src.recv(buffer_size)
        except GNUTLSError as e:
            raise ReaderError(e.message)

        if len(data) == 0:
            return

        yield data

def send_range_safe(dst, data, range_low, range_high):
    range_low = max(range_low, 1 - len(data))
    range_high = range_high

    dst.send_range(data, (range_low, range_high))

def forward_connection_with_padding(reader, dst, range_low, range_high):
    for data in reader:
        try:
            send_range_safe(dst, data, range_low, range_high)
        except GNUTLSError as e:
            raise WriterError(e.message)

def forward_connection(reader, dst):
    for data in reader:
        try:
            dst.send(data)
        except GNUTLSError as e:
            raise WriterError(e.message)

def is_ipv6_address(address):
    # an evil way to tell apart IPv6 addresses
    ip_address = address[0]
    return ip_address.startswith('[') and ip_address.endswith(']')

class MITMServer(SocketServer.ThreadingTCPServer):

    daemon_threads = True

    logging_lock = threading.Lock()

    def __init__(self, config, bind_and_activate=True):
        self.config = config

        if is_ipv6_address(config.listen_address):
            self.address_family = socket.AF_INET6

        self.server_context = TLSContext(config.credentials_as_server, config.priority_string_as_server)
        self.client_context = TLSContext(config.credentials_as_client, config.priority_string_as_client)

        SocketServer.ThreadingTCPServer.__init__(self, config.listen_address, MITMHandler, bind_and_activate)

    def handle_error(self, request, client_address):
        self.print_exc()

    def print_exc(self):
        if self.config.suppress_exceptions:
            return

        with self.logging_lock:
            print '-'*40
            print 'Exception happened during processing of request'
            traceback.print_exc()
            print '-'*40

class ForwardingThread(threading.Thread):
    def __init__(self, src, dst, server):
        threading.Thread.__init__(self)
        self.daemon = True
        self.src = src
        self.dst = dst
        self.server = server

    def run(self):
        reader = connection_reader(self.src, self.server.config.buffer_size)

        try:
            if self.server.config.use_length_hiding_with_client:
                forward_connection_with_padding(reader, self.dst, *self.server.config.padding_range_with_client)
            else:
                forward_connection(reader, self.dst)
        except Exception:
            self.server.print_exc()
        else:
            self.say_bye_to_dst()

        self.close_dst()

    def say_bye_to_dst(self):
        try:
            self.dst.bye()
        except:
            pass

    def close_dst(self):
        try:
            self.dst.shutdown()
        except:
            pass

        self.dst.close()

class MITMHandler(SocketServer.BaseRequestHandler):
    def setup(self):
        self.handshake_with_client()

        if self.server.config.verify_client_identity:
            self.verify_client_identity()

        if self.server.config.use_length_hiding_with_client and not self.session.can_use_length_hiding():
            raise RuntimeError("Can't use length hiding with client")

        self.build_server_connection()

        if self.server.config.verify_server_identity:
            self.verify_server_identity()

        if self.server.config.use_length_hiding_with_server and not self.remote.can_use_length_hiding():
            raise RuntimeError("Can't use length hiding with server")

        self.start_forwarding_thread()

    def handshake_with_client(self):
        self.session = LengthHidingServerSession(self.request, self.server.server_context)
        self.session.handshake()

    def build_server_connection(self):
        server_address = self.server.config.server_address
        use_proxy = hasattr(self.server.config, "proxy") and self.server.config.proxy is not None
        is_ipv6 = is_ipv6_address(server_address)

        if use_proxy:
            import socks
            socket_builder = socks.socksocket
        else:
            socket_builder = socket.socket

        if is_ipv6:
            sock = socket_builder(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            sock = socket_builder(socket.AF_INET, socket.SOCK_STREAM)

        if use_proxy:
            proxy_type = socks.PROXY_TYPES[self.server.config.proxy[0]]
            sock.set_proxy(proxy_type, *self.server.config.proxy[1:])

        self.remote = LengthHidingClientSession(sock, self.server.client_context, self.server.config.server_name_indicator)

        if is_ipv6:
            self.remote.connect((server_address[0][1:-1],) + server_address[1:])
        else:
            self.remote.connect(server_address)

        self.remote.handshake()

    def verify_client_identity(self):
        self.session.verify_peer()
        self.server.config.credentials_as_server.check_certificate(self.session.peer_certificate)

    def verify_server_identity(self):
        self.remote.verify_peer()
        self.server.config.credentials_as_client.check_certificate(self.remote.peer_certificate)

    def start_forwarding_thread(self):
        self.forwarding_thread = ForwardingThread(self.remote, self.session, self.server)
        self.forwarding_thread.start()

    def handle(self):
        reader = connection_reader(self.session, self.server.config.buffer_size)

        try:
            if self.server.config.use_length_hiding_with_server:
                forward_connection_with_padding(reader, self.remote, *self.server.config.padding_range_with_server)
            else:
                forward_connection(reader, self.remote)
        except ReaderError:
            self.say_bye_to_remote()
            self.close_remote()
            raise
        except WriterError:
            self.close_remote()
            self.say_bye_to_origin()
            raise
        else:
            self.say_bye_to_remote()
            self.close_remote()
            self.say_bye_to_origin()

    def say_bye_to_remote(self):
        try:
            self.remote.bye()
        except:
            pass

    def close_remote(self):
        try:
            self.remote.shutdown()
        except:
            pass

        self.remote.close()

    def say_bye_to_origin(self):
        try:
            self.session.bye()
        except:
            pass

class MITMSettings():
    def __init__(self, server_addr, listen_addr):
        self.server_address = server_addr
        self.listen_address = listen_addr

if __name__ == '__main__':
    from mesona.configuration import settings, default_settings

    servers = []
    threads = []

    def sigint_received(signum, frame):
        for server in servers:
            server.shutdown()

        sys.exit(0)

    for key, setting in settings.items():
        config = MITMSettings(setting["server_address"], setting["listen_address"])
        config.__dict__.update(default_settings)
        config.__dict__.update(setting)

        try:
            server = MITMServer(config)
        except Exception:
            print("Could not create proxy instance \"{}\":".format(key))
            traceback.print_exc()
            print("\n")
            continue
        else:
            print("Starting listener \"{}\" on {} which forwards to {}".format(
                key, config.listen_address, config.server_address))

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True

        thread.start()

        servers.append(server)
        threads.append(thread)

    if not servers:
        print("No proxy instance started, quitting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, sigint_received)

    try:
        while True:
            time.sleep(3600)
    except:
        pass
