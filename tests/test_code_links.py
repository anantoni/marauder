import os,struct,sys,tempfile,unittest
from pathlib import Path
from test_ue_usmap import Img,BASE,synth
from usmap_generator.engine.scan import Image
from usmap_generator.engine.genusmap import im_functions
from usmap_generator.engine.code_links import CodeLinks
from usmap_generator.engine.genusmap import generate
from usmap_generator.engine.scan import Scanner

def unwrapped(im,path):
    im.build(path)
    data=Path(path).read_bytes()
    Path(path).write_bytes(data.replace(b'.bind\0\0\0',b'.safe\0\0\0',1))

def lea(register,pc,target):
    prefix={1:b'\x48\x8d\x0d',2:b'\x48\x8d\x15',8:b'\x4c\x8d\x05',9:b'\x4c\x8d\x0d'}[register]
    return prefix+struct.pack('<i',target-pc-7)

def emit_class(im,start,name,slot):
    code=b'\x48\x83\xec\x48'
    for offset,value in [(0x20,0x28),(0x28,8),(0x30,0x10000000)]:
        code+=b'\xc7\x44\x24'+bytes([offset])+struct.pack('<I',value)
    for reg,target in [(1,im.wstr('/Script/Synthetic')),(2,im.wstr(name)),(8,slot),(9,BASE+0x1800)]:
        code+=lea(reg,start+len(code),target)
    code+=b'\xe8'+struct.pack('<i',BASE+0x1c00-start-len(code)-5)
    code+=b'\x48\x8b\x05'+struct.pack('<i',slot-start-len(code)-7)+b'\x48\x83\xc4\x48\xc3'
    im.buf['.text'][start-BASE-0x1000:start-BASE-0x1000+len(code)]=code
    return (start-BASE,start-BASE+len(code),0x7000)

def emit_struct_constructor(im,start,params,slot):
    code=b'\x48\x83\xec\x28'
    code+=lea(1,start+len(code),slot)+lea(2,start+len(code)+7,params)
    code+=b'\xe8'+struct.pack('<i',BASE+0x1c00-start-len(code)-5)
    code+=b'\x48\x8b\x05'+struct.pack('<i',slot-start-len(code)-7)+b'\x48\x83\xc4\x28\xc3'
    off=start-BASE-0x1000;im.buf['.text'][off:off+len(code)]=code
    return code

def constant_property_initializer(im,start,prop,offset=4,unsafe=False):
    code=b'\xb8\x01\x00\x00\x00\x83\xc8\x40\x83\xc8\x04'
    if unsafe:code+=b'\x8b\x05'+struct.pack('<i',prop-start-len(code)-6)
    code+=b'\x89\x05'+struct.pack('<i',prop+0x1c-start-len(code)-6)
    for field,value in [(0x20,1),(0x24,offset)]:
        code+=b'\xc7\x05'+struct.pack('<i',prop+field-start-len(code)-10)+struct.pack('<I',value)
    code+=b'\xc3';o=start-BASE-0x1000;im.buf['.text'][o:o+len(code)]=code

def initializer_fixture(path,*,registered=True,unsafe=False,conflict=False,module='ucrtbase.dll'):
    im=Img();p=bytearray(48);struct.pack_into('<Q',p,0,im.cstr('Value'));p[0x18]=3
    prop=im.put('.data',p);array=im.put('.rdata',struct.pack('<Q',prop))
    im.put('.data',struct.pack('<QQQQQQQIII',BASE+0x1800,0,BASE+0x1900,im.cstr('NativeRecord'),8,4,array,1,0x45,1))
    callback=BASE+0x1600;constant_property_initializer(im,callback,prop,unsafe=unsafe)
    callbacks=[callback] if registered else [BASE+0x1700]
    im.buf['.text'][0x700]=0xc3
    if conflict:
        constant_property_initializer(im,BASE+0x1700,prop,offset=0)
        callbacks.append(BASE+0x1700)
    declare_crt(im,path,callbacks,module=module)
    return prop

def typed_initializer_fixture(path,kind='Struct',*,bad_pointer=False,memory_read=False,aliased_dimension=False):
    im=Img();record=bytearray(64);struct.pack_into('<Q',record,0,im.cstr('Value'))
    prop=im.put('.data',record);root=BASE+0x1600;target=im.cstr('NotCode') if bad_pointer else BASE+0x1800
    code=b''
    def scalar(offset,value):
        nonlocal code
        code+=b'\xc7\x05'+struct.pack('<i',prop+offset-root-len(code)-10)+struct.pack('<I',value)
    scalar(0x18,0x2c if kind=='Bool' else 0x19);scalar(0x1c,0x45)
    if aliased_dimension:
        code+=b'\xb8\x01\x00\x00\x00'
        code+=b'\x48\x8d\x05'+struct.pack('<i',target-root-len(code)-7)
        code+=b'\x89\x05'+struct.pack('<i',prop+0x20-root-len(code)-6)
    else:scalar(0x20,1)
    scalar(0x24,1 if kind=='Bool' else 8)
    if kind=='Bool':code+=b'\x48\xc7\x05'+struct.pack('<i',prop+0x28-root-len(code)-11)+struct.pack('<I',64)
    code+=(b'\x48\x8b\x05' if memory_read else b'\x48\x8d\x05')+struct.pack('<i',target-root-len(code)-7)
    tail=0x30 if kind=='Bool' else 0x28
    code+=b'\x48\x89\x05'+struct.pack('<i',prop+tail-root-len(code)-7)+b'\xc3'
    im.buf['.text'][0x600:0x600+len(code)]=code;declare_crt(im,path,[root])
    return prop,target

def declare_crt(im,path,callbacks,module='ucrtbase.dll'):
    table=im.put('.rdata',b''.join(struct.pack('<Q',p) for p in callbacks))
    hint=im.put('.rdata',b'\0\0_initterm\0',2)
    original=im.put('.rdata',struct.pack('<QQ',hint-BASE,0));iat=im.put('.rdata',struct.pack('<QQ',hint-BASE,0))
    dll=im.cstr(module);descriptor=im.put('.rdata',struct.pack('<IIIII',original-BASE,0,0,dll-BASE,iat-BASE)+bytes(20))
    thunk=BASE+0x1f00;code=b'\xff\x25'+struct.pack('<i',iat-thunk-6);im.buf['.text'][0xf00:0xf00+len(code)]=code
    root=BASE+0x1100;code=lea(1,root,table)+lea(2,root+7,table+8*len(callbacks))
    code+=b'\xe8'+struct.pack('<i',thunk-root-len(code)-5)+b'\xc3';im.buf['.text'][0x100:0x100+len(code)]=code
    unwrapped(im,path);raw=bytearray(Path(path).read_bytes())
    struct.pack_into('<I',raw,0x98+16,root-BASE);struct.pack_into('<II',raw,0x98+120,descriptor-BASE,40)
    Path(path).write_bytes(raw)

def emit_bootstrap(im,*,typed=True,returns_this=True):
    root=BASE+0x1600;slot=im.va('.data',0x1800);allocate=BASE+0x1b00;ctor=BASE+0x1a00
    code=b'\x48\x83\xec\x68'
    code+=b'\xe8'+struct.pack('<i',allocate-root-len(code)-5)
    code+=b'\x48\x89\x05'+struct.pack('<i',slot-root-len(code)-7)+b'\x48\x89\xc3'
    for offset,value in [(0x20,8),(0x28,0x10000000 if typed else 0),(0x30,0x20),(0x40,0xc7)]:
        code+=b'\xc7\x44\x24'+bytes([offset])+struct.pack('<I',value)
    for offset,target in [(0x38,im.wstr('Engine')),(0x48,BASE+0x1c00),(0x50,BASE+0x1c00),(0x58,BASE+0x1c00)]:
        code+=b'\x48\x8d\x05'+struct.pack('<i',target-root-len(code)-7)+b'\x48\x89\x44\x24'+bytes([offset])
    code+=b'\x48\x89\xd9\x31\xd2\x45\x31\xc0\x41\xb9\x80\x00\x00\x00'
    code+=b'\xe8'+struct.pack('<i',ctor-root-len(code)-5)
    code+=b'\x48\x89\x05'+struct.pack('<i',slot-root-len(code)-7)
    code+=b'\x48\x8b\x0d'+struct.pack('<i',slot-root-len(code)-7)
    code+=lea(2,root+len(code),im.wstr('/Script/Synthetic'))+lea(8,root+len(code)+7,im.wstr('IntrinsicNative'))
    code+=b'\xe8'+struct.pack('<i',BASE+0x1d00-root-len(code)-5)
    code+=b'\x48\x8b\x05'+struct.pack('<i',slot-root-len(code)-7)+b'\x48\x83\xc4\x68\xc3'
    im.buf['.text'][0x600:0x600+len(code)]=code
    body=b'\x48\x89\xc8\xc3' if returns_this else b'\x31\xc0\xc3'
    im.buf['.text'][0xa00:0xa00+len(body)]=body
    return root,slot

class StaticLinks(unittest.TestCase):
    def test_callback_return_proof_handles_long_control_flow_and_still_fails_at_its_bound(self):
        for blocks,expected in [(70,{0}),(180,set())]:
            with tempfile.TemporaryDirectory() as tmp:
                path=os.path.join(tmp,'fixture.exe');im=Img()
                # Independent empty conditional blocks; neither branch modifies
                # the saved callback or introduces a different return source.
                code=b'\x56\x48\x89\xce'+b'\x85\xd2\x74\x01\x90'*blocks+b'\xff\xd6\x5e\xc3'
                im.buf['.text'][0x200:0x200+len(code)]=code
                unwrapped(im,path);image=Image(path);links=CodeLinks(image,im_functions(image))
                result=links.scan(BASE+0x1200)
                self.assertEqual(result['return_argument_calls'],expected)
                self.assertEqual(result['unknown_returns'],not bool(expected))
                self.assertEqual(bool(links.stats['control_flow_limit']),not bool(expected))

    def test_intrinsic_bootstrap_identity_uses_the_typed_constructor_and_returned_singleton(self):
        im=Img();root,slot=emit_bootstrap(im)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.class_name(root),('/Script/Synthetic','IntrinsicNative'))
            self.assertEqual(links.bootstrap_evidence[slot]['constructor'],BASE+0x1a00)

    def test_named_objects_without_a_typed_self_returning_class_constructor_are_not_classes(self):
        for kwargs in ({'typed':False},{'returns_this':False}):
            im=Img();root,slot=emit_bootstrap(im,**kwargs)
            with tempfile.TemporaryDirectory() as tmp:
                p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
                self.assertIsNone(links.class_name(root))

    def test_an_unwind_fragment_is_the_same_bootstrap_writer_at_the_same_call_site(self):
        im=Img();root,slot=emit_bootstrap(im)
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'fixture.exe');unwrapped(im,path);image=Image(path);links=CodeLinks(image,im_functions(image))
            links.scan(root+4)
            self.assertEqual(links.class_name(root),('/Script/Synthetic','IntrinsicNative'))

    def test_virtual_call_provenance_preserves_the_actual_receiver_singleton(self):
        im=Img();root=BASE+0x1600;slot=im.put('.data',bytes(64));parent=BASE+0x1800
        code=b'\x48\x8b\x1d'+struct.pack('<i',slot-root-7)
        code+=b'\xe8'+struct.pack('<i',parent-root-len(code)-5)
        code+=b'\x48\x89\xc2\x48\x89\xd9\x48\x8b\x03\x4c\x8b\x80\xc8\x02\x00\x00\x41\xff\xd0\xc3'
        im.buf['.text'][0x600:0x600+len(code)]=code
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'fixture.exe');unwrapped(im,path);image=Image(path);links=CodeLinks(image,im_functions(image))
            site=[s for s in links.scan(root)['call_sites'] if s['virtual']][0]
            self.assertEqual(site['virtual'],(slot,0x2c8))
            self.assertEqual(site['origins']['rcx'],('data',slot))
            self.assertEqual(site['origins']['rdx'][0:2],('call',parent))

    def test_bootstrap_empty_schemas_require_a_calibrated_parent_method_and_engine_scope(self):
        from types import SimpleNamespace
        definitions=[('Class','Struct'),('Enum','Field'),('Field','Object'),('ScriptStruct','Struct'),
                     ('PackageMap','Object'),('CustomIntrinsic','Object')]
        evidence={};scans={};parents={}
        for i,(name,parent) in enumerate(definitions,1):
            evidence[i]={'identity':('/Script/CoreUObject',name),'initializer':100+i}
            parents[200+i]=('/Script/CoreUObject',parent)
            scans[100+i]={'call_sites':[{'virtual':(i,0x2c8),'origins':{'rcx':('data',i),'rdx':('call',200+i,300+i)}}]}
        # An engine name in another package is not the same intrinsic.
        evidence[20]={'identity':('/Script/Synthetic','PackageMap'),'initializer':120}
        scans[120]={'call_sites':[{'virtual':(20,0x2c8),'origins':{'rcx':('data',20),'rdx':('call',205,320)}}]}
        links=SimpleNamespace(bootstrap_evidence=evidence,scan=scans.get,class_result=parents.get)
        known={'Class':'Struct','Enum':'Field','Field':'Object','ScriptStruct':'Struct'}
        output,audit=CodeLinks.bootstrap_schemas(links,known)
        self.assertEqual(set(output),{'PackageMap'});self.assertEqual(output['PackageMap']['super'],'Object')
        self.assertEqual(audit['calibration_matches'],4)
        scans[105]['call_sites'][0]['origins']['rcx']=('data',99)
        self.assertNotIn('PackageMap',CodeLinks.bootstrap_schemas(links,known)[0])
        parents[202]=('/Script/CoreUObject','Struct')
        self.assertEqual(CodeLinks.bootstrap_schemas(links,known)[0],{})

    def test_callback_adapter_joins_constructor_ids_and_rejects_another_wrappers_cache(self):
        im=synth('P51');first=im.va('.data',0);second=im.va('.data',72)
        # A second, independent parameter record is needed only as an opaque stable id.
        emit_struct_constructor(im,BASE+0x1100,first,im.va('.data',0x1800))
        emit_struct_constructor(im,BASE+0x1200,second,im.va('.data',0x1810))
        helper=BASE+0x1f00;im.buf['.text'][0xf00:0xf07]=b'\x53\x48\x89\xcb\xff\xd3\x5b';im.buf['.text'][0xf07]=0xc3
        def wrapper(start,callback,write,returned):
            code=lea(1,start,callback)+b'\xe8'+struct.pack('<i',helper-start-12)
            code+=b'\x48\x89\x05'+struct.pack('<i',write-start-len(code)-7)
            code+=b'\x48\x8b\x05'+struct.pack('<i',returned-start-len(code)-7)+b'\xc3'
            off=start-BASE-0x1000;im.buf['.text'][off:off+len(code)]=code
        a,b=im.va('.data',0x1820),im.va('.data',0x1830)
        wrapper(BASE+0x1c00,BASE+0x1100,a,a);wrapper(BASE+0x1d00,BASE+0x1200,b,b)
        wrapper(BASE+0x1e00,BASE+0x1100,a,b)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image));records={first:'A',second:'B'}
            self.assertIsNone(links.constructor_id(BASE+0x1c00,records))
            self.assertEqual(links.registration_id(BASE+0x1c00,records),first)
            links.scan(BASE+0x1d00)
            self.assertIsNone(links.registration_id(BASE+0x1e00,records))

    def test_package_identity_comes_from_the_returned_parameter_record(self):
        im=Img();params=im.put('.rdata',struct.pack('<Q',im.cstr('/Script/Synthetic')))
        emit_struct_constructor(im,BASE+0x1100,params,im.va('.data',0x1800))
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.package_name(BASE+0x1100),'/Script/Synthetic')

    def test_inlined_cache_writers_bind_their_own_callback_call_sites(self):
        for conflict in (False,True):
            im=Img();a=im.put('.data',bytes(64));b=im.put('.data',bytes(64))
            emit_struct_constructor(im,BASE+0x1100,a,im.va('.data',0x1800))
            emit_struct_constructor(im,BASE+0x1200,b,im.va('.data',0x1810))
            helper=BASE+0x1f00;im.buf['.text'][0xf00:0xf08]=b'\x53\x48\x89\xcb\xff\xd3\x5b\xc3'
            cache=im.va('.data',0x1820);other=im.va('.data',0x1830)
            def calls(root,pairs):
                code=b''
                for callback,output in pairs:
                    code+=lea(1,root+len(code),callback)
                    code+=b'\xe8'+struct.pack('<i',helper-root-len(code)-5)
                    code+=b'\x48\x89\x05'+struct.pack('<i',output-root-len(code)-7)
                code+=b'\x48\x8b\x05'+struct.pack('<i',cache-root-len(code)-7)+b'\xc3'
                off=root-BASE-0x1000;im.buf['.text'][off:off+len(code)]=code
            calls(BASE+0x1c00,[(BASE+0x1100,cache)])
            calls(BASE+0x1d00,[(BASE+0x1200,other),(BASE+(0x1200 if conflict else 0x1100),cache)])
            with tempfile.TemporaryDirectory() as tmp:
                path=os.path.join(tmp,'fixture.exe');unwrapped(im,path);image=Image(path);links=CodeLinks(image,im_functions(image))
                links.scan(BASE+0x1d00)
                self.assertEqual(links.registration_id(BASE+0x1c00,{a:None,b:None}),None if conflict else a)

    def test_declared_crt_constant_initializer_recovers_scalar_property_metadata_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');prop=initializer_fixture(p);original=Path(p).read_bytes()
            self.assertNotIn('NativeRecord',generate(p)[1])
            _,schema,report=generate(p,code_links=True)
            self.assertEqual(schema['NativeRecord']['props'],[{'i':0,'dim':1,'name':'Value','type':{'t':'IntProperty'}}])
            self.assertEqual(report['staticCodeLinks']['metadata_initializer_bytes'],12)
            self.assertEqual(Path(p).read_bytes(),original)

    def test_initializer_recovers_kind_and_typed_tails_from_constant_addresses_without_reads(self):
        for kind in ('Struct','Bool'):
            for bad in ({},{'bad_pointer':True},{'memory_read':True}):
                with self.subTest(kind=kind,bad=bad),tempfile.TemporaryDirectory() as tmp:
                    path=os.path.join(tmp,'fixture.exe');prop,target=typed_initializer_fixture(path,kind,**bad)
                    original=Path(path).read_bytes();image=Image(path);scanner=Scanner(image);scanner.prepare();links=CodeLinks(image,im_functions(image))
                    overlay=links.metadata_initializers(scanner,[prop])
                    self.assertEqual(Path(path).read_bytes(),original)
                    if bad:self.assertEqual(overlay,{});continue
                    self.assertTrue(overlay)
                    for address,value in overlay.items():image.d[image.off(address)]=value
                    record=scanner._prop(prop,__import__('usmap_generator.engine.layouts',fromlist=['PROP']).PROP['P4x'])
                    self.assertEqual(record['kind'],kind);self.assertEqual(record['dim'],1)
                    if kind=='Struct':self.assertEqual(record['fn'],target)

    def test_initializer_address_formation_invalidates_the_previous_narrow_register_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'fixture.exe');prop,_=typed_initializer_fixture(path,aliased_dimension=True)
            image=Image(path);scanner=Scanner(image);scanner.prepare();links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.metadata_initializers(scanner,[prop]),{})

    def test_initializer_recovery_rejects_unregistered_callbacks_memory_reads_and_foreign_imports(self):
        for kwargs in ({'registered':False},{'unsafe':True},{'module':'synthetic.dll'}):
            with tempfile.TemporaryDirectory() as tmp:
                p=os.path.join(tmp,'fixture.exe');initializer_fixture(p,**kwargs)
                self.assertNotIn('NativeRecord',generate(p,code_links=True)[1])

    def test_conflicting_initializer_constants_leave_the_property_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');initializer_fixture(p,conflict=True)
            _,schema,report=generate(p,code_links=True)
            self.assertNotIn('NativeRecord',schema)
            self.assertEqual(report['staticCodeLinks']['metadata_initializer_conflicts'],1)

    def test_constant_address_adjustment_preserves_a_native_name_without_aliasing_loaded_objects(self):
        im=Img();start=BASE+0x1600;slot=im.va('.data',0x1800)
        code=b'\x48\x83\xec\x48'
        for offset,value in [(0x20,0x28),(0x28,8),(0x30,0x10000000)]:
            code+=b'\xc7\x44\x24'+bytes([offset])+struct.pack('<I',value)
        name=im.wstr('UNative')
        code+=b'\x48\x8d\x05'+struct.pack('<i',name-start-len(code)-7)+b'\x48\x83\xc0\x02\x48\x89\xc2'
        for reg,target in [(1,im.wstr('/Script/Synthetic')),(8,slot),(9,BASE+0x1800)]:code+=lea(reg,start+len(code),target)
        code+=b'\xe8'+struct.pack('<i',BASE+0x1c00-start-len(code)-5)
        code+=b'\x48\x8b\x05'+struct.pack('<i',slot-start-len(code)-7)+b'\x48\x83\xc4\x48\xc3'
        im.buf['.text'][0x600:0x600+len(code)]=code
        root=BASE+0x1900
        shifted=b'\x48\x8b\x05'+struct.pack('<i',slot-root-7)+b'\x48\x83\xc0\x08\xc3'
        im.buf['.text'][0x900:0x900+len(shifted)]=shifted
        im.buf['.pdata'][:24]=struct.pack('<IIIIII',0x1600,0x1600+len(code),0x7000,0x1900,0x1900+len(shifted),0x7000)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.class_name(start),('/Script/Synthetic','Native'))
            self.assertIsNone(links.class_name(root))

    def test_traps_do_not_fall_through_into_an_adjacent_constructor(self):
        for trap in (b'\xcc',b'\x0f\x0b',b'\xf4'):
            im=synth('P51');root=BASE+0x1600;params=im.va('.data',0)
            im.buf['.text'][0x600:0x600+len(trap)]=trap
            neighbour=root+len(trap)
            body=emit_struct_constructor(im,neighbour,params,im.va('.data',0x1900))
            im.buf['.pdata'][:24]=struct.pack('<IIIIII',0x1600,0x1600+len(trap),0x7000,
                neighbour-BASE,neighbour-BASE+len(body),0x7000)
            with tempfile.TemporaryDirectory() as tmp:
                p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
                self.assertIsNone(links.constructor(root,{params:'Neighbour'}))
                self.assertEqual(links.constructor(neighbour,{params:'Neighbour'}),'Neighbour')

    def test_class_parameter_identity_reaches_the_constructed_inner_singleton(self):
        im=synth('P51');root=BASE+0x1100;params=im.va('.data',0)
        emit_struct_constructor(im,root,params,im.va('.data',0x1900))
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            links.class_parameters[params]=('/Script/Synthetic','NativeClass')
            self.assertEqual(links.class_name(root),('/Script/Synthetic','NativeClass'))

    def test_registerless_memory_operand_is_unknown_without_crashing(self):
        im=Img();code=b'\x48\x8b\x04\x25\x00\x00\x00\x00\xc3'
        im.buf['.text'][0x600:0x600+len(code)]=code
        im.buf['.pdata'][:12]=struct.pack('<III',0x1600,0x1600+len(code),0x7000)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertIsNone(links.class_name(BASE+0x1600))

    def test_constructing_an_unreturned_type_is_not_a_type_link(self):
        im=synth('P51');start=BASE+0x1100;target=im.va('.data',0)
        code=emit_struct_constructor(im,start,target,im.va('.data',0x1900))
        # The same function calls a constructor but returns a different, unproven singleton.
        other=im.va('.data',0x1910);offset=4+7+7+5
        im.buf['.text'][0x100+offset:0x100+offset+7]=b'\x48\x8b\x05'+struct.pack('<i',other-start-offset-7)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertIsNone(links.constructor(start,{target:'WrongIfGuessed'}))

    def test_intrinsic_super_layout_is_calibrated_and_unknown_classes_are_not_filled(self):
        from collections import defaultdict
        links=object.__new__(CodeLinks);links.class_slots=defaultdict(set);links.class_records={}
        native=lambda name:('/Script/CoreUObject',name)
        parents={10:native('Field'),20:native('Object'),30:native('Class')}
        links.class_result=lambda fn:parents.get(fn)
        relations=[]
        for i,(name,parent_fn) in enumerate([('ChildA',10),('ChildB',30),('ChildC',10),('Property',10),('CustomIntrinsic',10)]):
            identity=native(name);links.class_slots[i].add(identity)
            links.class_records[i]={'identity':identity,'arguments':{0x30:0x10000000,0x80:parent_fn,0x88:20},'address_arguments':{0x80,0x88}}
            if name.startswith('Child'):relations.append((identity,parents[parent_fn]))
        identity=('/Script/Synthetic','Property');links.class_slots[99].add(identity)
        links.class_records[99]={'identity':identity,'arguments':{0x30:0x10000000,0x80:10},'address_arguments':{0x80}}
        schemas,proof=links.intrinsic_schemas(relations)
        self.assertEqual(proof['superclass_argument'],0x80)
        self.assertEqual(schemas['Property']['super'],'Field')
        self.assertNotIn('CustomIntrinsic',schemas)
        links.class_records[2]['arguments'][0x80]=20
        self.assertEqual(links.intrinsic_schemas(relations)[0],{})

    def test_frame_relative_stack_arguments_keep_their_call_site_positions(self):
        im=Img();start=BASE+0x1600;slot=im.va('.data',0x1800)
        code=b'\x4c\x8b\xdc\x48\x81\xec\x88\x00\x00\x00'
        for offset,target in [(0xd8,BASE+0x1800),(0xe0,BASE+0x1900)]:
            code+=lea(2,start+len(code),target)
            code+=b'\x49\x89\x53'+bytes([offset])
        for offset,value in [(0x20,0x28),(0x28,8),(0x30,0x10000000)]:
            code+=b'\xc7\x44\x24'+bytes([offset])+struct.pack('<I',value)
        for reg,target in [(1,im.wstr('/Script/Synthetic')),(2,im.wstr('Native')),(8,slot),(9,BASE+0x1d00)]:
            code+=lea(reg,start+len(code),target)
        code+=b'\xe8'+struct.pack('<i',BASE+0x1c00-start-len(code)-5)
        code+=b'\x48\x8b\x05'+struct.pack('<i',slot-start-len(code)-7)+b'\x48\x81\xc4\x88\x00\x00\x00\xc3'
        im.buf['.text'][0x600:0x600+len(code)]=code
        im.buf['.pdata'][:12]=struct.pack('<III',0x1600,0x1600+len(code),0x7000)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.class_name(start),('/Script/Synthetic','Native'))
            args=links.class_records[slot]['arguments']
            self.assertEqual(args[0x60],BASE+0x1800)
            self.assertEqual(args[0x68],BASE+0x1900)

    def test_exact_constructor_link_does_not_collapse_an_equal_layout_prefix_alias(self):
        from usmap_generator.engine.genusmap import generate
        im=synth('P51');twin=bytearray(im.buf['.data'][:68])
        struct.pack_into('<Q',twin,0x10,BASE+0x1840)
        struct.pack_into('<Q',twin,0x18,im.cstr('InnerVariant'))
        params=im.put('.data',twin);start=BASE+0x1100
        emit_struct_constructor(im,start,params,im.va('.data',0x1900))
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p)
            _,schema,report=generate(p,code_links=True)
        self.assertEqual(schema['Outer']['props'][1]['type'],{'t':'StructProperty','struct':'InnerVariant'})
        self.assertEqual(report['naming']['structHow'],{'code':1})

    def test_singleton_identity_is_joined_by_slot_and_ambiguous_returns_fail_closed(self):
        im=Img();a,b=im.va('.data',0x1800),im.va('.data',0x1810)
        entries=[emit_class(im,BASE+0x1600,'Alpha',a),emit_class(im,BASE+0x1700,'Beta',b)]
        root=BASE+0x1900
        load=lambda pc,slot:b'\x48\x8b\x05'+struct.pack('<i',slot-pc-7)
        code=b'\x85\xc9\x74\x09'+load(root+4,a)+b'\xeb\x07'+load(root+13,b)+b'\xc3'
        im.buf['.text'][0x900:0x900+len(code)]=code;entries.append((0x1900,0x1900+len(code),0x7000))
        getter=BASE+0x1a00;one=load(getter,a)+b'\xc3'
        im.buf['.text'][0xa00:0xa00+len(one)]=one;entries.append((0x1a00,0x1a00+len(one),0x7000))
        partial=BASE+0x1b00;two=load(partial,a)+b'\xb0\x00\xc3'
        im.buf['.text'][0xb00:0xb00+len(two)]=two;entries.append((0x1b00,0x1b00+len(two),0x7000))
        inner=im.va('.data',0x1820);constructor=BASE+0x1d00
        copy=b'\x48\x83\xec\x28\xe8'+struct.pack('<i',getter-constructor-9)
        copy+=b'\x48\x89\x05'+struct.pack('<i',inner-constructor-len(copy)-7)
        copy+=b'\x48\x83\xc4\x28\xc3'
        im.buf['.text'][0xd00:0xd00+len(copy)]=copy;entries.append((0x1d00,0x1d00+len(copy),0x7000))
        inner_getter=BASE+0x1e00;copied=load(inner_getter,inner)+b'\xc3'
        im.buf['.text'][0xe00:0xe00+len(copied)]=copied;entries.append((0x1e00,0x1e00+len(copied),0x7000))
        im.buf['.pdata'][:12*len(entries)]=b''.join(struct.pack('<III',*entry) for entry in sorted(entries))
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            links.scan(BASE+0x1600);links.scan(BASE+0x1700)
            self.assertEqual(links.class_name(getter),('/Script/Synthetic','Alpha'))
            self.assertIsNone(links.class_name(root))
            self.assertIsNone(links.class_name(partial))
            links.scan(constructor)
            self.assertEqual(links.class_name(inner_getter),('/Script/Synthetic','Alpha'))

    def test_parameter_link_crosses_a_hot_cold_runtime_function_split(self):
        im=synth('P51');start=BASE+0x1100;target=im.va('.data',0)
        # The first unwind range ends at a conditional branch, before the constructor call.
        slot=im.va('.data',0x1900)
        prologue=b'\x48\x83\xec\x28\x48\x8b\x05'+struct.pack('<i',slot-start-11)+b'\x48\x85\xc0\x75\x30'
        body=lea(1,start+len(prologue),slot)+lea(2,start+len(prologue)+7,target)
        body+=b'\xe8'+struct.pack('<i',BASE+0x1800-start-len(prologue)-len(body)-5)
        body+=b'\x48\x8b\x05'+struct.pack('<i',slot-start-len(prologue)-len(body)-7)+b'\x48\x83\xc4\x28\xc3'
        code=prologue+body
        im.buf['.text'][0x100:0x100+len(code)]=code
        im.buf['.text'][0x140:0x145]=b'\x48\x83\xc4\x28\xc3'
        entries=[(0x1100,0x1110,0x7000),(0x1110,0x1140,0x7000),(0x1140,0x1145,0x7000)]
        im.buf['.pdata'][:36]=b''.join(struct.pack('<III',*entry) for entry in entries)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.constructor(start,{target:'NativeIdentity'}),'NativeIdentity')

    def test_direct_parameter_argument_identifies_constructor(self):
        im=synth('P51');start=BASE+0x1100;target=im.va('.data',0)
        emit_struct_constructor(im,start,target,im.va('.data',0x1900))
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p)
            links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.constructor(start,{target:'NativeIdentity'}),'NativeIdentity')
            self.assertGreater(links.summary()['code_bytes_read'],0)

    def test_protected_marker_blocks_every_code_read(self):
        im=synth('P51')
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');im.build(p);image=Image(p)
            links=CodeLinks(image,im_functions(image))
            self.assertIsNone(links.constructor(BASE+0x1100,{im.va('.data',0):'Identity'}))
            self.assertEqual(links.class_candidates(),set())
            self.assertNotIn('code_bytes_read',links.summary())
            with self.assertRaises(ValueError):links.read(BASE+0x1100,1)

    def test_class_identity_needs_package_name_singleton_and_callback(self):
        im=Img();start=BASE+0x1600
        package=im.wstr('/Script/Synthetic');name=im.wstr('DEPRECATED_Parent')
        code=b'\x48\x83\xec\x48'
        for offset,value in [(0x20,0x28),(0x28,8),(0x30,0x10000000)]:
            code+=b'\xc7\x44\x24'+bytes([offset])+struct.pack('<I',value)
        for reg,target in [(1,package),(2,name),(8,im.va('.data',0x1800)),(9,BASE+0x1800)]:
            code+=lea(reg,start+len(code),target)
        code+=b'\xe8'+struct.pack('<i',BASE+0x1900-start-len(code)-5)
        code+=b'\x48\x8b\x05'+struct.pack('<i',im.va('.data',0x1800)-start-len(code)-7)+b'\x48\x83\xc4\x48\xc3'
        im.buf['.text'][0x600:0x600+len(code)]=code
        im.buf['.pdata'][:12]=struct.pack('<III',0x1600,0x1600+len(code),0x7000)
        with tempfile.TemporaryDirectory() as tmp:
            p=os.path.join(tmp,'fixture.exe');unwrapped(im,p);image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(links.class_name(start),('/Script/Synthetic','Parent'))
            self.assertIn(start,links.class_candidates())

if __name__=='__main__':unittest.main()
