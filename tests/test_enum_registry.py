import os,struct,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
from test_ue_usmap import Img,BASE
from test_code_links import declare_crt,emit_struct_constructor,lea
from usmap_generator.engine.scan import Image,Scanner
from usmap_generator.engine.genusmap import enum_prefix,im_functions
from usmap_generator.engine.code_links import CodeLinks
from usmap_generator.engine.enum_registry import discover,build

def fixture(path,reverse=False,mismatch=False,legacy=False):
    im=Img();package=im.put('.rdata',struct.pack('<Q',im.cstr('/Script/Synthetic')))
    emit_struct_constructor(im,BASE+0x1800,package,im.va('.data',0x1a00))
    records=[];wrappers=[]
    for i,(name,entries) in enumerate([('EFirst',[('Shared_A',0),('Shared_B',1)]),('ESecond',[('Shared_C',2),('Shared_D',3)])]):
        values=im.put('.rdata',b''.join(struct.pack('<Qq',im.cstr(n),v) for n,v in entries));record=bytearray(48)
        nm=im.cstr(name);struct.pack_into('<QQQQQ',record,0,BASE+0x1800,0,nm,nm,values)
        struct.pack_into('<IHBB',record,0x28,0x45,len(entries),0,0);param=im.put('.data',record);records.append(param)
        constructor=BASE+0x1200+0x100*i;emit_struct_constructor(im,constructor,param,im.va('.data',0x1a10+0x10*i))
        wrapper=BASE+0x1d00+0x100*i;cache=im.va('.data',0x1a30+0x10*i)
        code=lea(1,wrapper,constructor)+b'\xe8'+struct.pack('<i',BASE+0x1c80-wrapper-12)
        code+=b'\x48\x89\x05'+struct.pack('<i',cache-wrapper-len(code)-7)
        code+=b'\x48\x8b\x05'+struct.pack('<i',cache-wrapper-len(code)-7)+b'\xc3'
        offset=wrapper-BASE-0x1000;im.buf['.text'][offset:offset+len(code)]=code
        wrappers.append((wrapper,'WrongName' if mismatch and i==0 else name,im.va('.data',0x1b00+0x18*i),0))
    im.buf['.text'][0xc80:0xc88]=b'\x53\x48\x89\xcb\xff\xd3\x5b\xc3'
    rows=list(reversed(wrappers)) if reverse else wrappers
    table=im.put('.data',b''.join(struct.pack('<QQQQ',fn,im.wstr(name),info,version) for fn,name,info,version in rows))
    root=BASE+0x1600;code=b'\x48\x83\xec\x48'
    code+=b'\x48\xc7\x44\x24\x30\x02\x00\x00\x00'
    code+=b'\x48\x8d\x05'+struct.pack('<i',table-root-len(code)-7)+b'\x48\x89\x44\x24\x28'
    code+=b'\x48\xc7\x44\x24\x20\x00\x00\x00\x00\x45\x31\xc9\x45\x31\xc0\x31\xd2'
    code+=lea(1,root+len(code),im.wstr('/Script/Synthetic'))
    code+=b'\xe8'+struct.pack('<i',BASE+0x1b00-root-len(code)-5)+b'\x48\x83\xc4\x48\xc3'
    if legacy:
        code=b'\x48\x83\xec\x28'
        for fn,name,info,version in rows:
            for reg,target in [(1,fn),(2,im.wstr('/Script/Synthetic')),(8,im.wstr(name))]:
                code+=lea(reg,root+len(code),target)
            code+=b'\x45\x31\xc9\xe8'+struct.pack('<i',BASE+0x1b00-root-len(code)-8)
        code+=b'\x48\x83\xc4\x28\xc3'
    im.buf['.text'][0x600:0x600+len(code)]=code
    declare_crt(im,path,[root])
    image=Image(path);scanner=Scanner(image);scanner.prepare();defs={x['va']:x for x in scanner.enums_raw('E5x')}
    return CodeLinks(image,im_functions(image)),defs,records

class RegistryRules(unittest.TestCase):
    def test_legacy_struct_defer_is_excluded_only_by_proven_parameter_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,records,ids=fixture(os.path.join(tmp,'fixture.exe'),legacy=True)
            enums={ids[0]:records[ids[0]]};structs={ids[1]:records[ids[1]]}
            rows,unknown,audit,_=discover(linker,enums,structs)
            self.assertEqual([r['record'] for r in rows],[ids[0]])
            self.assertEqual(unknown,set());self.assertEqual(audit['other_reflection_registrations'],1)
            # Without a verified non-enum identity the same unresolved call is a gap.
            _,unknown,_,_=discover(linker,enums)
            self.assertEqual(unknown,{'/Script/Synthetic'})

    def test_declared_registration_array_binds_returned_enum_and_package_identifiers(self):
        for reverse in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                linker,records,ids=fixture(os.path.join(tmp,'fixture.exe'),reverse=reverse)
                rows,unknown,audit,_=discover(linker,records)
                self.assertEqual([r['record'] for r in rows],list(reversed(ids)) if reverse else ids)
                self.assertEqual(unknown,set());self.assertEqual(audit['registration_tables'],1)
                output,audit,_=build(records,linker,enum_prefix)
                self.assertEqual(audit['complete_packages'],1);self.assertEqual(audit['automatic_name_collisions'],1)
                first='ESecond' if reverse else 'EFirst';last='EFirst' if reverse else 'ESecond'
                self.assertEqual(output[first][-1][1],'Shared_MAX')
                self.assertNotIn('Shared_MAX',[n for v,n in output[last]])

    def test_matching_names_cannot_replace_constructor_identity_or_complete_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            linker,records,ids=fixture(os.path.join(tmp,'fixture.exe'),mismatch=True)
            rows,unknown,audit,_=discover(linker,records)
            self.assertEqual(rows,[]);self.assertEqual(unknown,{'/Script/Synthetic'})
            output,audit,_=build(records,linker,enum_prefix)
            self.assertEqual(audit['complete_packages'],0)
            self.assertTrue(all(not any(n=='Shared_MAX' for v,n in entries) for entries in output.values()))

    def test_collision_does_not_register_other_raw_names_and_packages_are_independent(self):
        def rec(n,entries,outer=10,form=0):return {'name':n,'entries':entries,'outer':outer,'form':form,'flags':0}
        records={1:rec('EFirst',[('Shared_A',0),('Shared_B',2)]),
                 2:rec('ESecond',[('Shared_C',0),('Shared_Other_MAX',8)]),
                 3:rec('EThird',[('Shared_Other_A',0),('Shared_Other_B',1)]),
                 4:rec('EElsewhere',[('Shared_A',0),('Shared_B',1)],outer=20),
                 5:rec('EEmpty',[],form=2)}
        scopes={10:'/Script/One',20:'/Script/Two'}
        rows=[{'record':i,'package':scopes[r['outer']]} for i,r in records.items()]
        linker=SimpleNamespace(package_name=scopes.get)
        with patch('usmap_generator.engine.enum_registry.discover',return_value=(rows,set(),{},dict(scopes))),patch('usmap_generator.engine.enum_maximum.discover',return_value=('signed maximum',{})):
            output,audit,_=build(records,linker,enum_prefix)
        self.assertEqual(output['EFirst'][-1],(3,'Shared_MAX'))
        self.assertEqual(output['EThird'][-1],(2,'Shared_Other_MAX'))
        self.assertEqual(output['EElsewhere'][-1],(2,'Shared_MAX'))
        self.assertEqual(output['EEmpty'],[(1,'EEmpty_MAX')])
        self.assertEqual(audit['automatic_name_collisions'],1)

if __name__=='__main__':unittest.main()
