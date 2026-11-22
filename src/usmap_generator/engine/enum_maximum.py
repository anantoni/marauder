"""Bounded source-linked recognition of the native enum maximum reducer.

The parameter constructor identifies its helper, the helper's native constructor
identifies a vtable, and its virtual call identifies SetEnums. Only a recognized
read-only value reducer called by that method establishes a maximum policy.
"""

def graph(linker,root):
    C=linker.cs;X=C.x86_const;todo=[root];seen=set();out={};budget=0
    while todo and len(seen)<128 and len(out)<4096:
        start=todo.pop()
        if start in seen:continue
        if not linker.im.is_text(start):return None
        seen.add(start);known=linker.functions.get(start)
        size=min(known[0] if known else 256,4096,linker.im.text[1]-start)
        if size<=0 or budget+size>65536:return None
        budget+=size;last=None
        for ins in linker.md.disasm(linker.read(start,size),start):
            last=ins;out[ins.address]=ins
            if ins.group(C.CS_GRP_JUMP) or ins.mnemonic.startswith('ret') or ins.mnemonic in ('int3','ud2','hlt'):break
        if last is None:return None
        if last.group(C.CS_GRP_JUMP):
            if last.operands[0].type!=X.X86_OP_IMM:return None
            todo.append(last.operands[0].imm)
            if last.mnemonic!='jmp':todo.append(last.address+last.size)
        elif not last.mnemonic.startswith('ret') and last.mnemonic not in ('int3','ud2','hlt'):
            todo.append(last.address+last.size)
    return sorted(out.values(),key=lambda i:i.address) if not todo else None

def reducer(linker,code):
    """Recognize the two supported read-only native GetMaxEnumValue families."""
    if not code or len(code)>128:return None
    X=linker.cs.x86_const
    allowed={'mov','movsxd','movzx','test','cmp','lea','xor','and','or','shl','add','sub','inc','dec','nop','ret'}
    for ins in code:
        if ins.mnemonic not in allowed and not ins.mnemonic.startswith(('j','cmov')):return None
        if ins.operands and ins.operands[0].type==X.X86_OP_MEM and ins.mnemonic not in ('test','cmp','nop'):return None
    def member(ins,offset,size):
        return any(o.type==X.X86_OP_MEM and o.mem.base==X.X86_REG_RCX and not o.mem.index
                   and o.mem.disp==offset and o.size==size for o in ins.operands)
    if not (any(i.mnemonic=='movsxd' and member(i,0x48,4) for i in code)
            and any(i.mnemonic=='mov' and member(i,0x40,8) for i in code)
            and any(i.mnemonic=='cmovle' for i in code)
            and any(i.mnemonic=='ret' for i in code)):return None
    meaningful=[i for i in code if i.mnemonic!='nop']
    largest=[p for p,i in enumerate(meaningful) if i.mnemonic=='cmovle']
    if len(largest)!=1:return None
    pos=largest[0];select=meaningful[pos]
    if (not pos or pos+1>=len(meaningful) or len(select.operands)!=2
        or any(o.type!=X.X86_OP_REG or o.size!=8 for o in select.operands)
        or select.operands[1].reg!=X.X86_REG_RAX):return None
    compare,store=meaningful[pos-1],meaningful[pos+1]
    if (compare.mnemonic!='cmp' or len(compare.operands)!=2 or any(o.type!=X.X86_OP_REG for o in compare.operands)
        or [o.reg for o in compare.operands]!=[o.reg for o in select.operands]
        or store.mnemonic!='mov' or len(store.operands)!=2
        or store.operands[0].type!=X.X86_OP_REG or store.operands[0].reg!=X.X86_REG_RAX
        or store.operands[1].type!=X.X86_OP_REG or store.operands[1].reg!=select.operands[0].reg):return None
    # No arithmetic may modify the returned accumulator after the reduction.
    for ins in meaningful:
        if not ins.operands or ins.operands[0].type!=X.X86_OP_REG:continue
        dest=linker.md.reg_name(ins.operands[0].reg)
        if dest not in ('rax','eax','ax','al','ah') or ins.mnemonic in ('test','cmp'):continue
        if ins.mnemonic=='xor' and len(ins.operands)==2 and ins.operands[0].reg==ins.operands[1].reg and ins.operands[0].size>=4:continue
        if ins.mnemonic=='mov' and len(ins.operands)==2 and ins.operands[0].size==8 and ins.operands[1].type in (X.X86_OP_REG,X.X86_OP_MEM):continue
        return None
    flag_tests=[i for i in code if i.mnemonic=='test' and member(i,0x51,1)]
    ors=[i for i in code if i.mnemonic=='or'];ands=[i for i in code if i.mnemonic=='and']
    if not flag_tests:return 'signed maximum' if not ors and not ands else None
    if (len(flag_tests)!=1 or flag_tests[0].operands[-1].type!=X.X86_OP_IMM or flag_tests[0].operands[-1].imm!=1
        or len(ors)!=1 or len(ands)!=1):return None
    # The flag path tests v & (v-1), includes only zero/single-bit values,
    # ORs those into the result, and leaves the signed-maximum path intact.
    for pos,ins in enumerate(code):
        if ins.mnemonic!='lea' or len(ins.operands)!=2:continue
        dst,src=ins.operands
        if (dst.type!=X.X86_OP_REG or src.type!=X.X86_OP_MEM or src.mem.index or src.mem.disp!=-1):continue
        bitand=ands[0];bitor=ors[0]
        if (len(bitand.operands)==2 and all(o.type==X.X86_OP_REG for o in bitand.operands)
            and bitand.operands[0].reg==dst.reg and bitand.operands[1].reg==src.mem.base
            and len(bitor.operands)==2 and all(o.type==X.X86_OP_REG for o in bitor.operands)
            and bitor.operands[0].reg==src.mem.base and any(i.mnemonic=='cmovne' for i in code)):
            return 'single-bit union for flags'
    return None

def inline_reducer(linker,code):
    """Recognize the split name/value storage variant inside the linked method.

    Establish both reducers, their flag branch, their common +1 join, the empty
    path bypassing that join, and the resulting value's indexed storage write.
    A nearby OR or a recognizable standalone helper is insufficient.
    """
    if not code:return None
    X=linker.cs.x86_const;c=[i for i in code if i.mnemonic!='nop'];by={i.address:p for p,i in enumerate(c)}
    def reg(o):return o.reg if o.type==X.X86_OP_REG else None
    def regs(i):return [reg(o) for o in i.operands]
    def mem(o,base,disp):return o.type==X.X86_OP_MEM and o.mem.base==base and not o.mem.index and o.mem.disp==disp
    def same(a,b):
        # The ABI's full and narrow register aliases have the same identity.
        if not a or not b:return False
        def name(r):
            n=linker.md.reg_name(r)
            for full,aliases in [('rax',('eax','ax','al','ah')),('rcx',('ecx','cx','cl','ch')),('rdx',('edx','dx','dl','dh')),('rbx',('ebx','bx','bl','bh')),('rsi',('esi','si','sil')),('rdi',('edi','di','dil'))]:
                if n in aliases:return full
            return n.rstrip('dwb') if n.startswith('r') else n
        return a is not None and b is not None and name(a)==name(b)
    def writes(i,r):
        try:return any(same(v,r) for v in i.regs_access()[1])
        except linker.cs.CsError:return True
    for p,ins in enumerate(c):
        if ins.mnemonic!='lea' or len(ins.operands)!=2 or p<1 or p+10>=len(c):continue
        temp=reg(ins.operands[0]);v=ins.operands[1]
        if v.type!=X.X86_OP_MEM or v.mem.index or v.mem.disp!=-1:continue
        value=v.mem.base;load=c[p-1];loop=load.address
        if load.mnemonic!='mov' or reg(load.operands[0])!=value or load.operands[1].type!=X.X86_OP_MEM:continue
        iterator=load.operands[1].mem.base
        if not mem(load.operands[1],iterator,8):continue
        bitand,bitor,test,select=c[p+1:p+5]
        if (bitand.mnemonic!='and' or regs(bitand)!=[temp,value] or bitor.mnemonic!='or'
            or reg(bitor.operands[0])!=value or test.mnemonic!='test' or regs(test)!=[temp,temp]
            or select.mnemonic!='cmovne'):continue
        accumulator=reg(bitor.operands[1])
        if accumulator is None or regs(select)!=[value,accumulator]:continue
        tail=c[p+5:p+10]
        updates=[i for i in tail if i.mnemonic=='mov' and regs(i)==[accumulator,value]]
        strides=[i for i in tail if i.mnemonic in ('add','lea') and reg(i.operands[0])==iterator
                 and ((i.mnemonic=='add' and i.operands[1].type==X.X86_OP_IMM and i.operands[1].imm==16)
                      or (i.mnemonic=='lea' and mem(i.operands[1],iterator,16)))]
        backs=[i for i in tail if i.mnemonic=='jne' and i.operands[0].imm==loop]
        if len(updates)!=1 or len(strides)!=1 or len(backs)!=1:continue
        end=by[backs[0].address]+1
        if c[end].mnemonic!='jmp' or c[end].operands[0].imm not in by:continue
        join=by[c[end].operands[0].imm];addition=c[join]
        if addition.mnemonic!='lea' or len(addition.operands)!=2 or not mem(addition.operands[1],accumulator,1):continue
        output=reg(addition.operands[0])
        branches=[(q,i) for q,i in enumerate(c[:p]) if i.mnemonic=='test' and len(i.operands)==2
                  and i.operands[0].type==X.X86_OP_REG and i.operands[0].size==1
                  and i.operands[1].type==X.X86_OP_IMM and i.operands[1].imm==1
                  and q+1<len(c) and c[q+1].mnemonic=='je' and c[q+1].operands[0].imm in by
                  and by[c[q+1].operands[0].imm]>end]
        if not branches:continue
        q,branch=branches[-1];plain=by[c[q+1].operands[0].imm]
        flag=reg(branch.operands[0])
        flag_defs=[i for k,i in enumerate(c[:q]) if writes(i,flag)
                   and not (k>0 and k+1<len(c) and c[k-1].mnemonic=='jne' and c[k+1].mnemonic=='jmp'
                            and c[k-1].operands[0].imm>i.address and c[k+1].operands[0].imm==c[join+1].address)]
        if (not flag_defs or flag_defs[-1].mnemonic!='movzx'
            or not same(reg(flag_defs[-1].operands[1]),X.X86_REG_R9B)):continue
        signed=[]
        for k in range(plain,join-1):
            i=c[k]
            if i.mnemonic!='cmovle' or reg(i.operands[1])!=accumulator:continue
            chosen=reg(i.operands[0])
            if (c[k-1].mnemonic=='cmp' and regs(c[k-1])==[chosen,accumulator]
                and c[k+1].mnemonic=='mov' and regs(c[k+1])==[accumulator,chosen]):signed.append(k)
        if len(signed)!=1:continue
        # The count-zero branch must write a proven zero and skip +1 entirely.
        empty=[]
        for k in range(q-3):
            i=c[k]
            if i.mnemonic!='mov' or len(i.operands)!=2 or reg(i.operands[0])!=output:continue
            zero=reg(i.operands[1])
            if zero is None or c[k-1].mnemonic!='jne' or c[k+1].mnemonic!='jmp':continue
            if c[k+1].operands[0].imm!=c[join+1].address:continue
            defs=[v for v in c[:k] if writes(v,zero)]
            if not defs or defs[-1].mnemonic!='xor' or regs(defs[-1])!=[reg(defs[-1].operands[0])]*2:continue
            if c[k-2].mnemonic!='test' or c[k-3].mnemonic!='movsxd':continue
            count=reg(c[k-3].operands[0]);source=c[k-3].operands[1]
            if source.type!=X.X86_OP_MEM or source.mem.disp!=8 or source.mem.index:continue
            if not all(same(reg(o),count) for o in c[k-2].operands):continue
            aliases=[i for i in c[:k-3] if writes(i,source.mem.base)]
            if not aliases or aliases[-1].mnemonic!='mov' or reg(aliases[-1].operands[1])!=X.X86_REG_RDX:continue
            empty.append(k)
        if len(empty)!=1:continue
        for i in c[join+1:]:
            if (i.mnemonic=='mov' and len(i.operands)==2 and i.operands[0].type==X.X86_OP_MEM
                and i.operands[0].mem.index and i.operands[0].mem.scale==8 and reg(i.operands[1])==output):
                return 'single-bit union for flags; empty zero'
            if writes(i,output):break
    return None

def discover(linker,records):
    X=linker.cs.x86_const;helpers=set();evidence=[];policies=set()
    for result in list(linker.scanned.values()):
        for site in result['call_sites']:
            parameter=site['registers'].get('rdx');output=site['registers'].get('rcx')
            if (site['target'] and parameter in records and {'rcx','rdx'}<=site['addresses']
                and parameter in linker.parameter_outputs.get(output,set())):
                helpers.add(site['target'])
    def calls(code):return {i.operands[0].imm for i in code if i.mnemonic=='call' and i.operands[0].type==X.X86_OP_IMM}
    for helper in sorted(helpers):
        code=graph(linker,helper)
        if code is None:return None,{'status':'unverified','reason':'Enum helper exceeds the structural bound'}
        tables=set();offsets={i.operands[0].mem.disp for i in code if i.mnemonic=='call'
                and i.operands[0].type==X.X86_OP_MEM and not i.operands[0].mem.index
                and i.operands[0].mem.base!=X.X86_REG_RIP and 0x100<=i.operands[0].mem.disp<=0x800
                and i.operands[0].mem.disp%8==0}
        for fn in calls(code):
            result=linker.scan(fn)
            if (not result['unknown_returns'] and result['return_arguments']=={0} and not result['returns']
                and not result['return_calls'] and not result['return_argument_calls']):tables.update(result.get('vtable_writes',()))
        if len(tables)!=1 or len(offsets)!=1:return None,{'status':'unverified','reason':'Ambiguous enum native constructor or virtual slot'}
        table=next(iter(tables));offset=next(iter(offsets));method=linker.im.Q(table+offset)
        code=graph(linker,method) if linker.im.is_text(method) else None
        found=[]
        for fn in calls(code or []):
            policy=reducer(linker,graph(linker,fn))
            if policy:found.append((fn,policy))
        inlined=inline_reducer(linker,code)
        if inlined:found.append((method,inlined))
        if len(found)!=1:return None,{'status':'unverified','reason':'No unique supported native enum maximum reducer',
                                     'helper':helper,'vtable':table,'method':method,'candidates':sorted(calls(code or []))}
        fn,policy=found[0];policies.add(policy);evidence.append({'helper':helper,'vtable':table,'method':method,'reducer':fn,'policy':policy})
    if len(policies)!=1:return None,{'status':'unverified','reason':'Missing or conflicting enum maximum policies'}
    return next(iter(policies)),{'status':'verified','links':evidence}

def maximum(entries,is_flags,policy):
    if not entries:
        if policy is None:return None
        return 0 if policy=='single-bit union for flags; empty zero' else 1
    if is_flags and policy in ('single-bit union for flags','single-bit union for flags; empty zero'):
        value=0
        for name,item in entries:
            bits=item&((1<<64)-1)
            if bits&(bits-1)==0:value|=bits
        if value>=1<<63:value-=1<<64
        return value+1
    if is_flags and policy is None:return None
    return max((v for name,v in entries),default=0)+1
