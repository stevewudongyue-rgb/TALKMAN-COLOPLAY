"""Small libopus adapter; only opens the bundled library, never an audio device."""
import ctypes as C
from pathlib import Path
import sys


def library_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))/'TalkMan-assets/native'
    name = 'windows-x64/opus.dll' if sys.platform == 'win32' else 'mac-arm64/libopus.0.dylib'
    return root/name


class Decoder:
    def __init__(self):
        self.handle = None
        try:
            self.lib = C.CDLL(str(library_path()))
        except OSError:
            raise RuntimeError('设备麦克风解码组件缺失，请安装完整版本。') from None
        lib = self.lib
        lib.opus_decoder_create.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_int)]
        lib.opus_decoder_create.restype = C.c_void_p
        lib.opus_decoder_destroy.argtypes = [C.c_void_p]
        lib.opus_decode.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.POINTER(C.c_int16), C.c_int, C.c_int]
        lib.opus_decode.restype = C.c_int
        error = C.c_int()
        self.handle = lib.opus_decoder_create(16000, 1, C.byref(error))
        if not self.handle or error.value:
            raise RuntimeError('无法初始化设备麦克风解码器。')
        self.pcm = (C.c_int16*1920)()  # Opus permits up to 120 ms.

    def decode(self, data):
        frames = self.lib.opus_decode(self.handle, data, len(data), self.pcm, 1920, 0)
        if frames != 960:
            raise ValueError('设备音频不是约定的 16 kHz／60 ms 格式。')
        return C.string_at(self.pcm, frames*2)

    def close(self):
        if self.handle:
            self.lib.opus_decoder_destroy(self.handle)
            self.handle = None
