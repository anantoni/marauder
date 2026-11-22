import os,struct,tempfile,unittest
from pathlib import Path
from test_ue_usmap import Img,BASE
from test_code_links import emit_struct_constructor,unwrapped
from usmap_generator.engine.scan import Image
from usmap_generator.engine.genusmap import im_functions
from usmap_generator.engine.code_links import CodeLinks
from usmap_generator.engine.compiler_abi import preserves_return_register

def fixture(path,*,returning_failure=False,foreign_module=False,bad_handle=False,modified_return=False,protected=False):
    im=Img();cookie=im.put('.data',struct.pack('<Q',0x123456789abc))
    names=[]
    for symbol in ('GetCurrentProcess','TerminateProcess'):
        names.append(im.put('.rdata',b'\0\0'+symbol.encode()+b'\0',2)-BASE)
    original=im.put('.rdata',struct.pack('<QQQ',*names,0));iat=im.put('.rdata',struct.pack('<QQQ',*names,0))
    module=im.cstr('synthetic.dll' if foreign_module else 'kernel32.dll')
    descriptor=im.put('.rdata',struct.pack('<IIIII',original-BASE,0,0,module-BASE,iat-BASE)+bytes(20))
    parameter=im.put('.data',bytes(64));slot=im.put('.data',bytes(64))
    emit_struct_constructor(im,BASE+0x1200,parameter,slot)
    root=BASE+0x1100;helper=BASE+0x1a00;failure=BASE+0x1b00;terminal=BASE+0x1d00
    code=b'\x48\x83\xec\x28\xe8'+struct.pack('<i',BASE+0x1200-root-9)
    code+=b'\xe8'+struct.pack('<i',helper-root-len(code)-5)+b'\x48\x83\xc4\x28\xc3'
    im.buf['.text'][0x100:0x100+len(code)]=code
    code=bytearray(b'\x48\x3b\x0d'+struct.pack('<i',cookie-helper-7))
    first=len(code);code+=b'\x75\0\x48\xc1\xc1\x10\x66\xf7\xc1\xff\xff'
    second=len(code);code+=b'\x75\0'
    code+=b'\x31\xc0\xc3' if modified_return else b'\xc3'
    restore=len(code);code+=b'\x48\xc1\xc9\x10';jump=len(code)
    code+=b'\xe9'+struct.pack('<i',failure-helper-len(code)-5)
    code[first+1]=jump-first-2;code[second+1]=restore-second-2
    im.buf['.text'][0xa00:0xa00+len(code)]=code
    # Either fail-fast directly, or enter the verified current-process terminator.
    code=b'\x85\xd2\x74\x06\xe8'+struct.pack('<i',terminal-failure-9)+b'\xc3'
    code+=b'\xc3' if returning_failure else b'\xcd\x29'
    im.buf['.text'][0xb00:0xb00+len(code)]=code
    code=b'\xff\x15'+struct.pack('<i',iat-terminal-6)
    code+=b'\x48\xc7\xc1\x01\x00\x00\x00' if bad_handle else b'\x48\x89\xc1'
    code+=b'\xba\x09\x04\x00\xc0'
    code+=b'\xff\x25'+struct.pack('<i',iat+8-terminal-len(code)-6)
    im.buf['.text'][0xd00:0xd00+len(code)]=code
    if protected:im.build(path)
    else:unwrapped(im,path)
    raw=bytearray(Path(path).read_bytes());struct.pack_into('<II',raw,0x98+120,descriptor-BASE,40);Path(path).write_bytes(raw)
    image=Image(path);return CodeLinks(image,im_functions(image)),root,helper,parameter

class CompilerABI(unittest.TestCase):
    def test_cookie_helper_preserves_the_constructor_identity_on_its_only_returning_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,root,helper,parameter=fixture(os.path.join(tmp,'fixture.exe'))
            self.assertTrue(preserves_return_register(linker,helper))
            self.assertEqual(linker.constructor_id(root,{parameter:None}),parameter)
            self.assertGreater(linker.stats['compiler_preserved_return_calls'],0)

    def test_returning_failure_modified_register_foreign_import_or_arbitrary_handle_is_not_a_proof(self):
        for kwargs in ({'returning_failure':True},{'modified_return':True},{'foreign_module':True},{'bad_handle':True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as tmp:
                linker,root,helper,parameter=fixture(os.path.join(tmp,'fixture.exe'),**kwargs)
                self.assertFalse(preserves_return_register(linker,helper))
                self.assertIsNone(linker.constructor_id(root,{parameter:None}))

    def test_protected_inputs_do_not_read_cookie_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,root,helper,parameter=fixture(os.path.join(tmp,'fixture.exe'),protected=True)
            self.assertFalse(preserves_return_register(linker,helper))
            self.assertEqual(linker.stats['code_bytes_read'],0)

if __name__=='__main__':unittest.main()
