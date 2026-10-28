"""Strict FFS verification; only the selected TBU PE32 relocation is permitted."""
from __future__ import annotations
from hashlib import sha256
import struct
from .constants import TBU_TARGET_GUID
from .firmware import parse_ffs_at


def _u16(b, p): return struct.unpack_from('<H', b, p)[0]
def _u32(b, p): return struct.unpack_from('<I', b, p)[0]


def relocated_tbu(expected: bytes, file_offset: int, rom_size: int):
    """Derive every permitted byte from the input, never from the actual payload.

    Supports the uncompressed, memory-mapped IA32 PE32 ser8989 format only.
    Unsupported formats fail closed instead of receiving a relaxed comparison.
    """
    f = parse_ffs_at(expected, 0, TBU_TARGET_GUID)
    if f.size != len(expected) or f.ffs_type != 6 or f.attributes != 0 or f.state != 0xF8:
        raise ValueError('unsupported TBU FFS format for relocation verification')
    if not 0 < rom_size <= 0x100000000 or not 0 <= file_offset <= rom_size-len(expected):
        raise ValueError('invalid ROM mapping or target bounds')
    if (sum(expected[:24])-expected[17]-expected[23]) & 255 or expected[17] != 0xAA:
        raise ValueError('invalid supplied TBU FFS checksum')
    sections=[]; p=24
    while p<len(expected):
        if p+4>len(expected): raise ValueError('truncated section header')
        n=int.from_bytes(expected[p:p+3], 'little'); typ=expected[p+3]
        if n<4 or n==0xFFFFFF or p+n>len(expected): raise ValueError('invalid section bounds')
        sections.append((p,n,typ))
        end=p+n; p=(end+3)&~3
        if any(expected[end:min(p,len(expected))]):
            raise ValueError('nonzero section alignment padding')
    if p != (len(expected)+3)&~3:
        raise ValueError('invalid section framing')
    executable=[s for s in sections if s[2]==0x10]
    if len(executable)!=1 or any(t not in (0x10,0x1B,0x14,0x15) for _,_,t in sections):
        raise ValueError('unsupported TBU executable section layout')
    off,n,_=executable[0]; start=off+4; pe=expected[start:off+n]
    if len(pe)<64 or pe[:2]!=b'MZ': raise ValueError('TBU image is not PE32')
    nt=_u32(pe,60); opt=nt+24
    if nt<64 or opt+224>len(pe) or pe[nt:nt+4]!=b'PE\0\0': raise ValueError('invalid PE headers')
    if _u16(pe,nt+4)!=0x14C or _u16(pe,opt)!=0x10B or _u16(pe,nt+20)<224:
        raise ValueError('only IA32 PE32 TBU relocation is supported')
    if _u16(pe,nt+22)&1 or _u32(pe,opt+92)<6: raise ValueError('relocations unavailable')
    count=_u16(pe,nt+6); table=opt+_u16(pe,nt+20)
    if count<1 or table+count*40>len(pe): raise ValueError('invalid PE section table')
    headers_end=_u32(pe,opt+60); ranges=[]
    if headers_end<table+count*40 or headers_end>len(pe): raise ValueError('invalid PE header size')
    for i in range(count):
        row=table+i*40; rva=_u32(pe,row+12); size=_u32(pe,row+16); raw=_u32(pe,row+20)
        if not size: continue
        if rva!=raw or raw<headers_end or raw+size>len(pe):
            raise ValueError('PE sections are not a validated memory-mapped layout')
        if any(raw<b and a<raw+size for a,b in ranges): raise ValueError('overlapping PE sections')
        ranges.append((raw,raw+size))
    def in_section(p,n): return any(a<=p and p+n<=b for a,b in ranges)
    reloc=_u32(pe,opt+136); size=_u32(pe,opt+140)
    # ser8989 stores its relocation table in the file-backed gap between sections.
    if size<8 or reloc<headers_end or reloc+size>len(pe): raise ValueError('invalid relocation directory')
    old=_u32(pe,opt+28); new=0x100000000-rom_size+file_offset+start
    if new>0xFFFFFFFF: raise ValueError('PE32 address overflow')
    delta=(new-old)&0xFFFFFFFF; out=bytearray(expected); edits=[]; occupied=set()
    struct.pack_into('<I',out,start+opt+28,new)
    p=reloc
    while p<reloc+size:
        if p+8>reloc+size: raise ValueError('truncated relocation block')
        page=_u32(pe,p); block=_u32(pe,p+4)
        if page&0xFFF or block<8 or block%2 or p+block>reloc+size:
            raise ValueError('invalid relocation block')
        for q in range(p+8,p+block,2):
            entry=_u16(pe,q); typ=entry>>12
            if typ==0: continue
            if typ!=3: raise ValueError('unsupported relocation type: '+str(typ))
            at=page+(entry&0xFFF)
            if not in_section(at,4) or (at<reloc+size and reloc<at+4):
                raise ValueError('relocation targets headers, relocation table or invalid data')
            if occupied.intersection(range(at,at+4)): raise ValueError('overlapping relocation targets')
            occupied.update(range(at,at+4))
            before=_u32(pe,at); after=(before+delta)&0xFFFFFFFF
            struct.pack_into('<I',out,start+at,after)
            edits.append({'ffs_offset':start+at,'before':before,'after':after,'type':'HIGHLOW'})
        p+=block
    return bytes(out), {'mode':'verified-pe32-relocation','old_image_base':old,
        'new_image_base':new,'delta_u32':delta,'relocations':edits,
        'changed_bytes':sum(a!=b for a,b in zip(expected,out)),
        'expected_sha256':sha256(expected).hexdigest(),'relocated_sha256':sha256(out).hexdigest()}


def verify_replacement(expected, actual, file_offset, rom_size, allow_tbu_relocation=False):
    if allow_tbu_relocation:
        canonical, info=relocated_tbu(expected,file_offset,rom_size)
    else:
        canonical=expected; info={'mode':'exact-ffs'}
    if actual!=canonical:
        raise ValueError('FFS differs from the exact expected replacement after validated relocation' if allow_tbu_relocation
                         else 'FFS differs from the exact expected replacement')
    return info


def validate_input_mapping(ffs, rom_size):
    """Require the original target to confirm the top-of-4GiB mapping used."""
    b=ffs.data; p=ffs.header_size
    while p+4<=len(b):
        n=int.from_bytes(b[p:p+3],'little')
        if n<4 or p+n>len(b): break
        if b[p+3]==0x10:
            pe=b[p+4:p+n]
            if len(pe)<64 or pe[:2]!=b'MZ': break
            nt=_u32(pe,60); opt=nt+24
            if opt+32>len(pe) or pe[nt:nt+4]!=b'PE\0\0' or _u16(pe,opt)!=0x10B: break
            physical=0x100000000-rom_size+ffs.offset+p+4
            if _u32(pe,opt+28)!=physical:
                raise ValueError('input TBU target does not confirm the required ROM address mapping')
            return
        p=(p+n+3)&~3
    raise ValueError('input TBU target has no supported mapped PE32 executable')
