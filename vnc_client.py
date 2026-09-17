# Minimal RFB (VNC) client for input control + framebuffer capture.
# Handshake -> None/VNC auth -> share -> pixel format -> raw framebuffer grab
# -> PNG for OCR, plus PointerEvent/KeyEvent injection.
import socket
import struct
import time

HOST = "caboose.proxy.rlwy.net"
PORT = 44507


class VNC:
    def __init__(self, host=HOST, port=PORT, password=None):
        self.s = socket.create_connection((host, port), timeout=30)
        self.s.settimeout(30)
        banner = self._recv_exact(12)
        assert banner.startswith(b"RFB "), f"not VNC: {banner!r}"
        self.server_ver = banner
        # we advertise 3.8
        self.s.sendall(b"RFB 003.008\n")
        nsec = struct.unpack(">B", self._recv_exact(1))[0]
        types = self._recv_exact(nsec)
        if 2 in types:  # VNC auth
            self.s.sendall(struct.pack(">B", 2))
            challenge = self._recv_exact(16)
            pw = (password or "").encode()[:8].ljust(8, b"\x00")
            key = bytes(int(f"{b:08b}"[::-1], 2) for b in pw)
            from Crypto.Cipher import DES
            resp = DES.new(key, DES.MODE_ECB).encrypt(challenge)
            self.s.sendall(resp)
            sec_result = struct.unpack(">I", self._recv_exact(4))[0]
            if sec_result != 0:
                raise RuntimeError(f"VNC auth failed (wrong password?)")
        else:
            raise RuntimeError(f"no supported security type in {list(types)}")
        self.s.sendall(struct.pack(">B", 1))  # share
        # ServerInit
        si = self._recv_exact(24)
        self.width, self.height = struct.unpack(">HH", si[:4])
        self.bpp, self.depth, self.bigendian, self.truecol = struct.unpack(">BBBB", si[4:8])
        self.rmax, self.gmax, self.bmax = struct.unpack(">HHH", si[8:14])
        self.rsh, self.gsh, self.bsh = struct.unpack(">BBB", si[14:17])
        self.name_len = struct.unpack(">I", si[20:24])[0]
        self.name = self._recv_exact(self.name_len).decode("utf-8", "replace")
        # NOTE: x11vnc closes the connection if we push our own SetPixelFormat;
        # keep the server default (32bpp BGRX here) and just pick encodings.
        self.s.sendall(struct.pack(">BxH", 2, 1) + struct.pack(">i", 0))
        self.bytes_pp = self.bpp // 8
        self.pixels = bytearray(self.width * self.height * self.bytes_pp)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def request_full_update(self):
        # FramebufferUpdateRequest (incremental=0)
        self.s.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))

    def read_framebuffer(self, wait_s=2.0):
        self.request_full_update()
        end = time.time() + wait_s
        got = False
        self.s.settimeout(0.5)
        while time.time() < end:
            try:
                msg = self._recv_exact(1)
            except (socket.timeout, TimeoutError):
                if got:
                    break
                self.request_full_update()
                continue
            mtype = msg[0]
            if mtype == 0:  # FramebufferUpdate
                self._recv_exact(1)
                nrect = struct.unpack(">H", self._recv_exact(2))[0]
                for _ in range(nrect):
                    x, y, w, h = struct.unpack(">HHHH", self._recv_exact(8))
                    enc = struct.unpack(">i", self._recv_exact(4))[0]
                    if enc == 0:  # raw
                        data = self._recv_exact(w * h * 4)
                        self._blit(x, y, w, h, data)
                        got = True
                    elif enc == -224:  # pseudo cursor
                        self._recv_exact(w * h * 4 + int((w + 7) // 8) * ((h + 7) // 8))
                    else:
                        raise RuntimeError(f"unsupported encoding {enc}")
            elif mtype == 1:  # SetColourMapEntries
                self._recv_exact(4)
                n = struct.unpack(">H", self._recv_exact(2))[0]
                self._recv_exact(n * 6)
            elif mtype == 2:  # bell
                pass
            elif mtype == 3:  # server cut text
                ln = struct.unpack(">I", self._recv_exact(4))[0]
                self._recv_exact(ln)
        self.s.settimeout(30)
        return got

    def _blit(self, x, y, w, h, data):
        stride = self.width * self.bytes_pp
        for row in range(h):
            off = ((y + row) * stride) + x * self.bytes_pp
            self.pixels[off:off + w * self.bytes_pp] = data[row * w * self.bytes_pp:(row + 1) * w * self.bytes_pp]

    def to_png(self, path):
        from PIL import Image
        mode = "RGBX" if self.bytes_pp == 4 else "RGB"
        img = Image.frombytes(mode, (self.width, self.height), bytes(self.pixels), "raw", "BGRX" if self.bytes_pp == 4 else "BGR")
        img.convert("RGB").save(path)
        return path

    # ---- input ----
    def pointer(self, x, y, button_mask=0):
        self.s.sendall(struct.pack(">BBHHB", 4, 0, x, y, button_mask))

    def click(self, x, y, button=1):
        self.pointer(x, y, 0)
        time.sleep(0.05)
        self.pointer(x, y, button)
        time.sleep(0.06)
        self.pointer(x, y, 0)

    def client_cut_text(self, text):
        data = text.encode("latin-1", "replace")
        self.s.sendall(struct.pack(">BxxxI", 6, len(data)) + data)

    def paste_to_terminal(self, text):
        """Set the desktop clipboard; user/terminal pastes with Ctrl+Shift+V."""
        self.client_cut_text(text)

    def key(self, keysym, down):
        self.s.sendall(struct.pack(">BBxxII", 4, down, 0, keysym))

    def type_text(self, text):
        for ch in text:
            ks = self._keysym(ch)
            if ks:
                self.key(ks, 1)
                self.key(ks, 0)
                time.sleep(random.uniform(0.03, 0.09))

    @staticmethod
    def _keysym(ch):
        # latin-1 maps directly for ASCII; extend as needed
        o = ord(ch)
        if 32 <= o <= 126:
            return o
        return None

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


import random  # noqa: E402  (used by type_text)
