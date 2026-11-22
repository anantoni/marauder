"""Synthetic regression cases: no installed executables, captures or game names."""
import os,struct,sys,tempfile,unittest
from test_ue_usmap import Img,BASE,synth
from usmap_generator.engine.scan import Scanner,Image
from usmap_generator.engine.genusmap import generate,version_string,version_estimate,enum_entries
from usmap_generator.engine import usmap as U
from usmap_generator.engine import naming

class TreeRules(unittest.TestCase):
    def test_code_mode_does_not_name_an_unknown_class_by_its_first_property(self):
        from test_code_links import unwrapped
        im=Img();prop=bytearray(72);struct.pack_into('<Q',prop,0,im.cstr('ClassWithin'));prop[0x18]=3
        struct.pack_into('<I',prop,0x1c,0x45);struct.pack_into('<I',prop,0x20,1)
        field=im.put('.rdata',prop);array=im.put('.rdata',struct.pack('<Q',field));cpp=im.put('.rdata',bytes(8))
        deps=im.put('.rdata',struct.pack('<Q',BASE+0x1800));record=bytearray(80)
        struct.pack_into('<QQQQQQQ',record,0,BASE+0x1900,0,cpp,deps,0,array,0)
        struct.pack_into('<IIII',record,0x38,1,0,1,0);im.put('.data',record)
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');unwrapped(im,exe);_,schema,report=generate(exe,code_links=True)
        self.assertEqual(report['counts']['classesUnnamed'],1)
        self.assertEqual(schema['Class']['props'],[])

    def test_empty_enum_requires_matching_null_count_native_flags_and_valid_form(self):
        for packed in (False,True):
            im=Img();name=im.cstr('EEmpty');rec=bytearray(56)
            struct.pack_into('<QQQQQ',rec,0,BASE+0x1800,0,name,name,0)
            if packed:struct.pack_into('<IHBB',rec,0x28,0x45,0,0,2)
            else:struct.pack_into('<IIBB',rec,0x28,0,0x45,0,2)
            ptr=im.put('.data',rec)
            with tempfile.TemporaryDirectory() as tmp:
                exe=os.path.join(tmp,'fixture.exe');im.build(exe);image=Image(exe);sc=Scanner(image);sc.prepare()
                family='E5x' if packed else 'E51'
                self.assertEqual(sc.enums_raw(family)[0]['entries'],[])
                e,_,report=generate(exe);self.assertEqual(e['EEmpty'],[])
                self.assertEqual(report['enumRegistration']['unverified_automatic_values'],1)
                struct.pack_into('<Q',image.d,image.off(ptr+0x20),name)
                self.assertEqual(sc.enums_raw(family),[])

    def test_modern_object_modifier_does_not_use_the_legacy_bool_exclusion(self):
        im=Img();record=bytearray(64);struct.pack_into('<Q',record,0,im.cstr('Object'))
        record[0x18]=0x52;struct.pack_into('<I',record,0x1c,0x45)
        struct.pack_into('<HH',record,0x30,1,0);struct.pack_into('<Q',record,0x38,BASE+0x1800)
        ptr=im.put('.rdata',record)
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe);node=Scanner(Image(exe)).prop(ptr,'P5x')
            self.assertEqual(node['kind'],'Object');self.assertEqual(node['gen_flags'],0x52)

    def test_utf8_and_ansi_codegen_tags_do_not_alias_verse_dynamic(self):
        im=Img();ptrs=[]
        for name,kind,offset in [('Utf8',0x23,0),('Ansi',0x24,16)]:
            rec=bytearray(56);struct.pack_into('<Q',rec,0,im.cstr(name));rec[0x18]=kind
            struct.pack_into('<I',rec,0x1c,0x45);struct.pack_into('<HH',rec,0x30,1,offset)
            ptrs.append(im.put('.rdata',rec))
        array=im.put('.rdata',struct.pack('<QQ',*ptrs));rec=bytearray(56)
        struct.pack_into('<Q',rec,0,BASE+0x1800);struct.pack_into('<Q',rec,0x18,im.cstr('Strings'))
        struct.pack_into('<Q',rec,0x20,array);struct.pack_into('<HHB',rec,0x28,2,32,8)
        im.put('.data',rec)
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe)
            _,schema,_=generate(exe)
            self.assertEqual([p['type']['t'] for p in schema['Strings']['props']],['Utf8StrProperty','AnsiStrProperty'])
            image=Image(exe);position=image.off(ptrs[0])+0x18;image.d[position]=0x22
            self.assertIsNone(Scanner(image).prop(ptrs[0],'P5x'))

    def test_legacy_kind_bits_and_child_first_order_are_detected_independently(self):
        im=Img();T=lambda n:BASE+0x1000+n
        def prop(name,kind,off=0,fn=0):
            return im.put('.rdata',struct.pack('<QQQBxxxIIIQ',im.cstr(name),0,0,kind,0x45,1,off,fn))
        ptrs=[prop('Enabled',0x2c),prop('UnderlyingType',0),prop('Choices',0x1e,fn=T(0x200)),prop('Choices',0x16,8)]
        array=im.put('.rdata',b''.join(struct.pack('<Q',p) for p in ptrs))
        im.put('.data',struct.pack('<QQQQQQQIII',T(0x10),0,T(0x440),im.cstr('Legacy'),24,8,array,len(ptrs),0x45,1))
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe)
            _,schema,report=generate(exe)
        self.assertEqual(report['layouts']['chosen']['prop'],'P4x')
        self.assertEqual([p['name'] for p in schema['Legacy']['props']],['Enabled','Choices'])
        self.assertEqual(schema['Legacy']['props'][0]['type'],{'t':'BoolProperty'})
        self.assertEqual(schema['Legacy']['count'],2)

    def test_lwc_real_does_not_masquerade_as_a_legacy_byte(self):
        im=Img();ptr=im.put('.rdata',struct.pack('<QQQBxxxIIIQ',im.cstr('Real'),0,0,0x20,0x45,1,0,0))
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe);sc=Scanner(Image(exe))
            self.assertEqual(sc.prop(ptr,'P50')['kind'],'LWCReal')
            self.assertIsNone(sc.prop(ptr,'P4'));self.assertIsNone(sc.prop(ptr,'P4x'))

    def test_legacy_native_bool_flag_preserves_the_whole_owning_schema(self):
        im=Img()
        ptr=im.put('.rdata',struct.pack('<QQQBxxxIIIQ',im.cstr('Enabled'),0,0,0x2c,0x45,1,1,0))
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe);sc=Scanner(Image(exe))
            self.assertEqual(sc.tree([ptr],'P4')[0]['kind'],'Bool')
            self.assertIsNone(sc.tree([ptr],'P50'))

    def test_typed_operator_signatures_bind_native_operands_without_aliasing(self):
        def node(kind,name,z=None):return {'kind':kind,'name':name,'fn':z}
        f={'name':'Multiply_VectorDouble','tree':[node('Struct','A',10),node('Double','B'),node('Struct','ReturnValue',10)]}
        self.assertEqual(naming.operator_hints(f,{'Vector','IntVector'}),{10:'Vector'})
        f['name']='Multiply_VectorFloat'
        self.assertEqual(naming.operator_hints(f,{'Vector','IntVector'}),{})
        f={'name':'Add_VectorIntVector','tree':[node('Struct','A',10),node('Struct','B',10)]}
        self.assertEqual(naming.operator_hints(f,{'Vector','IntVector'}),{})
        f={'name':'Add_ABC','tree':[node('Struct','A',10),node('Struct','B',20)]}
        self.assertEqual(naming.operator_hints(f,{'A','AB','BC','C'}),{})

    def test_scoped_enum_max_names_are_qualified_once(self):
        self.assertEqual(enum_entries('EStyle',[('EStyle::UseColor_A',0),('EStyle::UseColor_B',1)],False),
                         [(0,'UseColor_A'),(1,'UseColor_B'),(2,'UseColor_MAX')])
        self.assertEqual(enum_entries('EMode',[('EMode::A',0),('EMode::Max',1)],False),
                         [(0,'A'),(1,'Max')])
        self.assertEqual(enum_entries('ESource',[('ESource::Data_Kind_A',0),('ESource::Data_Max',1)],False),
                         [(0,'Data_Kind_A'),(1,'Data_Max')])

    def test_generated_enum_max_requires_a_matched_flag_or_empty_reducer(self):
        for flags in (False,True):
            self.assertEqual(enum_entries('EBits',[('Bit_A',1),('Bit_C',4)],flags,'signed maximum'),
                             [(1,'Bit_A'),(4,'Bit_C'),(5,'Bit_MAX')])
            self.assertEqual(enum_entries('EEmpty',[],flags,'signed maximum'),[(1,'EEmpty_MAX')])
        self.assertEqual(enum_entries('EEmpty',[],False),[])
        self.assertEqual(enum_entries('EBits',[('Bit_A',1)],True),[(1,'Bit_A')])

    def test_explicit_build_versions_cover_engine_boundaries_and_utf16(self):
        for version in ['4.25','4.26','4.27','5.0','5.2','5.3','5.5']:
            for encoding in ['ascii','utf-16-le']:
                im=Img();im.put('.rdata',(f'++UE{version[0]}+Release-{version}\0').encode(encoding))
                with tempfile.TemporaryDirectory() as tmp:
                    exe=os.path.join(tmp,'fixture.exe');im.build(exe)
                    self.assertEqual(version_string(Image(exe)),version)
        self.assertEqual(version_estimate(None,{'prop':'P4'}, {})['version'],'4.x')

    def test_absent_new_property_kinds_do_not_imply_an_older_minor_version(self):
        for features in ({'optionalProps':0,'utf8StrProps':0,'ansiStrProps':0},
                         {'optionalProps':1,'utf8StrProps':0,'ansiStrProps':0},
                         {'optionalProps':1,'utf8StrProps':3,'ansiStrProps':0}):
            self.assertEqual(version_estimate(None,{'prop':'P5x'},features)['version'],'5.x')
        self.assertEqual(version_estimate(None,{'prop':'P51'}, {})['version'],'5.x')
        self.assertEqual(version_estimate(None,{'prop':'P50'}, {})['version'],'unknown')
        self.assertEqual(version_estimate('5.5',{'prop':'P5x'}, {})['version'],'5.5')

    def test_deprecated_cpp_class_names_preserve_runtime_identity_and_super(self):
        im=synth('P51');T=lambda n:BASE+0x1000+n
        cpp=im.put('.rdata',bytes(16))
        for outer,inner,name,superfn in [(T(0x600),T(0x640),'UDEPRECATED_Parent',T(0x10)),
                                         (T(0x700),T(0x740),'UChild',T(0x600))]:
            deps=im.put('.rdata',struct.pack('<Q',superfn));rec=bytearray(80)
            struct.pack_into('<QQQQ',rec,0,inner,0,cpp,deps)
            struct.pack_into('<I',rec,0x38,1)
            im.put('.data',rec)
            im.put('.rdata',struct.pack('<QQQQQ',outer,inner,im.wstr(name),im.va('.data',0x1800),0))
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe);e,s,r=generate(exe)
        self.assertIn('Parent',s);self.assertNotIn('DEPRECATED_Parent',s)
        self.assertEqual(s['Child']['super'],'Parent')

    def test_ue4_container_children_and_fixed_array_keep_schema_indices(self):
        im=Img();T=lambda n:BASE+0x1000+n
        def prop(name,kind,off=0,dim=1,fn=0):
            return im.put('.rdata',struct.pack('<QQQBxxxIIIQ',im.cstr(name),0,0,kind,0x45,dim,off,fn))
        ptrs=[prop('Tail',0x0a,96,3),
              prop('Choices',0x16,80),prop('Choices',0x1e,0,fn=T(0x200)),prop('UnderlyingType',0),
              prop('Lookup',0x17,16),prop('Lookup_Key',0x14),prop('Lookup_Value',3),
              prop('First',3,0)]
        array=im.put('.rdata',b''.join(struct.pack('<Q',p) for p in ptrs))
        im.put('.data',struct.pack('<QQQQQQQIII',T(0x10),0,T(0x440),im.cstr('Outer'),112,8,array,len(ptrs),0x45,1))
        entries=im.put('.rdata',struct.pack('<QqQq',im.cstr('Red'),0,im.cstr('Blue'),1))
        enum=bytearray(56)
        struct.pack_into('<QQQQQ',enum,0,T(0x10),0,im.cstr('EChoice'),im.cstr('EChoice'),entries)
        struct.pack_into('<II',enum,0x28,2,0x45)
        im.put('.data',enum)
        im.put('.rdata',struct.pack('<QQQQ',T(0x280),im.wstr('EChoice'),im.va('.data',0x1800),0))
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe);sc=Scanner(Image(exe))
            tree=sc.tree(ptrs,'P4')
            _,schema,report=generate(exe)
        self.assertEqual([p['name'] for p in tree],['First','Lookup','Choices','Tail'])
        self.assertEqual([p['kind'] for p in tree[1]['children']],['Name','Int'])
        self.assertEqual(tree[2]['children'][0]['kind'],'Enum')
        self.assertEqual(tree[2]['children'][0]['children'][0]['kind'],'Byte')
        self.assertEqual(report['layouts']['chosen']['prop'],'P4')
        self.assertEqual([p['i'] for p in schema['Outer']['props']],[0,1,2,3])
        self.assertEqual(schema['Outer']['count'],6)
        self.assertEqual(schema['Outer']['props'][1]['type'],{'t':'MapProperty','key':{'t':'NameProperty'},'value':{'t':'IntProperty'}})
        self.assertEqual(schema['Outer']['props'][2]['type'],{'t':'ArrayProperty','inner':{'t':'EnumProperty','inner':{'t':'ByteProperty'},'enum':'EChoice'}})
        self.assertIsNone(sc.tree(ptrs,'P50'))

    def test_invalid_enum_does_not_consume_a_neighbouring_object(self):
        sc=Scanner(None)
        sc.prop=lambda va,L:{'name':str(va),'kind':{1:'Object',2:'Enum'}[va],'dim':1,'fn':1}
        self.assertIsNone(sc.tree([1,2],'P50'))
        self.assertIsNone(sc.tree(None,'P50'))

    def test_struct_ops_anchor_is_useful_without_registration_table(self):
        im=synth('P51')
        # Remove registration records without removing FStructParams.StructOpsFunc.
        # They are the final two 40-byte records in the synthetic rdata section.
        end=im.cur['.rdata']
        im.buf['.rdata'][end-80:end]=bytes(80)
        with tempfile.TemporaryDirectory() as tmp:
            exe=os.path.join(tmp,'fixture.exe');im.build(exe)
            e,s,r=generate(exe)
        self.assertEqual(r['counts']['structRegs'],0)
        self.assertEqual(s['Outer']['props'][1]['type'],{'t':'StructProperty','struct':'Inner'})

if __name__=='__main__':unittest.main()
