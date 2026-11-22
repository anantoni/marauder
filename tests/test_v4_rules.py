"""Synthetic v4 cases: runtime records and FName display spelling (no game data)."""
import os,sys,unittest
from usmap_generator.engine import fname_order as F
from usmap_generator.engine.genusmap import runtime_records

def node(name,kind='Int',fn=None,children=()):
    return {'name':name,'kind':kind,'fn':fn,'dim':1,'children':list(children)}

class RuntimeRecords(unittest.TestCase):
    def test_metaclass_defaults_are_always_present(self):
        e,s={},{}
        runtime_records(e,s,'4.26',{'prop':'P4x'})
        self.assertEqual(s['Default__Class'],{'super':None,'count':0,'props':[]})
        self.assertEqual(s['Default__ScriptStruct'],{'super':None,'count':0,'props':[]})
        self.assertEqual(e['Default__Enum'],[])

    def test_niagara_simulation_copy_narrows_large_world_members_from_5_2(self):
        vec={'t':'StructProperty','struct':'Vector'}
        src={'super':None,'count':2,'props':[{'i':0,'dim':1,'name':'Position','type':vec},
                                            {'i':1,'dim':1,'name':'Time','type':{'t':'DoubleProperty'}}]}
        for vs,chosen,expected in (('5.2',{'prop':'P51'},True),('5.1',{'prop':'P51'},False),
                                    (None,{'prop':'P5x'},True),(None,{'prop':'P51'},False),('4.27',{'prop':'P4x'},False)):
            s={'ChaosDestructionEvent':src};runtime_records({},s,vs,chosen)
            self.assertEqual('ChaosDestructionEvent_SWC' in s,expected,(vs,chosen))
        s={'ChaosDestructionEvent':src};runtime_records({},s,'5.6',{'prop':'P5x'})
        props=s['ChaosDestructionEvent_SWC']['props']
        self.assertEqual(props[0]['type'],{'t':'StructProperty','struct':'Vector3f'})
        self.assertEqual(props[1]['type'],{'t':'FloatProperty'})
        self.assertEqual(src['props'][0]['type'],vec)          # the source record is unchanged

class DisplaySpelling(unittest.TestCase):
    def blocks(self):
        names=['ByteProperty','IntProperty','Filename','ID']
        ansi=b'\0'+b''.join(n.encode()+b'\0'*(8-len(n)%8) for n in names)+b'\0'*8
        wide=b'\0\0'+b''.join(n.encode('utf-16-le')+b'\0'*4 for n in ['ByteProperty','IntProperty','Lerp'])+b'\0\0\0\0'
        return ansi+b'\xff'*(16+len(ansi)%2)+wide

    def test_hardcoded_lists_fill_each_others_gaps(self):
        self.assertEqual(F.hardcoded(self.blocks()),['None','ByteProperty','IntProperty','Filename','ID','Lerp'])

    def test_first_registration_wins_and_types_come_first(self):
        S={'Inner':{'va':2,'name':'Inner','tree':[node('timestamp')],'super_fn':None},
           'Outer':{'va':1,'name':'Outer','tree':[node('TimeStamp'),node('Child','Struct',fn=9)],'super_fn':None}}
        C={'Thing':{'va':3,'name':'Thing','tree':[node('Url'),node('FileName')],'deps':[],'funcs':[],'ffns':[]}}
        E={'EMode':{'va':4,'name':'EMode','entries':[('URL',0),('EOther::TIMESTAMP',1)]}}
        order=F.registration_order(['None','Filename'],[],S,C,E,{9:('Inner','code')},{})
        # Properties are constructed from the end, so Outer's struct reference builds Inner first.
        self.assertEqual(order['timestamp'][0],'timestamp')
        self.assertEqual(order['filename'][0],'Filename')     # hardcoded
        self.assertEqual(order['url'][0],'Url')               # property before unqualified enum entry
        self.assertNotIn('eother::timestamp',order)
        e,s,renamed=F.apply({'EMode':[(0,'URL')],'EOther':[(1,'TIMESTAMP')]},
                            {'Thing':{'super':None,'count':2,'props':[{'i':0,'dim':1,'name':'FileName','type':{'t':'IntProperty'}}]}},
                            order,namespaced={'EOther'})
        self.assertEqual(s['Thing']['props'][0]['name'],'Filename')
        self.assertEqual(e['EMode'],[(0,'Url')])
        self.assertEqual(e['EOther'],[(1,'TIMESTAMP')])     # namespaced entries are their own FNames
        self.assertEqual(renamed,2)

if __name__=='__main__':unittest.main()
