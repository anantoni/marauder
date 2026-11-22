"""Native enum registration order and package-scoped automatic sentinel names.

Only declared CRT callbacks and validated UHT registration ABIs contribute order.
Constructor and package records are joined by returned identifiers. Partial or
ambiguous package coverage emits declared entries only and reports the gap.
"""
from collections import defaultdict

def discover(linker, records, other_records=None):
    im=linker.im;X=linker.cs.x86_const;rows=[];unknown=set();packages={};targets={}
    other_records=other_records or {}
    identities={**other_records,**records}
    audit={'candidate_callbacks':0,'registration_tables':0,'legacy_registrations':0,'unresolved_tables':0,'other_reflection_registrations':0}
    def package(fn):
        if fn not in packages:packages[fn]=linker.package_name(fn)
        return packages[fn]
    def target(fn):
        if fn not in targets:targets[fn]=linker.registration_id(fn,identities)
        return targets[fn]
    def pair(ptr,n):return isinstance(n,int) and 0<=n<=4096 and (ptr==0 if n==0 else im.is_data(ptr))
    def other_table(ptr,n):
        if not pair(ptr,n):return False
        for i in range(n):
            p=ptr+40*i
            if not im.is_text(im.Q(p)) or not im.tz(im.Q(p+8)) or not im.wident(im.Q(p+16)) or not im.is_data(im.Q(p+24)):return False
        return True
    def enum_table(ptr,n):
        if not pair(ptr,n) or not n:return None
        entries=[]
        for i in range(n):
            p=ptr+32*i;fn=im.Q(p);name=im.wident(im.Q(p+8))
            if not im.is_text(fn) or not name or not im.is_data(im.Q(p+16)):return None
            entries.append((p,fn,name))
        return entries
    def candidate(callback):
        length=min(linker.functions.get(callback,(512,))[0],512,im.text[1]-callback)
        for ins in linker.md.disasm(linker.read(callback,length),callback):
            ops=ins.operands
            if (ins.mnemonic=='lea' and len(ops)==2 and ops[0].type==X.X86_OP_REG
                and linker.md.reg_name(ops[0].reg) in ('rcx','rdx','r8') and ops[1].type==X.X86_OP_MEM
                and ops[1].mem.base==X.X86_REG_RIP and linker.wide(ins.address+ins.size+ops[1].mem.disp,package=True)):return True
            if ins.mnemonic in ('ret','int3','ud2','hlt'):break
        return False
    order=[]      # (callback position, call address, entry, kind, key): native registration order
    def ordered(position,address,kind,table,n,stride):
        if not pair(table,n):return
        for i in range(n):
            fn=im.Q(table+stride*i)
            if kind=='class':order.append((position,address,i,'class',im.Q(table+stride*i+8)))
            else:
                record=target(fn) if im.is_text(fn) else None
                if record is not None:order.append((position,address,i,kind,record))
    for position,callback in enumerate(linker.startup_callbacks()):
        if not candidate(callback):continue
        audit['candidate_callbacks']+=1
        sites=defaultdict(list);names={}
        for site in linker.scan(callback)['call_sites']:sites[site['address']].append(site)
        # UE 4.20 and older pass the enum name as an FName built in the same callback just
        # before the defer call: FName(TEXT("EName"), FNAME_Add) with a stack destination.
        last=None
        for address in sorted(sites):
            r=sites[address][0]['registers']
            if r.get('r8')==1 and linker.wide(r.get('rdx')):last=linker.wide(r.get('rdx'))
            else:names[address]=last;last=None
        for address,paths in sites.items():
            declarations=[]
            for site in paths[:1]:
                r,s=site['registers'],site['stack']
                if linker.wide(r.get('rcx'),package=True) and other_table(r.get('rdx'),r.get('r8')) and other_table(r.get('r9'),s.get(0x20)):
                    ordered(position,address,'class',r['rdx'],r['r8'],40);ordered(position,address,'struct',r['r9'],s[0x20],40)
                    ordered(position,address,'enum',s.get(0x28),s.get(0x30),32)
                elif linker.wide(r.get('rdx'),package=True) and im.is_data(r.get('rcx')) and other_table(r.get('r8'),r.get('r9')) and other_table(s.get(0x20),s.get(0x28)):
                    ordered(position,address,'class',r['r8'],r['r9'],40);ordered(position,address,'struct',s[0x20],s[0x28],40)
                    ordered(position,address,'enum',s.get(0x30),s.get(0x38),32)
            for site in paths:
                r,s=site['registers'],site['stack'];declaration=None
                pkg=linker.wide(r.get('rcx'),package=True)
                if pkg and other_table(r.get('rdx'),r.get('r8')) and other_table(r.get('r9'),s.get(0x20)):
                    entries=enum_table(s.get(0x28),s.get(0x30))
                    if entries:declaration=(pkg,tuple(entries),'table')
                pkg=linker.wide(r.get('rdx'),package=True)
                if pkg and im.is_data(r.get('rcx')) and other_table(r.get('r8'),r.get('r9')) and other_table(s.get(0x20),s.get(0x28)):
                    entries=enum_table(s.get(0x30),s.get(0x38))
                    if entries:declaration=(pkg,tuple(entries),'table')
                # Legacy member and free-function enum-defer ABIs. Dynamic variants
                # are excluded; the actual enum constructor must prove the identity.
                pkg=linker.wide(r.get('r8'),package=True);name=linker.wide(r.get('r9'))
                if pkg and name and im.is_data(r.get('rcx')) and im.is_text(r.get('rdx')) and s.get(0x20)==0:
                    declaration=(pkg,((address,r['rdx'],name),),'legacy')
                pkg=linker.wide(r.get('rdx'),package=True);name=linker.wide(r.get('r8'))
                if pkg and name and im.is_text(r.get('rcx')) and r.get('r9')==0:
                    declaration=(pkg,((address,r['rcx'],name),),'legacy')
                name=names.get(address)
                if pkg and name and 'r8' not in r and im.is_text(r.get('rcx')) and r.get('r9')==0 and s.get(0x20)==0:
                    declaration=(pkg,((address,r['rcx'],name),),'legacy')
                declarations.append(declaration)
            valid={d for d in declarations if d is not None}
            if not valid:continue
            if len(valid)!=1 or any(d is None for d in declarations):
                unknown.update(d[0] for d in valid);audit['unresolved_tables']+=1;continue
            pkg,entries,kind=next(iter(valid));resolved=[]
            # Legacy struct and enum defer calls share an ABI. Prove a different
            # parameter identity before excluding one; a failed enum join remains
            # unknown and must not silently become a non-enum registration.
            if kind=='legacy' and target(entries[0][1]) in other_records:
                audit['other_reflection_registrations']+=1
                order.append((position,address,0,'struct',target(entries[0][1])))
                continue
            if kind=='legacy':order.append((position,address,0,'enum',target(entries[0][1])))
            for source,fn,name in entries:
                record=target(fn)
                if record not in records or records[record]['name']!=name or package(records[record]['outer'])!=pkg:break
                resolved.append({'record':record,'package':pkg,'constructor':fn,'source':source,'callback':callback})
            else:
                rows.extend(resolved);audit['registration_tables' if kind=='table' else 'legacy_registrations']+=1
                continue
            unknown.add(pkg);audit['unresolved_tables']+=1
    identities=defaultdict(set)
    for row in rows:identities[row['record']].add(row['package'])
    for record,scopes in identities.items():
        if len(scopes)>1:unknown.update(scopes)
    audit['unknown_packages']=sorted(unknown)
    linker.registration_order=order
    return rows,unknown,audit,packages

def build(records, linker, prefix, other_records=None, flagless=False):
    """Apply SetEnums in verified native-registration order, separately per package.

    flagless: the build's FEnumParams have no EnumFlags (UE 4.19-4.21), so UEnum has no flags
    form and its maximum is the plain signed maximum when the reducer is not recognised.
    """
    if not records:return {},{'registered_records':0,'complete_packages':0},{}
    out={x['name']:[(v,n.split('::',1)[-1]) for n,v in x['entries']] for x in records.values()}
    rows,unknown,audit,packages=discover(linker,records,other_records)
    from .enum_maximum import discover as maximum_policy,maximum as maximum_value
    policy,policy_evidence=maximum_policy(linker,records)
    if policy is None and flagless:
        policy='signed maximum';policy_evidence={'status':'engine generation without enum flags','unverified':policy_evidence}
    audit['maximum_policy']=policy or 'unverified'
    by_package=defaultdict(set);unscoped=[]
    registered={row['record'] for row in rows}
    for record,x in records.items():
        outer=x['outer']
        if outer not in packages:packages[outer]=linker.package_name(outer)
        if packages[outer]:by_package[packages[outer]].add(record)
        elif rows and record not in registered:
            # No native registration and no package: the runtime never constructs this enum.
            out.pop(x['name'],None);audit['unregistered_records']=audit.get('unregistered_records',0)+1
        else:unscoped.append(record)
    ordered=defaultdict(list);seen=set()
    for row in rows:
        key=(row['package'],row['record'])
        if key not in seen:ordered[row['package']].append(row['record']);seen.add(key)
    complete={p for p,ids in by_package.items() if p not in unknown and ids==set(ordered[p])}
    complete={p for p in complete if all(records[r]['form'] in (0,1,2) for r in by_package[p])}
    if unscoped:complete.clear()
    evidence=[];suppressed=0;unknown_maxima=0
    for pkg in sorted(complete):
        names=set()
        for record in ordered[pkg]:
            x=records[record];entries=x['entries'];full=[n for n,v in entries]
            qualifier=full[0].split('::',1)[0]+'::' if full and '::' in full[0] else (x['name']+'::' if not full and x['form'] in (1,2) else '')
            pre=prefix(x['name'],full);maximum=('' if '::' in pre else qualifier)+pre+'_MAX'
            existing={n.lower() for n in full};result=[(v,n.split('::',1)[-1]) for n,v in entries]
            collision=False
            if maximum.lower() not in existing and (qualifier+'MAX').lower() not in existing:
                if maximum.lower() in names:collision=True;suppressed+=1
                else:
                    value=maximum_value(entries,bool(x.get('flags',0)&1),policy)
                    if value is None:unknown_maxima+=1;collision=True
                    else:result.append((value,maximum.split('::',1)[-1]));full.append(maximum)
            # An automatic-name collision returns before AddNamesToPrimaryList.
            if not collision:names.update(n.lower() for n in full)
            out[x['name']]=result
            evidence.append({'record':record,'package':pkg,'automatic_name_collision':collision})
    audit.update(registered_records=len(seen),complete_packages=len(complete),
                 incomplete_packages=sorted(set(by_package)-complete),unscoped_records=len(unscoped),
                 automatic_name_collisions=suppressed,unverified_automatic_values=unknown_maxima)
    return out,audit,{'registrations':rows,'emission':evidence,'maximum_policy':policy_evidence}
