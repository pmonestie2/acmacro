"""Read-only PE inspection. Requires pefile and capstone; does not run the proxy."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys

# Keep analysis dependencies separate from the macro's Python environment.
sys.path.insert(0, str(Path(os.environ.get("TEMP", ".")) / "acmacro-protocol-tools"))
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--xref", action="append", type=lambda value: int(value, 0), default=[])
    parser.add_argument("--start", type=lambda value: int(value, 0))
    parser.add_argument("--length", type=lambda value: int(value, 0), default=0x200)
    parser.add_argument("--context", type=int, default=18)
    parser.add_argument("--strings")
    args = parser.parse_args()
    data = args.image.read_bytes()
    pe = pefile.PE(data=data)
    base = pe.OPTIONAL_HEADER.ImageBase
    print(f"Image: {args.image.name}; base={base:#x}; SHA256={hashlib.sha256(data).hexdigest()}")
    if pe.FILE_HEADER.Machine != 0x14c:
        raise SystemExit("This inspector expects the installed 32-bit x86 proxy")
    imports = {}
    for descriptor in pe.DIRECTORY_ENTRY_IMPORT:
        for symbol in descriptor.imports:
            imports[symbol.address] = descriptor.dll.decode() + "!" + (
                symbol.name.decode() if symbol.name else f"ordinal_{symbol.ordinal}")
    if args.strings:
        for match in re.finditer(rb"[ -~]{5,}", data):
            value = match.group().decode()
            if re.search(args.strings, value, re.I):
                print(f"{base + pe.get_rva_from_offset(match.start()):08x}: {value}")
    disassembler = Cs(CS_ARCH_X86, CS_MODE_32)
    disassembler.skipdata = True
    instructions = []
    for section in pe.sections:
        if section.Characteristics & 0x20000000:
            instructions.extend(disassembler.disasm_lite(section.get_data(), base + section.VirtualAddress))
    selected = set()
    for index, (address, size, mnemonic, operands) in enumerate(instructions):
        if args.start is not None and args.start <= address < args.start + args.length:
            selected.add(index)
        for target in args.xref:
            if re.search(r"\b" + hex(target) + r"\b", operands):
                print(f"XREF {target:#x} at {address:#x}")
                selected.update(range(max(0, index - args.context), min(len(instructions), index + args.context + 1)))
    previous = -2
    for index in sorted(selected):
        address, size, mnemonic, operands = instructions[index]
        if index != previous + 1:
            print("...")
        symbols = [name for target, name in imports.items() if hex(target) in operands]
        annotation = " ; " + ", ".join(symbols) if symbols else ""
        print(f"{address:08x}  {mnemonic:8s} {operands}{annotation}")
        previous = index


if __name__ == "__main__":
    main()
