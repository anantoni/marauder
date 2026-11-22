"""v11: FNames built by static initializers, including tail-called constructors and the
constructor of a static registry object called by one initializer (synthetic executable)."""
import os, struct, sys, tempfile, unittest
from test_ue_usmap import Img, BASE
from test_code_links import declare_crt, lea
from usmap_generator.engine.scan import Image
from usmap_generator.engine.genusmap import im_functions
from usmap_generator.engine.code_links import CodeLinks
from usmap_generator.engine import fname_order

TEXT = BASE + 0x1000


def code_at(im, va, code):
    im.buf['.text'][va - TEXT:va - TEXT + len(code)] = code
    return va + len(code)


def rel(op, pc, target):
    return op + struct.pack('<i', target - pc - 5)


def build(im, path):
    ctor_w, ctor_a = TEXT + 0xa00, TEXT + 0xa10
    code_at(im, ctor_w, b'\xc3'); code_at(im, ctor_a, b'\xc3')
    obj = im.va('.data', 0x100)
    alpha, low_min, low_max = im.wstr('Alpha'), im.cstr('min'), im.cstr('max')
    # callback 1: FName(TEXT("Alpha")) on a global, tail-called
    cb1 = TEXT + 0x300
    c = b'\x41\xb8\x01\x00\x00\x00'
    c += lea(2, cb1 + len(c), alpha); c += lea(1, cb1 + len(c), obj)
    c += rel(b'\xe9', cb1 + len(c), ctor_w); end1 = code_at(im, cb1, c + b'\xcc')
    # registry constructor: two narrow names; FNAME_Add from a zeroed register; an int3 inside
    reg = TEXT + 0x400
    c = b'\x53\x48\x83\xec\x20\x31\xc0\x44\x8d\x40\x01'
    c += lea(2, reg + len(c), low_min); c += b'\x48\x8d\x4c\x24\x30'
    c += rel(b'\xe8', reg + len(c), ctor_a) + b'\xcc'
    c += b'\x41\xb8\x01\x00\x00\x00'
    c += lea(2, reg + len(c), low_max); c += b'\x48\x8d\x4c\x24\x30'
    c += rel(b'\xe8', reg + len(c), ctor_a) + b'\x48\x83\xc4\x20\x5b\xc3'
    end_reg = code_at(im, reg, c)
    # callback 2: constructs the registry object
    cb2 = TEXT + 0x340
    c = b'\x48\x83\xec\x28' + lea(1, cb2 + 4, obj + 8)
    c += rel(b'\xe8', cb2 + len(c), reg) + b'\x48\x83\xc4\x28\xc3'
    end2 = code_at(im, cb2, c)
    pdata = b''.join(struct.pack('<III', a - BASE, b - BASE, 0) for a, b in
                     [(ctor_w, ctor_w + 1), (ctor_a, ctor_a + 1), (cb1, end1), (cb2, end2), (reg, end_reg)])
    im.buf['.pdata'][:len(pdata)] = pdata
    declare_crt(im, path, [cb1, cb2])


class StaticInitializerNames(unittest.TestCase):
    def test_tail_calls_and_registry_constructors(self):
        im = Img()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'fixture.exe'); build(im, path)
            image = Image(path); links = CodeLinks(image, im_functions(image))
            self.assertEqual(len(links.startup_callbacks()), 2)
            self.assertEqual(fname_order.static_names(image, links), [])       # v10 shape misses both
            self.assertEqual(fname_order.static_names_nested(image, links), ['Alpha', 'min', 'max'])

    def test_first_static_spelling_wins_over_reflection(self):
        order = fname_order.registration_order(['None'], ['min', 'Max'], {}, {}, {}, {}, {})
        self.assertEqual(order['min'][0], 'min')
        self.assertEqual(order['max'][0], 'Max')


if __name__ == '__main__':
    unittest.main()
