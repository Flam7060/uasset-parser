"""
Ограниченное чтение little-endian бинарных данных.

Каждое чтение проверяет границы окна [start, end): битый оффсет в таблице
экспортов не должен молча читать соседний объект — вместо этого ParseError
с абсолютной позицией в файле, по которой ошибку можно найти в hex-редакторе.
"""
import struct


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, data, start=0, end=None):
        self.data = data
        self.start = start
        self.end = len(data) if end is None else end
        if not (0 <= self.start <= self.end <= len(data)):
            raise ParseError(f"окно [{start:#x}, {end:#x}) вне файла размером {len(data):#x}")
        self.pos = start

    def sub(self, size):
        """Дочерний ридер на следующие size байт; родитель сдвигается за них."""
        self._need(size, "под-окно")
        child = Reader(self.data, self.pos, self.pos + size)
        self.pos += size
        return child

    def tell(self):
        return self.pos

    def seek(self, pos):
        if not (self.start <= pos <= self.end):
            raise ParseError(f"seek {pos:#x} вне окна [{self.start:#x}, {self.end:#x})")
        self.pos = pos

    def remaining(self):
        return self.end - self.pos

    def at_end(self):
        return self.pos >= self.end

    def _need(self, n, what):
        if n < 0 or self.pos + n > self.end:
            raise ParseError(
                f"{what}: нужно {n} байт @ {self.pos:#x}, до конца окна {self.end - self.pos}")

    def _unpack(self, fmt, size, what):
        self._need(size, what)
        v = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return v

    def u8(self):  return self._unpack('<B', 1, "u8")
    def i8(self):  return self._unpack('<b', 1, "i8")
    def u16(self): return self._unpack('<H', 2, "u16")
    def i16(self): return self._unpack('<h', 2, "i16")
    def u32(self): return self._unpack('<I', 4, "u32")
    def i32(self): return self._unpack('<i', 4, "i32")
    def u64(self): return self._unpack('<Q', 8, "u64")
    def i64(self): return self._unpack('<q', 8, "i64")
    def f32(self): return self._unpack('<f', 4, "f32")
    def f64(self): return self._unpack('<d', 8, "f64")

    def bytes(self, n):
        self._need(n, "bytes")
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def guid(self):
        # FGuid — четыре u32 A,B,C,D; печатаем как UE (%08X%08X%08X%08X)
        a, b, c, d = struct.unpack('<4I', self.bytes(16))
        return f"{a:08X}{b:08X}{c:08X}{d:08X}"

    def fstring(self):
        """FString: i32 длина с терминатором; отрицательная — UTF-16."""
        at = self.pos
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            raw = self.bytes(length)
            if raw[-1] != 0:
                raise ParseError(f"FString @ {at:#x} без нулевого терминатора")
            return raw[:-1].decode('latin-1')
        if length == -0x80000000:
            raise ParseError(f"FString @ {at:#x}: длина INT_MIN")
        raw = self.bytes(-length * 2)
        if raw[-2:] != b'\0\0':
            raise ParseError(f"FString(UTF-16) @ {at:#x} без нулевого терминатора")
        return raw[:-2].decode('utf-16-le', errors='replace')

    def cstring(self):
        """ANSI-строка до нуля (так хранит EX_StringConst)."""
        nul = self.data.find(b'\0', self.pos, self.end)
        if nul < 0:
            raise ParseError(f"C-строка @ {self.pos:#x} без терминатора до конца окна")
        v = self.data[self.pos:nul].decode('latin-1')
        self.pos = nul + 1
        return v

    def wcstring(self):
        """UTF-16 строка до u16 нуля (EX_UnicodeStringConst)."""
        at = self.pos
        chars = []
        while True:
            if self.pos + 2 > self.end:
                raise ParseError(f"UTF-16 C-строка @ {at:#x} без терминатора")
            ch = struct.unpack_from('<H', self.data, self.pos)[0]
            self.pos += 2
            if ch == 0:
                break
            chars.append(ch)
        return struct.pack(f'<{len(chars)}H', *chars).decode('utf-16-le', errors='replace')
