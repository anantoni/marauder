import os,struct,tempfile,unittest
from test_ue_usmap import Img,BASE
from test_code_links import unwrapped,emit_struct_constructor
from usmap_generator.engine.scan import Image
from usmap_generator.engine.code_links import CodeLinks
from usmap_generator.engine.genusmap import im_functions
from usmap_generator.engine.enum_maximum import discover,maximum,reducer,graph,inline_reducer

def synthetic_reducer(flags):
    """Our own two-path integer reducer, assembled without any input executable."""
    data=bytearray();labels={};fixups=[]
    def emit(value):data.extend(bytes.fromhex(value))
    def label(name):labels[name]=len(data)
    def jump(op,name):emit(op);fixups.append((len(data),name));emit('00')
    emit('48 63 51 48 4c 8b 41 40 31 c0 85 d2');jump('74','return')
    if flags:
        emit('f6 41 51 01');jump('74','plain')
        label('bits');emit('4d 8b 48 08 4d 8d 51 ff 4d 21 ca 49 09 c1 4d 85 d2 4c 0f 45 c8 4c 89 c8 49 83 c0 10 48 ff ca')
        jump('75','bits');jump('eb','return')
    label('plain');emit('49 8b 40 08 48 ff ca');jump('74','return')
    label('largest');emit('49 83 c0 10 49 8b 48 08 48 39 c1 48 0f 4e c8 48 89 c8 48 ff ca')
    jump('75','largest');label('return');emit('c3')
    for pos,name in fixups:struct.pack_into('<b',data,pos,labels[name]-pos-1)
    return bytes(data)

def synthetic_inline():
    data=bytearray();labels={};fixups=[]
    def emit(value):data.extend(bytes.fromhex(value))
    def label(name):labels[name]=len(data)
    def jump(op,name):emit(op);fixups.append((len(data),name));emit('00')
    emit('48 89 d3 41 0f b6 f1 45 31 e4 4c 63 53 08 45 85 d2');jump('75','nonempty')
    emit('4c 89 e6');jump('eb','store')
    label('nonempty');emit('4c 8b 03 4d 89 d1 40 f6 c6 01');jump('74','plain')
    emit('49 c1 e1 04 4c 89 e2 4d 01 c1')
    label('bits');emit('49 8b 48 08 48 8d 41 ff 48 21 c8 48 09 d1 48 85 c0 48 0f 45 ca 49 83 c0 10 48 89 ca 4d 39 c8')
    jump('75','bits');jump('eb','join')
    label('plain');emit('49 8b 50 08 49 ff c9');jump('74','join')
    label('largest');emit('49 83 c0 10 49 8b 40 08 48 39 d0 48 0f 4e c2 48 89 c2 49 ff c9')
    jump('75','largest');label('join');emit('48 8d 72 01')
    label('store');emit('4a 89 34 d7 c3')
    for pos,name in fixups:struct.pack_into('<b',data,pos,labels[name]-pos-1)
    return bytes(data)

def fixture(path,flags=False,wrong_table=False,inline=False):
    im=Img();record=im.put('.data',bytes(64));slot=im.put('.data',bytes(128))
    emit_struct_constructor(im,BASE+0x1200,record,slot)
    table=bytearray(0x300);struct.pack_into('<Q',table,0x2e0,BASE+(0x1700 if wrong_table else 0x1a00))
    vt=im.put('.rdata',table)
    ctor=BASE+0x1b00;code=b'\x48\x8d\x05'+struct.pack('<i',vt-ctor-7)+b'\x48\x89\x01\x48\x89\xc8\xc3'
    im.buf['.text'][0xb00:0xb00+len(code)]=code
    helper=BASE+0x1c00;code=b'\xe8'+struct.pack('<i',ctor-helper-5)+b'\x48\x8b\x00\xff\x90\xe0\x02\x00\x00\xc3'
    im.buf['.text'][0xc00:0xc00+len(code)]=code
    method=BASE+0x1a00;code=b'\xe8'+struct.pack('<i',BASE+0x1900-method-5)+b'\xc3'
    if inline:code=synthetic_inline()
    im.buf['.text'][0xa00:0xa00+len(code)]=code
    code=synthetic_reducer(flags);im.buf['.text'][0x900:0x900+len(code)]=code
    im.buf['.text'][0x700]=0xc3
    unwrapped(im,path);image=Image(path);linker=CodeLinks(image,im_functions(image))
    linker.constructor_id(BASE+0x1200,{record:None})
    return linker,{record:None}

class EnumMaximum(unittest.TestCase):
    def test_flag_union_ignores_composites_and_is_not_a_next_power_of_two_rule(self):
        entries=[('A',1),('B',8),('Composite',5)]
        self.assertEqual(maximum(entries,True,'single-bit union for flags'),10)
        self.assertEqual(maximum(entries,True,'signed maximum'),9)
        self.assertEqual(maximum([('Composite',7)],True,'single-bit union for flags'),1)
        self.assertEqual(maximum([('Negative',-1)],True,'single-bit union for flags'),1)
        self.assertEqual(maximum([('High',-(1<<63))],True,'single-bit union for flags'),-(1<<63)+1)
        self.assertEqual(maximum([('Negative',-4)],False,None),-3)
        self.assertIsNone(maximum(entries,True,None))
        self.assertEqual(maximum([],True,'single-bit union for flags'),1)
        self.assertEqual(maximum([],False,'single-bit union for flags; empty zero'),0)
        self.assertEqual(maximum(entries,True,'single-bit union for flags; empty zero'),10)
        self.assertIsNone(maximum([],False,None))

    def test_maximum_policy_is_bound_to_the_enum_constructor_vtable_and_virtual_method(self):
        for flags,expected in [(False,'signed maximum'),(True,'single-bit union for flags')]:
            with tempfile.TemporaryDirectory() as tmp:
                linker,records=fixture(os.path.join(tmp,'fixture.exe'),flags=flags)
                policy,evidence=discover(linker,records)
                self.assertEqual(policy,expected);self.assertEqual(evidence['status'],'verified')
        with tempfile.TemporaryDirectory() as tmp:
            linker,records=fixture(os.path.join(tmp,'fixture.exe'),wrong_table=True)
            self.assertIsNone(discover(linker,records)[0])
            # A matching reducer elsewhere in code cannot replace the missing link.
            self.assertEqual(reducer(linker,graph(linker,BASE+0x1900)),'signed maximum')

    def test_minimum_and_post_reduction_arithmetic_are_not_maximum_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,_=fixture(os.path.join(tmp,'fixture.exe'))
            code=synthetic_reducer(False)
            for changed in [code.replace(b'\x48\x0f\x4e\xc8',b'\x48\x0f\x4d\xc8'),code[:-1]+b'\x48\xff\xc0\xc3',
                            code.replace(b'\x48\x39\xc1',b'\x48\x83\xf9'+bytes([linker.cs.x86_const.X86_REG_RAX]))]:
                self.assertIsNone(reducer(linker,list(linker.md.disasm(changed,BASE+0x1900))))

    def test_an_absolute_count_address_cannot_supply_an_input_array_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,_=fixture(os.path.join(tmp,'fixture.exe'))
            code=synthetic_inline().replace(b'\x4c\x63\x53\x08',b'\x4c\x63\x14\x25\x08\x00\x00\x00')
            self.assertIsNone(inline_reducer(linker,list(linker.md.disasm(code,BASE+0x1a00))))

    def test_inline_policy_requires_both_reducers_empty_path_and_the_indexed_value_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,records=fixture(os.path.join(tmp,'fixture.exe'),inline=True)
            self.assertEqual(discover(linker,records)[0],'single-bit union for flags; empty zero')
            code=synthetic_inline()
            for changed in [code.replace(b'\x48\x09\xd1',b'\x48\x31\xd1'),
                            code.replace(b'\x48\x0f\x4e\xc2',b'\x48\x0f\x4d\xc2'),
                            code.replace(b'\x4a\x89\x34\xd7',b'\x4a\x89\x04\xd7'),
                            code.replace(b'\x41\x0f\xb6\xf1',b'\x41\x0f\xb6\xf0')]:
                self.assertIsNone(inline_reducer(linker,list(linker.md.disasm(changed,BASE+0x1a00))))

if __name__=='__main__':unittest.main()
