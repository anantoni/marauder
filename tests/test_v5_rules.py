"""Synthetic v5 cases: UE 4.19-4.22 record layouts (no game data)."""
import os,struct,sys,tempfile,unittest
from usmap_generator.engine import usmap as U
from usmap_generator.engine.genusmap import generate, recover_legacy_types
from usmap_generator.engine.scan import Image, Scanner
from test_ue_usmap import Img, BASE

def legacy_image(layout):
    """Inner {X: int, Label: text} and Outer {Count: int, InnerItem: Inner, Mode: enum EMode}."""
    im=Img();T=lambda off:BASE+0x1000+off
    pkg,z_inner,z_enum=T(0x10),T(0x100),T(0x160)
    ss_inner,ops_inner,ss_outer,ops_outer=T(0x400),T(0x440),T(0x480),T(0x4C0)
    pd=bytearray()
    for a in (z_inner,z_enum):pd+=struct.pack('<III',a-BASE,a-BASE+0x2F,0x7000)
    for a in (ss_inner,ops_inner,ss_outer,ops_outer):pd+=struct.pack('<III',a-BASE,a-BASE+0x37,0x7010)
    im.buf['.pdata'][:len(pd)]=pd
    def prop(name,kind,off,fn=0):
        if layout=='P419':   # Type, Name, ObjectFlags, PropertyFlags, ArrayDim, RepNotify, Offset, tail
            r=struct.pack('<QQIxxxxQIxxxxQIxxxxQQ',kind,im.cstr(name),0x45,0,1,0,off,fn,0)
        else:                # 4.22: Name, RepNotify, PropertyFlags, Flags, ObjectFlags, ArrayDim, Offset, tail
            r=struct.pack('<QQQBxxxIIIQQ',im.cstr(name),0,0,kind,0x45,1,off,fn,0)
        return im.put('.rdata',r)
    def params(name,sup,ops,props,size):
        # UE4 arrays run in reverse declaration order, each container followed by its inner property.
        arr=im.put('.rdata',b''.join(struct.pack('<Q',p) for p in props)) if props else 0
        if layout=='P419':r=struct.pack('<QQQQIIQQQI',pkg,sup,ops,im.cstr(name),0x45,1,size,4,arr,len(props))
        else:r=struct.pack('<QQQQQQQIII',pkg,sup,ops,im.cstr(name),size,4,arr,len(props),0x45,1)
        return im.put('.data',r)
    params('Inner',0,ops_inner,[prop('Label',0x1C,8),prop('X',3,0)],0x20)
    params('Outer',0,ops_outer,[prop('Mode',0x1D,0x28,z_enum),prop('UnderlyingType',0,0),
                                 prop('InnerItem',0x19,8,z_inner),prop('Count',3,0)],0x30)
    for ss,ops,nm in ((ss_inner,ops_inner,'Inner'),(ss_outer,ops_outer,'Outer')):
        im.put('.rdata',struct.pack('<QQQQQ',ss,ops,im.wstr(nm),im.va('.data',0x1800),0))
    return im

class LegacyLayouts(unittest.TestCase):
    def check(self,layout,struct_layout):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'synth.exe');legacy_image(layout).build(p)
            e,s,rep=generate(p)
        chosen=rep['layouts']['chosen']
        self.assertEqual((chosen['prop'],chosen['struct']),(layout,struct_layout),rep['layouts'])
        self.assertEqual([x['name'] for x in s['Outer']['props']],['Count','InnerItem','Mode'])
        self.assertEqual(U.fmt(s['Outer']['props'][1]['type']),'Struct<Inner>')
        self.assertEqual(s['Outer']['props'][2]['type']['t'],'EnumProperty')     # 0x1D: Enum before 4.23
        self.assertEqual(U.fmt(s['Inner']['props'][1]['type']),'Text')            # 0x1C: Text before 4.23
        self.assertEqual(rep['engineVersionEstimate']['basis'][:4],{'P419':'type','P422':'name'}[layout])

    def test_type_first_records_of_4_19_to_4_21(self):
        self.check('P419','S419')

    def test_early_property_kinds_of_4_22(self):
        self.check('P422','S51')

    def test_legacy_enum_and_class_params(self):
        im=Img();T=lambda off:BASE+0x1000+off
        vals=im.put('.rdata',struct.pack('<QqQq',im.cstr('EMode::A'),0,im.cstr('EMode::B'),1))
        # OuterFunc, DynamicType, Name, ObjectFlags, DisplayNameFunc, CppForm, CppType, Values, Num
        im.put('.data',struct.pack('<QQQQQQQQI',T(0x10),0,im.cstr('EMode'),0x45,0,2,im.cstr('EMode'),vals,2))
        deps=im.put('.rdata',struct.pack('<Q',T(0x20)))
        props=im.put('.rdata',struct.pack('<Q',im.put('.rdata',struct.pack('<QQIxxxxQIxxxxQIxxxxQQ',3,im.cstr('Health'),0x45,0,1,0,8,0,0))))
        # NoRegister, Deps, NumDeps|ClassFlags, Functions, NumFunctions, Properties, NumProperties,
        # Config, CppClassInfo, Interfaces, NumInterfaces
        im.put('.data',struct.pack('<QQIIQQQQQQQQ',T(0x30),deps,1,0x80,0,0,props,1,0,im.va('.data',0x1800),0,0))
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'synth.exe');im.build(p)
            sc=Scanner(Image(p));sc.prepare()
            enums=sc.enums_raw('E419');classes=sc.classes_raw('C419')
        self.assertEqual([(x['name'],x['entries'],x['form']) for x in enums],[('EMode',[('EMode::A',0),('EMode::B',1)],2)])
        self.assertEqual([(x['noreg'],x['deps'],x['n']) for x in classes],[(T(0x30),[T(0x20)],1)])

    def test_partial_legacy_property_descriptor_recovers_known_component(self):
        im = Img()
        im.wstr("UAudioCaptureComponent")
        im.cstr("/Script/AudioCapture")
        prop = im.cstr("JitterLatencyFrames")
        va = im.put(".data", struct.pack("<QQ", 3, prop) + b"\0" * 0x28)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "synth.exe")
            im.build(path)
            image = Image(path)
            records = {"SynthComponent": {}}
            self.assertEqual(recover_legacy_types(image, records, "4.20"), [])
            self.assertEqual(recover_legacy_types(image, {}, "4.19"), [])
            self.assertEqual(recover_legacy_types(image, records, "4.19"), ["AudioCaptureComponent"])
            record = records.pop("AudioCaptureComponent")
            self.assertEqual((record["super"], record["count"]), ("SynthComponent", 1))
            self.assertEqual(record["props"], [
                {"i": 0, "dim": 1, "name": "JitterLatencyFrames", "type": {"t": "IntProperty"}}
            ])
            image.d[image.off(va + 0x20)] = 2
            self.assertEqual(recover_legacy_types(image, records, "4.19"), [])

class RegistrationOrder(unittest.TestCase):
    def test_numbered_names_share_the_first_registered_stem(self):
        from usmap_generator.engine import fname_order as F
        self.assertEqual(F.name_parts("Size_0"), ("Size", "_0"))
        self.assertEqual(F.name_parts("Size_2147483646"), ("Size", "_2147483646"))
        for name in ("Size_01", "Size_2147483647", "Size_99999999999", "_1"):
            self.assertEqual(F.name_parts(name), (name, ""))

        order = F.registration_order([], ["Node_8"], {}, {}, {}, {}, {})
        record = {"super": None, "count": 1, "props": [{"name": "NODE_1"}]}
        enums = {"EMode": [(0, "NODE_1"), (1, "NODE_2"), (2, "NODE_MAX")],
                 "EQualified": [(0, "node_3")]}
        emitted_enums, structs, _ = F.apply(enums, {"Owner": record}, order, {"EQualified"})
        self.assertEqual(structs["Owner"]["props"][0]["name"], "Node_1")
        self.assertEqual(emitted_enums, {"EMode": [(0, "Node_1"), (1, "Node_2"), (2, "Node_MAX")],
                                         "EQualified": [(0, "node_3")]})

    def test_native_functions_and_static_names_precede_reflected_members(self):
        from usmap_generator.engine import fname_order as F
        node=lambda name:{'name':name,'kind':'Int','fn':None,'dim':1,'children':[]}
        S={'Box':{'va':1,'name':'Box','tree':[node('Max'),node('Pitch')],'super_fn':None,'ops':7}}
        C={'Math':{'va':2,'name':'Math','tree':[],'deps':[],'funcs':['max'],'ffns':[0]}}
        order=F.registration_order(['None'],['pitch'],S,C,{},{},{})
        self.assertEqual(order['max'][0],'max')            # registered with its class
        self.assertEqual(order['pitch'],('pitch','static'))  # CRT static FName before reflection
        self.assertEqual(order['box'][1],'type')

    def test_hardcoded_names_come_from_the_registering_code_without_pooling_gaps(self):
        from usmap_generator.engine import fname_order as F
        from usmap_generator.engine.code_links import CodeLinks
        from usmap_generator.engine.genusmap import im_functions
        import test_ue_usmap
        from unittest import mock
        patch=mock.patch.object(test_ue_usmap,'SECS',[x for x in test_ue_usmap.SECS if x[0]!='.bind'])   # unprotected
        patch.start();self.addCleanup(patch.stop)
        im=Img();names=['ByteProperty','IntProperty','ID','Filename']
        lits=[im.cstr(n) for n in names[:2]];im.cstr('pooled elsewhere');lits+=[im.cstr(n) for n in names[2:]]
        body=bytearray()
        start=BASE+0x1000+0x200
        for v in lits:
            at=start+len(body);body+=b'\x48\x8d\x15'+struct.pack('<i',v-(at+7))
        body+=b'\xc3'
        im.buf['.text'][0x200:0x200+len(body)]=body
        im.buf['.pdata'][:12]=struct.pack('<III',start-BASE,start-BASE+len(body),0x7000)
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'synth.exe');im.build(p)
            image=Image(p);links=CodeLinks(image,im_functions(image))
            self.assertEqual(F.hardcoded(image.d,image,links),['None']+names)
            self.assertEqual(F.hardcoded(image.d),['None','ByteProperty','IntProperty'])   # the data block has a gap

class LegacyBootstrap(unittest.TestCase):
    def test_naming_parent_calibrates_on_core_classes(self):
        from types import SimpleNamespace
        from usmap_generator.engine.code_links import CodeLinks
        core={'Class':'Struct','Struct':'Field','Field':'Object','Function':'Struct'}
        evidence={};parents={}
        for i,(name,parent) in enumerate(list(core.items())+[('PackageMap','Object'),('Model','Object')]):
            package='/Script/Engine' if name=='Model' else '/Script/CoreUObject'
            evidence[i]={'identity':(package,name),'naming_parent':100+i};parents[100+i]=('/Script/CoreUObject',parent)
        links=SimpleNamespace(bootstrap_evidence=evidence,class_result=parents.get)
        out,audit=CodeLinks.naming_parent_schemas(links,core)
        self.assertEqual({n:x['super'] for n,x in out.items()},{'PackageMap':'Object','Model':'Object'})
        self.assertEqual(audit['naming_parent_matches'],4)
        parents[100]=('/Script/CoreUObject','Object')         # one contradicted core parent: fail closed
        self.assertEqual(CodeLinks.naming_parent_schemas(links,core)[0],{})

if __name__=='__main__':unittest.main()
