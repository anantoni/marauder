"""Read-only PE helper: maps VAs to file offsets for the data sections. Never touches .text contents."""
import struct

class PE:
    DATA_SECTIONS = frozenset((".rdata", ".data", ".pdata"))

    def __init__(self, path):
        self.path=path
        # Keep raw offsets usable by the scanner, but never even load executable sections.
        # In particular, a whole-file version-string search must not inspect .text or .bind.
        with open(path, "rb") as f:
            dos = f.read(0x40)
            if len(dos) != 0x40 or dos[:2] != b"MZ": raise ValueError("not a PE executable")
            pe = struct.unpack_from("<I", dos, 0x3C)[0]
            f.seek(pe)
            coff = f.read(24)
            if len(coff) != 24 or coff[:4] != b"PE\0\0" or struct.unpack_from("<H", coff, 4)[0] != 0x8664:
                raise ValueError("not an x64 PE executable")
            n = struct.unpack_from("<H", coff, 6)[0]
            opt = struct.unpack_from("<H", coff, 20)[0]
            optional = f.read(opt)
            if len(optional) < 32 or struct.unpack_from("<H", optional)[0] != 0x20B:
                raise ValueError("not a PE32+ executable")
            self.base = struct.unpack_from("<Q", optional, 24)[0]
            self.entrypoint = self.base + struct.unpack_from('<I', optional, 16)[0]
            self.import_directory = struct.unpack_from('<II',optional,120) if len(optional)>=128 else (0,0)
            table = f.read(40 * n)
            self.secs = {}
            for i in range(n):
                name, vs, va, rs, ra = struct.unpack_from("<8sIIII", table, 40 * i)
                self.secs[name.rstrip(b"\0").decode()] = (va, vs, ra, rs)
            end = max((ra + rs for name, (_, _, ra, rs) in self.secs.items()
                       if name in self.DATA_SECTIONS), default=0)
            self.d = bytearray(end)
            for name, (_, _, ra, rs) in self.secs.items():
                if name in self.DATA_SECTIONS:
                    f.seek(ra)
                    data = f.read(rs)
                    if len(data) != rs: raise ValueError("truncated PE data section")
                    self.d[ra:ra + rs] = data

    def sec(self, name):
        va, vs, ra, rs = self.secs[name]
        return self.base + va, self.base + va + min(vs, rs), ra

    def off(self, va):
        for name, (sva, vs, ra, rs) in self.secs.items():
            a = self.base + sva
            if a <= va < a + min(vs, rs):
                return ra + (va - a), name
        return None, None

    def in_sec(self, va, name):
        lo, hi, _ = self.sec(name)
        return lo <= va < hi

    def q(self, va):
        o, name = self.off(va)
        if name not in self.DATA_SECTIONS: raise ValueError("not a data address")
        return struct.unpack_from("<Q", self.d, o)[0]

    def cstr(self, va, maxlen=256):
        o, name = self.off(va)
        if o is None or name not in (".rdata", ".data"):
            return None
        e = self.d.find(b"\0", o, o + maxlen)
        if e < 0:
            return None
        s = self.d[o:e]
        try:
            return s.decode("ascii")
        except UnicodeDecodeError:
            return None

    def imports(self):
        """IAT slot -> declared import name; read only retained data sections."""
        self.import_modules={}
        rva,size=self.import_directory
        if not rva or not 20<=size<=1024*1024:return {}
        out={}
        for offset in range(0,min(size,4096*20),20):
            pos,section=self.off(self.base+rva+offset)
            if section not in ('.rdata','.data') or pos+20>len(self.d):return {}
            original,stamp,chain,name,iat=struct.unpack_from('<IIIII',self.d,pos)
            if not any((original,stamp,chain,name,iat)):return out
            if not original or not iat:return {}
            module=self.cstr(self.base+name)
            if not module:return {}
            for i in range(65536):
                pos,section=self.off(self.base+original+8*i)
                if section not in ('.rdata','.data') or pos+8>len(self.d):return {}
                hint=struct.unpack_from('<Q',self.d,pos)[0]
                if not hint:break
                if hint>>63:continue
                symbol=self.cstr(self.base+hint+2)
                if symbol:
                    out[self.base+iat+8*i]=symbol
                    self.import_modules[self.base+iat+8*i]=module.lower()
            else:return {}
        return {}
