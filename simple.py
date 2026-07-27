# umqtt.simple oficial de MicroPython
import usocket as socket
import ustruct as struct
from ubinascii import hexlify

class MQTTException(Exception):
    pass

class MQTTClient:
    def __init__(self, client_id, server, port=0, user=None, password=None, keepalive=0, ssl=False, ssl_params={}):
        if port == 0:
            port = 8883 if ssl else 1883
        self.client_id = client_id
        self.sock = None
        self.server = server
        self.port = port
        self.ssl = ssl
        self.ssl_params = ssl_params
        self.pid = 0
        self.cb = None
        self.user = user
        self.pswd = password
        self.keepalive = keepalive
        self.lw_topic = None
        self.lw_msg = None
        self.lw_qos = 0
        self.lw_retain = False

    def _readexactly(self, n):
        """Lee exactamente n bytes del socket, reintentando si TCP entrega el payload fragmentado."""
        buf = b""
        while len(buf) < n:
            chunk = self.sock.read(n - len(buf))
            if not chunk:
                raise MQTTException("Socket cerrado al leer payload")
            buf += chunk
        return buf

    def _send_str(self, s):
        if isinstance(s, str):
            s = s.encode("utf-8")
        self.sock.write(struct.pack("!H", len(s)))
        self.sock.write(s)

    def _recv_len(self):
        n = 0
        sh = 0
        while 1:
            res = self.sock.read(1)
            if res is None or len(res) == 0:
                raise MQTTException(1)
            b = res[0]
            n |= (b & 0x7F) << sh
            if not (b & 0x80):
                return n
            sh += 7

    def set_callback(self, cb):
        self.cb = cb

    def set_last_will(self, topic, msg, retain=False, qos=0):
        assert qos in (0, 1)
        self.lw_topic = topic
        self.lw_msg = msg
        self.lw_retain = retain
        self.lw_qos = qos

    def connect(self, clean_session=True):
        self.sock = socket.socket()
        self.sock.settimeout(3.0)
        addr = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock.connect(addr)
        if self.ssl:
            import ussl
            self.sock = ussl.wrap_socket(self.sock, **self.ssl_params)
        premsg = bytearray(b"\x10\0\0\4MQTT\x04\x02\0\0")
        msg = bytearray()
        if clean_session:
            premsg[9] |= 0x02
        if self.user is not None:
            premsg[9] |= 0x80
            msg += struct.pack("!H", len(self.user)) + self.user.encode("utf-8")
            if self.pswd is not None:
                premsg[9] |= 0x40
                msg += struct.pack("!H", len(self.pswd)) + self.pswd.encode("utf-8")
        if self.keepalive:
            assert self.keepalive < 65536
            premsg[10] |= self.keepalive >> 8
            premsg[11] |= self.keepalive & 0xFF
        if self.lw_topic is not None:
            premsg[9] |= 0x04
            if self.lw_retain:
                premsg[9] |= 0x20
            premsg[9] |= self.lw_qos << 3
            msg += struct.pack("!H", len(self.lw_topic)) + self.lw_topic.encode("utf-8")
            msg += struct.pack("!H", len(self.lw_msg)) + self.lw_msg.encode("utf-8")
        premsg[1] = len(premsg) - 2 + len(msg) + len(self.client_id) + 2
        self.sock.write(premsg)
        self._send_str(self.client_id)
        self.sock.write(msg)
        res = self.sock.read(4)
        if res is None or len(res) < 4:
            raise MQTTException(2)
        assert res[0] == 0x20 and res[1] == 0x02
        if res[3] != 0:
            raise MQTTException(res[3])
        return res[2] & 1

    def disconnect(self):
        self.sock.write(b"\xe0\0")
        self.sock.close()
        self.sock = None

    def ping(self):
        self.sock.write(b"\xc0\0")

    def publish(self, topic, msg, retain=False, qos=0):
        if isinstance(topic, str):
            topic = topic.encode("utf-8")
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        pkt = bytearray()
        pkt.append(0x30 | (qos << 1) | retain)
        sz = len(topic) + 2 + len(msg)
        if qos:
            self.pid += 1
            sz += 2
        while sz > 0x7F:
            pkt.append((sz & 0x7F) | 0x80)
            sz >>= 7
        pkt.append(sz)
        pkt.append(len(topic) >> 8)
        pkt.append(len(topic) & 0xFF)
        pkt.extend(topic)
        if qos:
            pkt.append(self.pid >> 8)
            pkt.append(self.pid & 0xFF)
        pkt.extend(msg)
        self.sock.write(pkt)

    def subscribe(self, topic, qos=0):
        if isinstance(topic, str):
            topic = topic.encode("utf-8")
        pkt = bytearray(b"\x82\0\0\0")
        self.pid += 1
        struct.pack_into("!BH", pkt, 1, 2 + 2 + len(topic) + 1, self.pid)
        self.sock.write(pkt)
        self._send_str(topic)
        self.sock.write(struct.pack("B", qos))
        res = self.sock.read(5)
        if len(res) < 5:
            raise MQTTException("Respuesta de suscripción inválida o incompleta")
        assert res[0] == 0x90 and res[1] == 0x03
        assert struct.unpack("!H", res[2:4])[0] == self.pid
        assert res[4] == qos

    def wait_msg(self):
        res = self.sock.read(1)
        if res is None or len(res) == 0:
            raise MQTTException(3)
        if res == b"\xd0":
            self.sock.read(1)
            return
        op = res[0]
        if op & 0xF0 != 0x30:
            return op
        sz = self._recv_len()
        topic_len = self._readexactly(2)
        topic_len = (topic_len[0] << 8) | topic_len[1]
        topic = self._readexactly(topic_len)
        sz -= topic_len + 2
        if op & 0x06:
            pid = self._readexactly(2)
            pid = (pid[0] << 8) | pid[1]
            sz -= 2
        msg = self._readexactly(sz)
        self.cb(topic, msg)
        if op & 0x06 == 2:
            pkt = bytearray(b"0\x02\0\0")
            struct.pack_into("!H", pkt, 2, pid)
            self.sock.write(pkt)
        elif op & 0x06 == 4:
            assert 0

    def check_msg(self):
        self.sock.setblocking(False)
        try:
            res = self.sock.read(1)
        except OSError as e:
            # EAGAIN
            if e.args[0] == 11:
                self.sock.settimeout(3.0)
                return
            self.sock.settimeout(3.0)
            raise
        self.sock.settimeout(3.0)
        if res is None or len(res) == 0:
            return
        if res == b"\xd0":
            self.sock.read(1)
            return
        op = res[0]
        if op & 0xF0 != 0x30:
            return op
        sz = self._recv_len()
        topic_len = self._readexactly(2)
        topic_len = (topic_len[0] << 8) | topic_len[1]
        topic = self._readexactly(topic_len)
        sz -= topic_len + 2
        if op & 0x06:
            pid = self._readexactly(2)
            pid = (pid[0] << 8) | pid[1]
            sz -= 2
        msg = self._readexactly(sz)
        self.cb(topic, msg)
        if op & 0x06 == 2:
            pkt = bytearray(b"0\x02\0\0")
            struct.pack_into("!H", pkt, 2, pid)
            self.sock.write(pkt)
        elif op & 0x06 == 4:
            assert 0
