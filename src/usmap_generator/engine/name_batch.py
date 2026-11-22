"""Packed name tables used by UE5 cooked archives."""
import struct


def read_name_batch(data, start=0):
    count, = struct.unpack_from("<I", data, start)
    if not count:
        return [], start + 4
    size, = struct.unpack_from("<I", data, start + 4)
    headers = start + 16 + 8 * count
    offset = headers + 2 * count
    end = offset + size
    if end > len(data):
        raise ValueError("truncated name batch")
    names = []
    for pos in range(headers, offset, 2):
        header = int.from_bytes(data[pos:pos + 2], "big")
        wide = bool(header & 0x8000)
        length = header & 0x7FFF
        stop = offset + length * (2 if wide else 1)
        if not 0 < length < 1024 or stop > end:
            raise ValueError("invalid name length")
        names.append(data[offset:stop].decode("utf-16-le" if wide else "latin-1", "replace"))
        offset = stop
    if offset != end:
        raise ValueError("invalid name batch size")
    return names, end


def numbered_name(names, index, number):
    if index >= len(names) or number > 0x7fffffff:
        raise ValueError("invalid serialized name")
    name = names[index]
    return f"{name}_{number - 1}" if number else name
