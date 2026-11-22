"""Narrow static ABI proofs for compiler helpers; input code is never executed."""

def preserves_return_register(linker,root):
    """Prove the MSVC x64 cookie check's normal return preserves RAX.

    Both rejection branches must reach a verified current-process termination
    path. An arbitrary similarly shaped comparator or a returning error handler
    does not establish this special calling convention.
    """
    if linker.blocked or not root or not linker.im.is_text(root):return False
    X=linker.cs.x86_const
    cache=getattr(linker,'compiler_return_cache',None)
    if cache is None:cache={};linker.compiler_return_cache=cache
    if root in cache:return cache[root]
    cache[root]=False
    code=[]
    for ins in linker.md.disasm(linker.read(root,min(64,linker.im.text[1]-root)),root):
        if ins.mnemonic not in ('nop','endbr64'):code.append(ins)
        if len(code)==8:break
    if [i.mnemonic for i in code]!=['cmp','jne','rol','test','jne','ret','ror','jmp']:return False
    compare,first,rotate,test,second,normal,restore,fail=code
    def reg_imm(ins,register,value):
        return (len(ins.operands)==2 and ins.operands[0].type==X.X86_OP_REG and ins.operands[0].reg==register
                and ins.operands[1].type==X.X86_OP_IMM and ins.operands[1].imm==value)
    if (len(compare.operands)!=2 or compare.operands[0].type!=X.X86_OP_REG or compare.operands[0].reg!=X.X86_REG_RCX
        or compare.operands[1].type!=X.X86_OP_MEM or compare.operands[1].mem.base!=X.X86_REG_RIP
        or compare.operands[1].mem.index or compare.operands[1].size!=8
        or not linker.im.is_data(compare.address+compare.size+compare.operands[1].mem.disp)
        or not reg_imm(rotate,X.X86_REG_RCX,16) or not reg_imm(test,X.X86_REG_CX,0xffff)
        or not reg_imm(restore,X.X86_REG_RCX,16)):return False
    if any(i.operands[0].type!=X.X86_OP_IMM for i in (first,second,fail)):return False
    if first.operands[0].imm!=fail.address or second.operands[0].imm!=restore.address:return False
    imports=linker.im.pe.imports();modules=linker.im.pe.import_modules
    def imported(ins,name):
        if len(ins.operands)!=1 or ins.operands[0].type!=X.X86_OP_MEM:return False
        mem=ins.operands[0].mem
        if mem.base!=X.X86_REG_RIP or mem.index:return False
        slot=ins.address+ins.size+mem.disp;module=modules.get(slot,'')
        return imports.get(slot)==name and (module in ('kernel32.dll','kernelbase.dll')
                or module.startswith('api-ms-win-core-processthreads-') and module.endswith('.dll'))
    terminal_cache={}
    def terminates_current_process(fn):
        if fn in terminal_cache:return terminal_cache[fn]
        terminal_cache[fn]=False
        if not linker.im.is_text(fn):return False
        body=linker.instructions(fn)
        if not body or len(body)>64 or body[-1].mnemonic!='jmp' or not imported(body[-1],'TerminateProcess'):return False
        if any(i.group(linker.cs.CS_GRP_JUMP) or i.mnemonic.startswith('ret') for i in body[:-1]):return False
        calls=[p for p,i in enumerate(body[:-1]) if i.mnemonic=='call' and imported(i,'GetCurrentProcess')]
        if len(calls)!=1:return False
        tail=body[calls[0]+1:-1]
        if not tail or tail[0].mnemonic!='mov' or len(tail[0].operands)!=2:return False
        if [o.reg if o.type==X.X86_OP_REG else None for o in tail[0].operands]!=[X.X86_REG_RCX,X.X86_REG_RAX]:return False
        for ins in tail[1:]:
            ops=ins.operands
            if ins.mnemonic=='mov' and reg_imm(ins,X.X86_REG_EDX,0xc0000409):continue
            if (ins.mnemonic=='add' and len(ops)==2 and ops[0].type==X.X86_OP_REG and ops[0].reg==X.X86_REG_RSP
                and ops[1].type==X.X86_OP_IMM and 0<ops[1].imm<=0x1000):continue
            if ins.mnemonic=='pop' and len(ops)==1 and ops[0].type==X.X86_OP_REG and ops[0].reg in (X.X86_REG_RBX,X.X86_REG_RBP,X.X86_REG_RSI,X.X86_REG_RDI):continue
            return False
        terminal_cache[fn]=True;return True
    todo=[fail.operands[0].imm];seen=set();steps=budget=0
    while todo and len(seen)<32 and steps<256:
        pc=todo.pop()
        if pc in seen:continue
        if not linker.im.is_text(pc):return False
        seen.add(pc);size=min(256,linker.im.text[1]-pc)
        if budget+size>16384:return False
        budget+=size;last=None;terminated=False
        for ins in linker.md.disasm(linker.read(pc,size),pc):
            last=ins;steps+=1
            if ins.mnemonic=='int' and len(ins.operands)==1 and ins.operands[0].type==X.X86_OP_IMM and ins.operands[0].imm==0x29:
                terminated=True;break
            if ins.mnemonic=='call' and ins.operands[0].type==X.X86_OP_IMM and terminates_current_process(ins.operands[0].imm):
                terminated=True;break
            if ins.mnemonic.startswith('ret') or ins.mnemonic in ('ud2','int3','hlt'):return False
            if ins.group(linker.cs.CS_GRP_JUMP):break
        if terminated:continue
        if last is None:return False
        if last.group(linker.cs.CS_GRP_JUMP):
            if last.operands[0].type!=X.X86_OP_IMM:return False
            todo.append(last.operands[0].imm)
            if last.mnemonic!='jmp':todo.append(last.address+last.size)
        else:todo.append(last.address+last.size)
    if todo:return False
    cache[root]=True;return True
