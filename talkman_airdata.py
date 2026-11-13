"""CXUS v2 framing. No device access, microphone or network side effects."""
import binascii
import struct
from collections import deque
from dataclasses import dataclass

HEADER = struct.Struct('<IHHIHHHH')
MAGIC = 0x53555843
AUDIO, CONTROL, BUTTONS, STATE, HELLO, PING, PONG = 16, 18, 19, 20, 21, 32, 33


def packet(kind, payload=b'', *, seq=0, tag=0, sub=0, priority=0):
    if kind >= 32 and len(payload) > 44:
        raise ValueError('CXUS control frame exceeds 64 bytes')
    return HEADER.pack(MAGIC, kind, priority & 1, len(payload), seq & 65535,
                       tag, sub, binascii.crc_hqx(payload, 0xffff))+payload


@dataclass(frozen=True)
class Frame:
    kind: int
    priority: int
    seq: int
    tag: int
    sub: int
    data: bytes


class Frames:
    """Bounded parser; recover at magic after a partial transfer or invalid CRC."""
    def __init__(self, limit=8192):
        self.buffer = bytearray()
        self.limit = limit
        self.stats = dict(crc_errors=0, invalid_headers=0, discarded_bytes=0)
        # Metadata only: error index, kind, priority, seq, payload size,
        # next magic offset. Never retain damaged audio or pairing payloads.
        self.crc_frames = deque(maxlen=4)

    def feed(self, chunk):
        self.buffer.extend(chunk)
        result = []
        while len(self.buffer) >= HEADER.size:
            start = self.buffer.find(b'CXUS')
            if start < 0:
                self.stats['discarded_bytes'] += len(self.buffer)-3
                del self.buffer[:-3]
                break
            if start:
                self.stats['discarded_bytes'] += start
                del self.buffer[:start]
            if len(self.buffer) < HEADER.size:
                break
            _, kind, flags, size, seq, tag, sub, crc = HEADER.unpack_from(self.buffer)
            if (kind not in (*range(16, 32), PONG) or flags & ~1 or
                    size > self.limit or (kind >= 32 and size > 44)):
                self.stats['invalid_headers'] += 1
                self.stats['discarded_bytes'] += 1
                del self.buffer[0]
                continue
            end = HEADER.size+size
            if len(self.buffer) < end:
                break
            data = bytes(self.buffer[HEADER.size:end])
            if crc and binascii.crc_hqx(data, 0xffff) != crc:
                self.stats['crc_errors'] += 1
                self.crc_frames.append((self.stats['crc_errors'], kind, flags & 1,
                                        seq, size, self.buffer.find(b'CXUS', HEADER.size)))
                self.stats['discarded_bytes'] += 1
                del self.buffer[0]
                continue
            del self.buffer[:end]
            result.append(Frame(kind, flags & 1, seq, tag, sub, data))
        return result


def opus_units(payload):
    """Validate the entire message before returning any of its audio packets."""
    units, pos = [], 0
    while pos < len(payload):
        if pos+2 > len(payload):
            raise ValueError('Incomplete Opus packet length')
        length = int.from_bytes(payload[pos:pos+2], 'big')
        pos += 2
        if not 0 < length <= 1275 or pos+length > len(payload):
            raise ValueError('Invalid Opus packet length')
        units.append(payload[pos:pos+length])
        pos += length
    if not units:
        raise ValueError('Empty Opus audio')
    return units
