"""Optional bounded static constructor links for ordinary x64 PE code.

No execution, patching, unpacking, emulation, or reference input. Wrapper
markers reject the mode before any code read, with one exception (owner decision, 22 Nov
2026): a SteamStub wrapper keeps its own code in the separate .bind section. The game's
.text may then be stored plain. plain_code_check() reads .text only (never the wrapper
section) and admits it only when it is verifiably plain: low entropy in every sampled block,
nearly every sampled .pdata function decodes as x86-64 to its end with branch targets inside
.text, and enough functions are sampled. The generator then also requires that the known
constructor patterns resolve (genusmap.generate); otherwise code links stay off. The default
generator continues to use data only. Instruction bytes and addresses are private evidence.
"""
import bisect,re,struct
from collections import Counter,defaultdict

# Wrappers whose own code lives in a separate section, so the game's code section can be plain.
PLAIN_CODE_WRAPPERS = {'.bind'}
PROTECTED = {'.bind','upx0','upx1','.vmp0','.vmp1','.vmp2','.themida','.enigma','.winlice'}
# Legacy engine reflection classes have no own reflected fields unless a validated
# FClassParams record supplies them. Never extend this rule to arbitrary native classes.
LEGACY_EMPTY_INTRINSICS = {
    'ArrayProperty','BoolProperty','ByteProperty','ClassProperty','DelegateProperty','DoubleProperty',
    'EnumProperty','FloatProperty','Int16Property','Int64Property','Int8Property','IntProperty',
    'InterfaceProperty','LazyObjectProperty','MapProperty','MulticastDelegateProperty',
    'MulticastDelegatePropertyWrapper','MulticastInlineDelegateProperty',
    'MulticastInlineDelegatePropertyWrapper','MulticastSparseDelegateProperty','NameProperty',
    'NumericProperty','ObjectProperty','ObjectPropertyBase','Property','PropertyWrapper','SetProperty',
    'SoftClassProperty','SoftObjectProperty','StrProperty','StructProperty','TextProperty',
    'UInt16Property','UInt32Property','UInt64Property','WeakObjectProperty','DynamicClass',
    'GCObjectReferencer','LinkerPlaceholderClass','LinkerPlaceholderExportObject',
    'LinkerPlaceholderFunction','MetaData','Model','ObjectRedirector','PackageMap','TextBuffer',
    'ObjectReachabilityStressData','InstanceDataObjectStruct',
}

def _entropy(block):
    import math
    if not block:return 0.0
    c=Counter(block);n=len(block)
    return -sum(v/n*math.log2(v/n) for v in c.values())


def plain_code_check(image, functions, blocks=128, block_size=1<<16, sample=2000, min_functions=200,
                     max_block_entropy=7.2, min_block_entropy=3.5, min_decoded=0.95, min_targets=0.98):
    """Is .text stored as plain x86-64? Reads .text only. Returns (ok, evidence)."""
    import capstone
    ev={'sections_read':['.text'],'reason':None}
    if '.text' not in image.pe.secs: ev['reason']='no .text';return False,ev
    lo,hi,raw=image.pe.sec('.text');size=hi-lo
    md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    with open(image.pe.path,'rb') as f:
        def read(va,n):
            f.seek(raw+va-lo);return f.read(max(0,min(n,hi-va)))
        step=max(block_size,size//blocks);ent=[]
        for off in range(0,max(1,size-block_size+1),step):
            ent.append(_entropy(read(lo+off,block_size)))
        ev['entropy_blocks']=len(ent);ev['entropy_max']=round(max(ent),3) if ent else None
        ev['entropy_min']=round(min(ent),3) if ent else None
        if not ent or max(ent)>max_block_entropy:ev['reason']='entropy above plain-code range';return False,ev
        if min(ent)<min_block_entropy:ev['reason']='entropy below code range (empty or filled section)';return False,ev
        starts=sorted(a for a,(n,_) in functions.items() if lo<=a<hi and 0<n<=16384)
        picked=starts[::max(1,len(starts)//sample)][:sample]
        ev['functions_sampled']=len(picked)
        if len(picked)<min_functions:ev['reason']='too few functions to verify';return False,ev
        whole=0;targets=inside=0
        for a in picked:
            n=functions[a][0];done=0
            for ins in md.disasm_lite(read(a,n),a):
                done=ins[0]+ins[1]-a
                m=ins[2]
                if m=='call' or m[0]=='j':
                    try:t=int(ins[3],16)
                    except ValueError:continue
                    targets+=1;inside+=lo<=t<hi
            whole+=done>=n
        ev['decoded_fraction']=round(whole/len(picked),4)
        ev['branch_targets']=targets;ev['branch_targets_inside']=round(inside/targets,4) if targets else None
        if whole/len(picked)<min_decoded:ev['reason']='functions do not decode as x86-64';return False,ev
        if not targets or inside/targets<min_targets:ev['reason']='branch targets leave .text';return False,ev
    return True,ev


class CodeLinks:
    def __init__(self, image, functions, plain_wrapped=True):
        self.im=image; self.functions=functions; self.starts=sorted(functions)
        markers=sorted(n for n in image.pe.secs if n.lower() in PROTECTED)
        self.blocked=markers; self.wrapper=[]; self.plain_check=None
        if markers and plain_wrapped and all(n.lower() in PLAIN_CODE_WRAPPERS for n in markers):
            ok,self.plain_check=plain_code_check(image,functions)
            self.plain_check['wrapper_sections_unread']=markers
            if ok: self.blocked=[]; self.wrapper=markers
        self.stats=Counter(); self.scanned={}
        self.class_slots=defaultdict(set);self.class_records={};self.singleton_copies=defaultdict(set);self.singleton_copy_writers=defaultdict(set);self.parameter_outputs=defaultdict(set);self.class_parameters={}
        self.md=None
        self.bootstrap_checked=set();self.bootstrap_evidence={}
        if not self.blocked:
            import capstone
            self.cs=capstone
            self.md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
            self.md.detail=True

    def summary(self):
        status='protected; code unread' if self.blocked else ('enabled: plain .text beside wrapper section(s) %s (wrapper unread)'%','.join(self.wrapper) if self.wrapper else 'enabled')
        out={'mode':'unprotected static code links','status':status,'protection_markers':self.blocked,**dict(self.stats)}
        if self.plain_check is not None:
            out['plain_code_check']=self.plain_check
            out['code_sections_read']=self.plain_check['sections_read']
            out['wrapper_sections_unread']=self.plain_check['wrapper_sections_unread']
            if self.plain_check['reason']:
                out['plain_code_rejected']={'reason':self.plain_check['reason']}
        if self.wrapper:
            out['code_sections_read']=['.text']; out['wrapper_sections']=self.wrapper
        return out

    def read(self, va, size):
        if self.blocked: raise ValueError('Protected executable: code reads forbidden')
        lo,hi,raw=self.im.pe.sec('.text')
        if not lo <= va < hi or size<0 or va+size>hi: return b''
        with open(self.im.pe.path,'rb') as f:
            f.seek(raw+va-lo); data=f.read(size)
        self.stats['code_bytes_read']+=len(data)
        return data

    def instructions(self, va):
        if self.blocked or not self.im.is_text(va):return []
        known=self.functions.get(va)
        length=known[0] if known else 256
        if not 0<length<=16384:return []
        length=min(length,self.im.text[1]-va)
        ins=[]
        for item in self.md.disasm(self.read(va,length),va):
            ins.append(item)
            if item.mnemonic in ('int3','ud2','hlt') or (not known and item.mnemonic in ('ret','retf','jmp')):break
        self.stats['functions_decoded']+=1
        return ins

    def wide(self, va, package=False):
        if not va or not self.im.is_data(va):return None
        off=self.im.off(va)
        if off is None:return None
        out=[]
        for i in range(256):
            if off+2*i+2>len(self.im.d):return None
            c=struct.unpack_from('<H',self.im.d,off+2*i)[0]
            if not c:break
            ch=chr(c)
            if ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_/.':return None
            out.append(ch)
        else:return None
        value=''.join(out)
        if package:return value if re.fullmatch(r'/Script/[A-Za-z_][A-Za-z0-9_]*',value) else None
        return value if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value) else None

    def scan(self, va):
        """Record arguments at ABI call boundaries; never interpret arbitrary string proximity."""
        if va in self.scanned:return self.scanned[va]
        if self.blocked:return {'params':set(),'classes':set(),'calls':set(),'returns':set(),'return_calls':set(),'return_call_sites':set(),'return_argument_calls':set(),'return_arguments':set(),'unknown_returns':True,'call_sites':[]}
        C=self.cs;X=C.x86_const;params=set();classes=set();calls=set();returns=set();return_calls=set();return_call_sites=set();return_argument_calls=set();return_arguments=set();pending=[];copies=[];outputs=[];call_sites=[];vtable_writes=set();decoded_addresses=set();unknown_returns=False
        def canon(reg):
            name=self.md.reg_name(reg)
            if not name:return ''
            for full,aliases in (('rax',('eax','ax','al','ah')),('rbx',('ebx','bx','bl','bh')),
                                 ('rcx',('ecx','cx','cl','ch')),('rdx',('edx','dx','dl','dh')),
                                 ('rsp',('esp','sp','spl')),('rbp',('ebp','bp','bpl')),
                                 ('rsi',('esi','si','sil')),('rdi',('edi','di','dil'))):
                if name in aliases:return full
            return re.sub(r'^(r(?:[89]|1[0-5]))[dwb]$',r'\1',name)
        # A RUNTIME_FUNCTION can describe only a hot/cold fragment. Traverse actual
        # conditional edges and fallthroughs, carrying constant ABI facts per path.
        arguments={n:('argument',i) for i,n in enumerate(('rcx','rdx','r8','r9'))}
        work=[(va,{},arguments,{'rsp':0},{})];seen=set();steps=0
        while work and len(seen)<256 and steps<4096:
            pc,values,origins,frames,stack=work.pop()
            state=(pc,tuple(sorted(values.items())),tuple(sorted(origins.items())),tuple(sorted(frames.items())),tuple(sorted(stack.items())))
            if state in seen or not self.im.is_text(pc):continue
            seen.add(state)
            i=bisect.bisect_right(self.starts,pc)-1
            entry=self.starts[i] if i>=0 else None
            end=entry+self.functions[entry][0] if entry is not None else 0
            length=min(end-pc if pc<end else 256,4096,self.im.text[1]-pc)
            if length<=0:continue
            block=[]
            for item in self.md.disasm(self.read(pc,length),pc):
                block.append(item)
                if item.group(C.CS_GRP_JUMP) or item.mnemonic.startswith('ret') or item.mnemonic in ('int3','ud2','hlt'):break
            self.stats['blocks_decoded']+=1
            for ins in block:
                decoded_addresses.add(ins.address)
                steps+=1;ops=ins.operands;value=None;dest=None;origin=None;frame_value=None
                if ins.mnemonic in ('lea','mov','movabs') and len(ops)==2 and ops[0].type==X.X86_OP_REG:
                    dest=canon(ops[0].reg);src=ops[1]
                    if src.type==X.X86_OP_REG:
                        source=canon(src.reg);value=values.get(source);origin=origins.get(source);frame_value=frames.get(source)
                    elif src.type==X.X86_OP_IMM:value=src.imm
                    elif src.type==X.X86_OP_MEM and not src.mem.index:
                        source_origin=origins.get(canon(src.mem.base))
                        base=ins.address+ins.size if src.mem.base==X.X86_REG_RIP else values.get(canon(src.mem.base))
                        if base is not None:
                            address=base+src.mem.disp
                            if ins.mnemonic=='lea':value=address;origin=('address',address)
                            elif self.im.is_data(address):
                                value=self.im.rd(address,'<Q' if ops[0].size==8 else '<I')
                                if ops[0].size==8:origin=('data',address)
                        if ins.mnemonic=='lea' and canon(src.mem.base) in frames:frame_value=frames[canon(src.mem.base)]+src.mem.disp
                        if ins.mnemonic=='mov' and ops[0].size==8 and source_origin:
                            if source_origin[0]=='data' and src.mem.disp==0:origin=('vtable',source_origin[1])
                            elif source_origin[0]=='vtable' and 0<=src.mem.disp<=0x1000 and src.mem.disp%8==0:
                                origin=('virtual',source_origin[1],src.mem.disp)
                elif ins.mnemonic=='xor' and len(ops)==2 and ops[0].type==ops[1].type==X.X86_OP_REG and ops[0].reg==ops[1].reg:
                    dest=canon(ops[0].reg);value=0
                elif ins.mnemonic in ('add','sub') and len(ops)==2 and ops[0].type==X.X86_OP_REG and ops[1].type==X.X86_OP_IMM:
                    dest=canon(ops[0].reg)
                    delta=ops[1].imm*(1 if ins.mnemonic=='add' else -1)
                    if dest in frames:frame_value=frames[dest]+delta
                    if dest in values and ops[0].size in (4,8):
                        value=(values[dest]+delta)&((1<<(8*ops[0].size))-1)
                        if ops[0].size==8 and origins.get(dest,())[0:1]==('address',):origin=('address',value)
                elif ins.mnemonic in ('push','pop') and 'rsp' in frames:
                    dest='rsp';frame_value=frames['rsp']+(-8 if ins.mnemonic=='push' else 8)
                elif ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==X.X86_OP_MEM and canon(ops[0].mem.base) in frames and not ops[0].mem.index:
                    slot=frames[canon(ops[0].mem.base)]+ops[0].mem.disp;src=ops[1]
                    v=src.imm if src.type==X.X86_OP_IMM else values.get(canon(src.reg)) if src.type==X.X86_OP_REG else None
                    provenance=origins.get(canon(src.reg)) if src.type==X.X86_OP_REG else None
                    if v is not None:stack[slot]=(v,provenance)
                    else:stack.pop(slot,None)
                elif ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==X.X86_OP_MEM and ops[0].mem.base==X.X86_REG_RIP and ops[1].type==X.X86_OP_REG and ops[1].size==8:
                    target=ins.address+ins.size+ops[0].mem.disp;source=origins.get(canon(ops[1].reg))
                    if self.im.is_data(target) and source is not None and source[0] in ('data','call'):copies.append((target,source))
                elif (ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==X.X86_OP_MEM
                      and not ops[0].mem.index and ops[0].mem.disp==0 and ops[0].size==8
                      and origins.get(canon(ops[0].mem.base))==('argument',0)
                      and ops[1].type==X.X86_OP_REG):
                    source=origins.get(canon(ops[1].reg))
                    if source and source[0]=='address' and self.im.is_data(source[1]):vtable_writes.add(source[1])
                try:
                    for reg in ins.regs_access()[1]:
                        name=canon(reg)
                        if ins.mnemonic=='call' and name=='rsp':continue
                        values.pop(name,None);origins.pop(name,None);frames.pop(name,None)
                except C.CsError:values.clear();origins.clear();frames.clear();stack.clear()
                if dest is not None:
                    full_write=ins.mnemonic in ('push','pop') or not ops or ops[0].size>=4
                    if full_write and value is not None:values[dest]=value
                    if full_write and origin is not None:origins[dest]=origin
                    if frame_value is not None and (ins.mnemonic in ('push','pop') or ops[0].size==8):frames[dest]=frame_value
                if ins.mnemonic in ('call','jmp'):
                    if len(ops)==1 and ops[0].type==X.X86_OP_IMM and self.im.is_text(ops[0].imm):calls.add(ops[0].imm)
                    if ins.mnemonic=='call' and values.get('rdx'):params.add(values['rdx'])
                    if ins.mnemonic=='call' and origins.get('rcx',())[0:1]==('address',) and origins.get('rdx',())[0:1]==('address',):
                        out,record=values.get('rcx'),values.get('rdx')
                        data=self.im.pe.secs.get('.data')
                        if out and record and data and self.im.pe.base+data[0]<=out<self.im.pe.base+data[0]+data[1] and self.im.is_data(record):
                            outputs.append((out,record))
                    package=self.wide(values.get('rcx'),package=True);name=self.wide(values.get('rdx'))
                    args={k-frames['rsp']:v[0] for k,v in stack.items()} if 'rsp' in frames else {}
                    addresses={k-frames['rsp'] for k,v in stack.items() if v[1] and v[1][0]=='address'} if 'rsp' in frames else set()
                    size,alignment,flags=(args.get(k) for k in (0x20,0x28,0x30))
                    valid_class=(size is not None and 0x28<=size<1<<24 and alignment in (4,8,16,32,64)
                                 and flags is not None and 0<=flags<1<<32)
                    # UE 4.22 and older pass no alignment: InSize, InClassFlags, InClassCastFlags.
                    legacy_class=(not valid_class and size is not None and 0x28<=size<1<<24 and alignment is not None
                                  and 0<=alignment<1<<32 and flags is not None and 0<=flags<1<<64)
                    if legacy_class:args=dict(args);args['abi']='legacy';args[0x30]=alignment;valid_class=True
                    if ins.mnemonic=='call' and valid_class and package and name and self.im.is_data(values.get('r8')) and self.im.tz(values.get('r9')):
                        identity=(package,name.removeprefix('DEPRECATED_'));classes.add(identity)
                        slot=values['r8']
                        pending.append((slot,{'identity':identity,'initializer':va,'initializer_callback':values.get('r9'),'arguments':args,'address_arguments':addresses}))
                    if ins.mnemonic=='call':
                        target=ops[0].imm if len(ops)==1 and ops[0].type==X.X86_OP_IMM and self.im.is_text(ops[0].imm) else None
                        iat=ins.address+ins.size+ops[0].mem.disp if len(ops)==1 and ops[0].type==X.X86_OP_MEM and ops[0].mem.base==X.X86_REG_RIP else None
                        callback_origin=origins.get(canon(ops[0].reg)) if len(ops)==1 and ops[0].type==X.X86_OP_REG else None
                        virtual=callback_origin[1:] if callback_origin and callback_origin[0]=='virtual' else None
                        if len(ops)==1 and ops[0].type==X.X86_OP_MEM and not ops[0].mem.index:
                            base_origin=origins.get(canon(ops[0].mem.base))
                            if base_origin and base_origin[0]=='vtable':virtual=(base_origin[1],ops[0].mem.disp)
                        call_sites.append({'address':ins.address,'target':target,'iat':iat,
                            'virtual':virtual,
                            'registers':{n:values[n] for n in ('rcx','rdx','r8','r9') if n in values},
                            'origins':{n:origins[n] for n in ('rcx','rdx','r8','r9') if n in origins},
                            'addresses':{n for n in ('rcx','rdx','r8','r9') if origins.get(n,())[0:1]==('address',)},
                            'stack':dict(args)})
                        from .compiler_abi import preserves_return_register
                        preserved=preserves_return_register(self,target)
                        old_return=(values.get('rax'),origins.get('rax'),frames.get('rax'))
                        for name in ('rax','rcx','rdx','r8','r9','r10','r11'):
                            values.pop(name,None);origins.pop(name,None);frames.pop(name,None)
                        if preserved:
                            for facts,value in zip((values,origins,frames),old_return):
                                if value is not None:facts['rax']=value
                            self.stats['compiler_preserved_return_calls']+=1
                        elif target is not None:origins['rax']=('call',target,ins.address)
                        elif callback_origin and callback_origin[0]=='argument':origins['rax']=('argument_call',callback_origin[1])
                        stack.clear()
                if ins.mnemonic.startswith('ret'):
                    source=origins.get('rax')
                    if source and source[0]=='data':returns.add(source[1])
                    elif source and source[0]=='call':return_calls.add(source[1]);return_call_sites.add((source[1],source[2]))
                    elif source and source[0]=='argument_call':return_argument_calls.add(source[1])
                    elif source and source[0]=='argument':return_arguments.add(source[1])
                    else:unknown_returns=True
            if not block:continue
            last=block[-1]
            def enqueue(target):work.append((target,dict(values),dict(origins),dict(frames),dict(stack)))
            if last.group(C.CS_GRP_JUMP):
                if last.operands[0].type==X.X86_OP_IMM:enqueue(last.operands[0].imm)
                if last.mnemonic!='jmp':enqueue(last.address+last.size)
            elif not last.mnemonic.startswith('ret') and last.mnemonic not in ('jmp','int3','ud2','hlt'):enqueue(last.address+last.size)
        if work:
            params.clear();classes.clear();calls.clear();returns.clear();return_calls.clear();return_call_sites.clear();return_argument_calls.clear();return_arguments.clear();pending.clear();copies.clear();outputs.clear();call_sites.clear();vtable_writes.clear();decoded_addresses.clear();unknown_returns=True
            self.stats['control_flow_limit']+=1
        for slot,record in pending:
            self.class_slots[slot].add(record['identity']);self.class_records[slot]=record
        for target,source in copies:
            self.singleton_copies[target].add(source);self.singleton_copy_writers[(target,source)].add(va)
        for slot,record in outputs:self.parameter_outputs[slot].add(record)
        self.stats['functions_decoded']+=1
        result={'params':params,'classes':classes,'calls':calls,'returns':returns,'return_calls':return_calls,'return_call_sites':return_call_sites,'return_argument_calls':return_argument_calls,'return_arguments':return_arguments,'unknown_returns':unknown_returns,'call_sites':call_sites,'vtable_writes':vtable_writes,'decoded_addresses':decoded_addresses}
        self.scanned[va]=result
        return result

    def startup_callbacks(self):
        """Declared C++ initializer table reached from the PE entrypoint via _initterm.

        Import names establish the ABI. Pointer ranges must be constant, unambiguous,
        aligned data arrays containing only code pointers or nulls. No callback runs.
        """
        if self.blocked:return []
        if hasattr(self,'_startup_callbacks'):return list(self._startup_callbacks)
        imports=self.im.pe.imports()
        def crt(module):return module in ('ucrtbase.dll','msvcrt.dll') or module.startswith('api-ms-win-crt-runtime-') and module.endswith('.dll')
        wanted={p for p,n in imports.items() if n=='_initterm' and crt(self.im.pe.import_modules.get(p,''))}
        if not wanted:return []
        X=self.cs.x86_const;thunks={}
        def is_initterm(site):
            if site['iat'] in wanted:return True
            target=site['target']
            if target not in thunks:
                thunks[target]=False
                if target:
                    ins=list(self.md.disasm(self.read(target,16),target))
                    ins=[i for i in ins if i.mnemonic not in ('nop','endbr64')]
                    if ins and ins[0].mnemonic=='jmp' and len(ins[0].operands)==1:
                        op=ins[0].operands[0]
                        thunks[target]=op.type==X.X86_OP_MEM and op.mem.base==X.X86_REG_RIP and ins[0].address+ins[0].size+op.mem.disp in wanted
            return thunks[target]
        pending=[(self.im.pe.entrypoint,0)];seen=set()
        while pending and len(seen)<64:
            fn,depth=pending.pop(0)
            if fn in seen or not self.im.is_text(fn):continue
            seen.add(fn)
            if fn in self.functions and self.functions[fn][0]>4096:continue
            result=self.scan(fn);ranges=set();unknown=False
            for site in result['call_sites']:
                if not is_initterm(site):continue
                regs=site['registers']
                if not {'rcx','rdx'}<=site['addresses']:unknown=True;continue
                start,end=regs['rcx'],regs['rdx']
                if start%8 or end%8 or not 0<end-start<=8*65536 or not self.im.is_data(start) or not self.im.is_data(end-1):unknown=True;continue
                ranges.add((start,end))
            if ranges or unknown:
                if unknown or len(ranges)!=1:self.stats['startup_table_ambiguous']+=1;return []
                start,end=next(iter(ranges));callbacks=[self.im.Q(p) for p in range(start,end,8)]
                if not all(self.im.tz(p) for p in callbacks):self.stats['startup_table_invalid']+=1;return []
                self.startup_table=(start,end);self.stats['startup_callbacks']=len(callbacks)
                self._startup_callbacks=[p for p in callbacks if p]
                return [p for p in callbacks if p]
            if depth<3:pending.extend((target,depth+1) for target in sorted(result['calls']))
        return []

    def metadata_initializers(self, scanner, pointers):
        """Recover only constant scalar property metadata from admitted CRT leaf initializers.

        The input file remains untouched. Every accepted callback must have a complete,
        branchless body, no calls or memory reads, and writes confined to supported
        fields of one otherwise invalid property record. Type-function addresses
        are constants, not invocations. Conflicting initializers fail closed.
        """
        from .layouts import PROP
        if self.blocked:return {}
        candidates={}
        data=self.im.pe.secs.get('.data')
        if not data:return {}
        dlo,dhi=self.im.pe.base+data[0],self.im.pe.base+data[0]+min(data[1],data[3])
        for ptr in set(pointers):
            if not ptr or not dlo<=ptr<dhi or ptr%8:continue
            # Name-first records (UE 4.22+), or UE 4.19-4.21 records: one-byte kind, then the name.
            type_first=not self.im.ident(self.im.Q(ptr)) and (self.im.Q(ptr) or 0)<=0xFF and self.im.ident(self.im.Q(ptr+8))
            if not type_first and not self.im.ident(self.im.Q(ptr)):continue
            if any(scanner.prop(ptr,family) is not None for family in PROP if bool(PROP[family].get('type_first'))==bool(type_first)):continue
            for family,layout in PROP.items():
                if bool(layout.get('type_first'))!=bool(type_first):continue
                if layout.get('legacy') and not type_first:continue     # 4.22 needs none (measured)
                if self.im.rd(ptr+layout['dim'][0],layout['dim'][1])!=0:continue
                if type_first:spans={(ptr+0x10,4),(ptr+0x18,8),(ptr+0x28,8)}   # ObjectFlags, PropertyFlags, RepNotify
                else:spans={(ptr+0x18,4),(ptr+0x1c,4)}
                spans|={(ptr+layout['dim'][0],struct.calcsize(layout['dim'][1])),
                       (ptr+layout['off'][0],struct.calcsize(layout['off'][1]))}
                allowed={a+i for a,n in spans for i in range(n)}
                tail={ptr+offset+i for offset in (layout['x1'],layout['x2']) for i in range(8)}
                candidates.setdefault(ptr,[]).append((family,allowed,tail))
        if not candidates:return {}
        callbacks=self.startup_callbacks()
        if not callbacks:return {}
        X=self.cs.x86_const;accepted={};conflicts=set();byte_owners=defaultdict(set)
        def key(register):
            name=self.md.reg_name(register)
            if not name:return None
            for full,aliases in [('rax',('eax','ax','al','ah')),('rbx',('ebx','bx','bl','bh')),
                    ('rcx',('ecx','cx','cl','ch')),('rdx',('edx','dx','dl','dh')),
                    ('rsi',('esi','si','sil')),('rdi',('edi','di','dil')),('rsp',('esp','sp','spl')),('rbp',('ebp','bp','bpl'))]:
                if name in aliases:return full
            return re.sub(r'^(r(?:[89]|1[0-5]))[dwb]$',r'\1',name)
        def constant(values,operand):
            if operand.type==X.X86_OP_IMM:return operand.imm
            if operand.type!=X.X86_OP_REG:return None
            name=self.md.reg_name(operand.reg)
            if name in ('ah','bh','ch','dh'):return None
            known=values.get(key(operand.reg))
            return known[0]&((1<<(8*operand.size))-1) if known and known[1]>=operand.size else None
        for callback in callbacks:
            known=self.functions.get(callback)
            length=min(known[0] if known else 256,256,self.im.text[1]-callback)
            values={};writes={};complete=False;valid=True
            for ins in self.md.disasm(self.read(callback,length),callback):
                ops=ins.operands
                if ins.mnemonic in ('nop','endbr64'):continue
                if ins.mnemonic=='ret':complete=True;break
                if ins.mnemonic in ('mov','movabs') and len(ops)==2 and ops[0].type==X.X86_OP_REG and ops[1].type==X.X86_OP_IMM:
                    name=self.md.reg_name(ops[0].reg)
                    if name in ('ah','bh','ch','dh'):valid=False;break
                    values[key(ops[0].reg)]=(ops[1].imm&((1<<(8*ops[0].size))-1),8 if ops[0].size==4 else ops[0].size);continue
                if (ins.mnemonic=='lea' and len(ops)==2 and ops[0].type==X.X86_OP_REG and ops[0].size==8
                    and ops[1].type==X.X86_OP_MEM and ops[1].mem.base==X.X86_REG_RIP and not ops[1].mem.index):
                    address=ins.address+ins.size+ops[1].mem.disp
                    if not (self.im.is_text(address) or self.im.is_data(address)):valid=False;break
                    values[key(ops[0].reg)]=(address,8);continue
                if ins.mnemonic in ('or','and','xor','add','sub') and len(ops)==2 and ops[0].type==X.X86_OP_REG and ops[1].type==X.X86_OP_IMM:
                    a,b=constant(values,ops[0]),ops[1].imm
                    if a is None:valid=False;break
                    value=({'or':lambda:a|b,'and':lambda:a&b,'xor':lambda:a^b,'add':lambda:a+b,'sub':lambda:a-b}[ins.mnemonic]())&((1<<(8*ops[0].size))-1)
                    values[key(ops[0].reg)]=(value,8 if ops[0].size==4 else ops[0].size);continue
                if ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==X.X86_OP_MEM and ops[0].mem.base==X.X86_REG_RIP and not ops[0].mem.index:
                    value=constant(values,ops[1])
                    address=ins.address+ins.size+ops[0].mem.disp;size=ops[0].size
                    if value is None or size not in (1,2,4,8) or not dlo<=address or address+size>dhi:valid=False;break
                    for i in range(size):
                        byte=(value>>(8*i))&255
                        if address+i in writes and writes[address+i]!=byte:valid=False
                        writes[address+i]=byte
                    continue
                valid=False;break
            if not valid or not complete or not writes:continue
            owners=[(ptr,family,base) for ptr,layouts in candidates.items() for family,base,tail in layouts if writes.keys()<=base|tail]
            if len({owner[0] for owner in owners})!=1:continue
            ptr=owners[0][0]
            original={a:self.im.d[self.im.off(a)] for a in writes}
            for a,v in writes.items():self.im.d[self.im.off(a)]=v
            supported=False
            refs={'Byte','Struct','Enum','Object','Interface','SoftObject','WeakObject','LazyObject','SoftClass',
                  'Delegate','InlineMulticastDelegate','SparseMulticastDelegate'}
            for p,family,base in owners:
                layout=PROP[family];record=scanner._prop(ptr,layout)
                if record is None:continue
                allowed=set(base)
                if record['kind'] in refs:
                    allowed.update(ptr+layout['x1']+i for i in range(8))
                elif record['kind']=='Bool' and not writes.keys()<=base and not layout['acc'] and layout['x1']==0x28 and layout['x2']==0x30:
                    if not 0<(self.im.Q(ptr+0x28) or 0)<1<<24 or not self.im.tz(self.im.Q(ptr+0x30)):continue
                    allowed.update(ptr+offset+i for offset in (0x28,0x30) for i in range(8))
                if writes.keys()<=allowed:supported=True
            for a,v in original.items():self.im.d[self.im.off(a)]=v
            if not supported:continue
            for a,v in writes.items():
                if a in accepted and accepted[a]!=v:conflicts.update(byte_owners[a]|{ptr})
                accepted[a]=v;byte_owners[a].add(ptr)
        for address in list(accepted):
            if byte_owners[address]&conflicts:accepted.pop(address)
        self.stats['metadata_initializer_bytes']=len(accepted)
        self.stats['metadata_initializer_conflicts']=len(conflicts)
        return accepted

    def constructor(self, root, records, depth=2):
        record=self.constructor_id(root,records,depth)
        return records[record] if record is not None else None

    def constructor_id(self, root, records, depth=2):
        """Join a known parameter record to the same mutable singleton the stub returns."""
        if self.blocked:return None
        def slot_records(slot,seen):
            key=('data',slot)
            if key in seen or len(seen)>=32:return set(),False
            hits=self.parameter_outputs.get(slot,set())&records.keys()
            if hits:return hits,len(hits)==1
            sources=self.singleton_copies.get(slot,set());out=set();complete=bool(sources)
            for source in sources:
                kind,target=source[:2]
                hits,ok=slot_records(target,seen|{key}) if kind=='data' else function_records(target,seen|{key})
                out.update(hits);complete &= ok
            return out,complete
        def function_records(fn,seen):
            key=('call',fn)
            if key in seen or len(seen)>=32:return set(),False
            scan=self.scan(fn);out=set();complete=not scan['unknown_returns'] and not scan['return_argument_calls'] and not scan['return_arguments']
            for slot in scan['returns']:
                hits,ok=slot_records(slot,seen|{key});out.update(hits);complete &= ok
            for target in scan['return_calls']:
                hits,ok=function_records(target,seen|{key});out.update(hits);complete &= ok
            return out,complete and bool(scan['returns'] or scan['return_calls'])
        hits,complete=function_records(root,set())
        return next(iter(hits)) if complete and len(hits)==1 else None

    def registration_id(self, root, records):
        """A returned constructor result, including a verified callback-returning adapter.

        Bind the adapter's first argument by its actual code pointer. A shared helper
        name or an unrelated call to the same helper cannot identify a returned type.
        """
        if self.blocked:return None
        def callback_result(helper,owner,seen,call_site=None):
            result=self.scan(helper)
            if (result['unknown_returns'] or result['returns'] or result['return_calls'] or result['return_arguments']
                or result['return_argument_calls']!={0}):return set(),False
            sites=[s for s in self.scan(owner)['call_sites'] if s['target']==helper and (call_site is None or s['address']==call_site)]
            ids=set()
            for site in sites:
                callback=site['registers'].get('rcx')
                if 'rcx' not in site['addresses'] or not self.im.is_text(callback):return set(),False
                record=self.constructor_id(callback,records)
                if record is None:return set(),False
                ids.add(record)
            return ids,bool(sites) and len(ids)==1
        def slot_result(slot,owner,seen):
            key=('data',slot)
            if key in seen or len(seen)>=32:return set(),False
            ids=self.parameter_outputs.get(slot,set())&records.keys()
            if ids:return ids,len(ids)==1
            sources=self.singleton_copies.get(slot,set());out=set();complete=bool(sources)
            if not any(owner in self.singleton_copy_writers.get((slot,source),set()) for source in sources):return set(),False
            for source in sources:
                kind,target=source[:2]
                if kind=='data':ids,ok=slot_result(target,owner,seen|{key})
                else:
                    writers=self.singleton_copy_writers.get((slot,source),set());ids=set();ok=bool(writers)
                    for writer in writers:
                        more,verified=callback_result(target,writer,seen|{key},source[2]);ids.update(more);ok &= verified
                out.update(ids);complete &= ok
            return out,complete
        def function_result(fn,seen):
            key=('call',fn)
            if key in seen or len(seen)>=32:return set(),False
            direct=self.constructor_id(fn,records)
            if direct is not None:return {direct},True
            result=self.scan(fn);out=set();complete=not result['unknown_returns'] and not result['return_argument_calls'] and not result['return_arguments']
            for slot in result['returns']:
                ids,ok=slot_result(slot,fn,seen|{key});out.update(ids);complete &= ok
            for helper,call_site in result['return_call_sites']:
                ids,ok=callback_result(helper,fn,seen|{key},call_site);out.update(ids);complete &= ok
            return out,complete and bool(result['returns'] or result['return_calls'])
        ids,complete=function_result(root,set())
        return next(iter(ids)) if complete and len(ids)==1 else None

    def package_name(self, root):
        """Verified returned FPackageParams singleton; package name is its first field."""
        if self.blocked or not self.im.is_text(root):return None
        candidates={}
        for param in self.scan(root)['params']:
            name=self.im.pe.cstr(self.im.Q(param)) if self.im.is_data(param) and self.im.Q(param) else None
            if name and re.fullmatch(r'/Script/[A-Za-z_][A-Za-z0-9_]*',name):candidates[param]=name
        record=self.constructor_id(root,candidates)
        return candidates.get(record)

    def class_name(self, root, depth=2):
        # Merely calling a class initializer does not prove the caller returns that class.
        return self.class_result(root)

    def bootstrap_class(self, root, result):
        """Name a manually bootstrapped intrinsic UClass by typed constructor/return links.

        Its constructor must return its first argument, receive the native UClass
        size/alignment/flags/config/callback ABI, and act on the same allocation
        stored in the returned singleton. Naming calls must use that singleton.
        """
        if self.blocked or root in self.bootstrap_checked:return
        self.bootstrap_checked.add(root)
        if result['unknown_returns'] or result['return_arguments'] or result['return_argument_calls'] or result['return_calls'] or len(result['returns'])!=1:return
        slot=next(iter(result['returns']));sites=result['call_sites'];constructors=[]
        for site in sites:
            r,a=site['registers'],site['stack']
            if a.get(0x20) not in (4,8,16,32,64) and a.get(0x20) is not None and a[0x20]&0x10000000:
                # UE 4.22 and older: no InAlignment argument; every later argument is one slot earlier.
                a={**{k+8:v for k,v in a.items() if k>=0x20},0x20:8}
            size=r.get('r9');alignment=a.get(0x20);flags=a.get(0x28)
            if (not site['target'] or size is None or not 0x28<=size<1<<24 or alignment not in (4,8,16,32,64)
                or flags is None or not 0<=flags<1<<32 or not flags&0x10000000):continue
            if a.get(0x30) is None or not 0<=a[0x30]<1<<64 or a.get(0x40) is None or not 0<=a[0x40]<1<<32:continue
            config=a.get(0x38)
            if config is None or config and not self.wide(config):continue
            if not all(self.im.tz(a.get(k)) for k in (0x48,0x50,0x58)) or not a.get(0x48):continue
            source=site['origins'].get('rcx')
            if not source or source[0]!='call':continue
            helper=self.scan(site['target'])
            if helper['unknown_returns'] or helper['returns'] or helper['return_calls'] or helper['return_argument_calls'] or helper['return_arguments']!={0}:continue
            constructors.append((site,source))
        unique={(s['address'],source) for s,source in constructors}
        if len(unique)!=1:return
        site,allocation=constructors[0]
        if len({s['address'] for s in sites if s['target']==allocation[1]})!=1:return
        constructed=('call',site['target'],site['address'])
        expected={allocation,constructed};sources=self.singleton_copies.get(slot,set())
        if not sources or not sources<=expected or constructed not in sources:return
        # Unwind entries can begin inside the already verified function. The
        # exact producing call site must agree, and each alternate scan entry
        # must be an instruction reached by this complete constructor proof.
        if any(not self.singleton_copy_writers.get((slot,source),set())<=result['decoded_addresses'] for source in sources):return
        identities=set();names=[];parents=set()
        for call in sites:
            r=call['registers']
            if call['origins'].get('rcx')==('data',slot):
                package=self.wide(r.get('rdx'),package=True);name=self.wide(r.get('r8'))
            elif call['origins'].get('rdx')==('data',slot):
                # UE 4.20 and older: InitializePrivateStaticClass(Super, Class, Within, Package, Name).
                package=self.wide(r.get('r9'),package=True);name=self.wide(call['stack'].get(0x20))
                origin=call['origins'].get('rcx')
                if package and name and origin and origin[0]=='call':parents.add(origin[1])
            else:continue
            if package and name:
                identities.add((package,name.removeprefix('DEPRECATED_')));names.append(call['address'])
        if len(identities)!=1:return
        identity=next(iter(identities))
        if self.class_slots[slot] and self.class_slots[slot]!={identity}:self.stats['bootstrap_class_conflicts']+=1;return
        self.class_slots[slot].add(identity)
        self.bootstrap_evidence[slot]={'identity':identity,'initializer':root,'constructor':site['target'],'naming_calls':names,
                                       'naming_parent':next(iter(parents)) if len(parents)==1 else None}
        self.stats['bootstrap_classes']+=1

    def class_result(self, root):
        """Only verified returned singleton identities, for function-pointer ABI arguments."""
        if self.blocked or not root or not self.im.is_text(root):return None
        def slot_names(slot,seen):
            key=('data',slot)
            if key in seen or len(seen)>=32:return set(),False
            known=set(self.class_slots.get(slot,()))
            known.update(self.class_parameters[p] for p in self.parameter_outputs.get(slot,set()) if p in self.class_parameters)
            sources=self.singleton_copies.get(slot,set())
            if known:return known,len(known)==1
            if not sources:return set(),False
            out=set();complete=True
            for source in sources:
                kind,target=source[:2]
                names,ok=slot_names(target,seen|{key}) if kind=='data' else function_names(target,seen|{key})
                out.update(names);complete &= ok
            return out,complete
        def function_names(fn,seen):
            key=('call',fn)
            if key in seen or len(seen)>=32:return set(),False
            scan=self.scan(fn);self.bootstrap_class(fn,scan)
            out=set();complete=not scan['unknown_returns'] and not scan['return_argument_calls'] and not scan['return_arguments']
            for slot in scan['returns']:
                names,ok=slot_names(slot,seen|{key});out.update(names);complete &= ok
            for target in scan['return_calls']:
                names,ok=function_names(target,seen|{key});out.update(names);complete &= ok
            return out,complete and bool(scan['returns'] or scan['return_calls'])
        names,complete=function_names(root,set())
        return next(iter(names)) if complete and len(names)==1 else None

    def intrinsic_schemas(self, relationships):
        """Calibrate the superclass getter slot against exact UHT dependency identities.

        Use package-qualified identities reached through constructor/singleton links.
        Ambiguous records, contradictory argument layouts and unknown parents fail closed.
        """
        grouped=defaultdict(list)
        for slot,record in self.class_records.items():
            if self.class_slots[slot]=={record['identity']}:grouped[record['identity']].append((slot,record))
        unique={identity:rows[0] for identity,rows in grouped.items() if len(rows)==1}
        examples=[(unique[child][1],parent) for child,parent in relationships
                  if child in unique and parent and parent[1]!='Object']
        options={off for record,parent in examples for off in record.get('address_arguments',()) if off>=0x38}
        scores={}
        result_cache={}
        def result(fn):
            if fn not in result_cache:result_cache[fn]=self.class_result(fn)
            return result_cache[fn]
        for offset in options:
            good=bad=0;parents=set()
            for record,parent in examples:
                actual=result(record['arguments'].get(offset)) if offset in record.get('address_arguments',()) else None
                if actual is None:continue
                if actual==parent:good+=1;parents.add(parent)
                else:bad+=1
            if not bad and good>=3 and len(parents)>=2:scores[offset]=good
        if not scores:return {},{'superclass_argument':None,'calibration_examples':len(examples)}
        best=max(scores.values());winners=[off for off,count in scores.items() if count==best]
        if len(winners)!=1:return {},{'superclass_argument':None,'ambiguous_arguments':winners}
        offset=winners[0];out={};ambiguous=set()
        for identity,(slot,record) in unique.items():
            package,name=identity;flags=record['arguments'].get(0x30,0)
            if name not in LEGACY_EMPTY_INTRINSICS or not flags&0x10000000:continue
            expected_package='/Script/Engine' if name=='Model' else '/Script/CoreUObject'
            if package!=expected_package:continue
            parent=result(record['arguments'].get(offset))
            if parent is None:continue
            if name in out:ambiguous.add(name)
            out[name]={'super':parent[1],'count':0,'props':[],
                       'evidence':{'identity':identity,'singleton':slot,'super_getter':record['arguments'][offset]}}
        return {n:r for n,r in out.items() if n not in ambiguous},{'superclass_argument':offset,'calibration_matches':best,'calibration_examples':len(examples)}

    def bootstrap_schemas(self, known_parents):
        """Infer the superclass-setting virtual ABI from verified core bootstraps.

        Only supported empty engine intrinsics may use the resulting schema.
        Constructor identity, receiver singleton and parent getter remain exact.
        """
        candidates={};groups=defaultdict(list)
        for slot,evidence in list(self.bootstrap_evidence.items()):
            identity=evidence['identity'];values=defaultdict(set)
            for site in self.scan(evidence['initializer'])['call_sites']:
                virtual=site.get('virtual');origin=site['origins'].get('rdx')
                if (not virtual or virtual[0]!=slot or site['origins'].get('rcx')!=('data',slot)
                    or not origin or origin[0]!='call'):continue
                parent=self.class_result(origin[1])
                if parent and parent!=identity:values[virtual[1]].add(parent)
            candidates[slot]=(evidence,values);groups[identity].append(slot)
        unique={identity:slots[0] for identity,slots in groups.items() if len(slots)==1}
        scores={}
        offsets={offset for evidence,values in candidates.values() for offset in values}
        for offset in offsets:
            good=bad=0;parents=set()
            for (package,name),slot in unique.items():
                expected=known_parents.get(name) if package=='/Script/CoreUObject' else None
                if not expected:continue
                actual=candidates[slot][1].get(offset,set())
                if not actual:continue
                if actual=={(package,expected)}:good+=1;parents.update(actual)
                else:bad+=1
            if not bad and good>=3 and len(parents)>=2:scores[offset]=good
        if not scores:return CodeLinks.naming_parent_schemas(self,known_parents)
        best=max(scores.values());winners=[off for off,n in scores.items() if n==best]
        if len(winners)!=1:return {},{'superclass_virtual_offset':None,'ambiguous_offsets':winners}
        offset=winners[0];out={}
        for (package,name),slot in unique.items():
            if name not in LEGACY_EMPTY_INTRINSICS or package!=('/Script/Engine' if name=='Model' else '/Script/CoreUObject'):continue
            evidence,values=candidates[slot];parents=values.get(offset,set())
            if len(parents)!=1:continue
            parent=next(iter(parents))
            out[name]={'super':parent[1],'count':0,'props':[],
                       'evidence':{'identity':(package,name),'singleton':slot,'initializer':evidence['initializer'],
                                   'superclass_virtual_offset':offset,'parent_identity':parent}}
        return out,{'superclass_virtual_offset':offset,'calibration_matches':best}

    def naming_parent_schemas(self, known_parents):
        """UE 4.20 and older name a bootstrapped class with its superclass getter as the first
        argument. Calibrate that link on the core classes whose parents are engine-wide facts."""
        groups=defaultdict(list)
        for slot,evidence in list(self.bootstrap_evidence.items()):groups[evidence['identity']].append(slot)
        parents={}
        for identity,slots in groups.items():
            fn=self.bootstrap_evidence[slots[0]].get('naming_parent') if len(slots)==1 else None
            if fn:parents[identity]=self.class_result(fn)
        good=bad=0
        for (package,name),parent in parents.items():
            expected=known_parents.get(name) if package=='/Script/CoreUObject' else None
            if not expected or parent is None:continue
            if parent==(package,expected):good+=1
            else:bad+=1
        if bad or good<3:return {},{'superclass_virtual_offset':None,'naming_parent_matches':good,'naming_parent_conflicts':bad}
        out={}
        for (package,name),parent in parents.items():
            if parent is None or name not in LEGACY_EMPTY_INTRINSICS or package!=('/Script/Engine' if name=='Model' else '/Script/CoreUObject'):continue
            out[name]={'super':parent[1],'count':0,'props':[],
                       'evidence':{'identity':(package,name),'naming_parent':self.bootstrap_evidence[groups[(package,name)][0]]['naming_parent'],
                                   'parent_identity':parent}}
        return out,{'superclass_virtual_offset':None,'naming_parent_matches':good}

    def class_candidates(self):
        """Find possible name arguments; full instruction/ABI validation follows in scan()."""
        if self.blocked:return set()
        lo,hi,_=self.im.pe.sec('.text');out=set();chunk=1024*1024
        # LEA RCX,[RIP+disp32] of a native package path. Full ABI validation follows.
        for start in range(lo,hi,chunk):
            data=self.read(start,min(chunk+6,hi-start))
            for m in re.finditer(rb'\x48\x8d\x0d[\s\S]{4}',data):
                pos=start+m.start();target=pos+7+struct.unpack_from('<i',m.group(),3)[0]
                if not self.wide(target,package=True):continue
                i=bisect.bisect_right(self.starts,pos)-1
                if i>=0:
                    fn=self.starts[i]
                    if pos<fn+self.functions[fn][0] and self.functions[fn][0]<=4096:out.add(fn)
        return out
