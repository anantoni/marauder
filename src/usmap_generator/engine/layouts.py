"""Per-engine-version layouts of the UHT codegen records (UObject/UObjectGlobals.h, namespace
UECodeGen_Private, and UObjectBase.h for the registration tables), x64 shipping builds.

Every entry is general engine knowledge: it applies to every game built on that engine generation.
Nothing here is keyed to a game. The generator picks a layout per record family automatically by
counting how many records in the executable's data validate under each candidate (detect()).

Field spec: name -> (byte offset, struct format). "Observed" = confirmed on real executables during
this research; "from source" = taken from public engine source knowledge, not yet confirmed on a binary.
"""

# FPropertyParamsBase (+ ...WithOffset): NameUTF8, RepNotifyFuncUTF8, PropertyFlags, Flags (EPropertyGenFlags, u8),
# ObjectFlags (u32), [SetterFunc, GetterFunc], ArrayDim, [Offset], then the per-kind tail (type function).
PROP = {
    # No accessor pointers: observed on UE5.0; individual offsets are shared with UE4.
    "P50": {"dim": (0x20, "<I"), "acc": (), "off": (0x24, "<I"), "x1": 0x28, "x2": 0x30},
    # UE 5.1: setter/getter after ArrayDim (int32). Observed.
    "P51": {"dim": (0x20, "<I"), "acc": (0x28, 0x30), "off": (0x38, "<I"), "x1": 0x40, "x2": 0x48},
    # Later UE 5: setter/getter moved before a uint16 ArrayDim and uint16 Offset. Observed on 5.7 and on
    # builds whose version string is absent (structural detection only).
    "P5x": {"dim": (0x30, "<H"), "acc": (0x20, 0x28), "off": (0x32, "<H"), "x1": 0x38, "x2": 0x40},
    # UE4 has the same individual records as 5.0, but reverses the pointer-array traversal.
    "P4": {"dim": (0x20, "<I"), "acc": (), "off": (0x24, "<I"), "x1": 0x28, "x2": 0x30,
           "parent_first": True, "kind_mask": 0x1F},
    "P4x": {"dim": (0x20, "<I"), "acc": (), "off": (0x24, "<I"), "x1": 0x28, "x2": 0x30,
            "kind_mask": 0x1F},
    # UE 4.22: the same record, but the older property-kind numbering (one multicast delegate kind,
    # so Text = 0x1C and Enum = 0x1D). Observed on 4.22.
    "P422": {"dim": (0x20, "<I"), "acc": (), "off": (0x24, "<I"), "x1": 0x28, "x2": 0x30,
             "parent_first": True, "kind_mask": 0x1F, "gen": "legacy", "legacy": True},
    "P422x": {"dim": (0x20, "<I"), "acc": (), "off": (0x24, "<I"), "x1": 0x28, "x2": 0x30,
              "kind_mask": 0x1F, "gen": "legacy", "legacy": True},
    # UE 4.19-4.21 FPropertyParamsBase: EPropertyClass Type (u8, padded), NameUTF8, ObjectFlags (u32),
    # PropertyFlags (u64), ArrayDim (i32), RepNotifyFuncUTF8, [Offset (i32)], then the per-kind tail.
    # Observed on 4.19, 4.20 and 4.21.
    "P419": {"type_first": True, "name": 0x08, "rep": 0x28, "dim": (0x20, "<I"), "acc": (), "off": (0x30, "<I"),
             "x1": 0x38, "x2": 0x40, "parent_first": True, "gen": "legacy", "legacy": True},
    "P419x": {"type_first": True, "name": 0x08, "rep": 0x28, "dim": (0x20, "<I"), "acc": (), "off": (0x30, "<I"),
              "x1": 0x38, "x2": 0x40, "gen": "legacy", "legacy": True},
}

# FStructParams: OuterFunc, SuperFunc, StructOpsFunc, NameUTF8, then sizes/arrays/counts.
STRUCT = {
    # SIZE_T SizeOf, SIZE_T AlignOf, PropertyArray, int32 NumProperties, ObjectFlags, StructFlags. Observed (5.1).
    "S51": {"name": 0x18, "size": (0x20, "<Q"), "align": (0x28, "<Q"), "props": 0x30, "n": (0x38, "<I")},
    # PropertyArray, uint16 NumProperties, uint16 SizeOf, uint8 AlignOf, ObjectFlags, StructFlags. Observed.
    "S5x": {"name": 0x18, "size": (0x2A, "<H"), "align": (0x2C, "<B"), "props": 0x20, "n": (0x28, "<H")},
    # UE 4.19-4.21: ObjectFlags and StructFlags (u32 each) come before SIZE_T SizeOf/AlignOf. Observed.
    "S419": {"name": 0x18, "size": (0x28, "<Q"), "align": (0x30, "<Q"), "props": 0x38, "n": (0x40, "<I")},
}

# FClassParams: ClassNoRegisterFunc, ClassConfigNameUTF8, CppClassInfo, DependencySingletonFuncArray,
# FunctionLinkArray, PropertyArray, ImplementedInterfaceArray, then counts.
CLASS = {
    # four int32 counts, then ClassFlags. Observed (5.1).
    "C51": {"counts": "i32x4"},
    # one uint32 bitfield: NumDependencySingletons:4, NumFunctions:11, NumProperties:11,
    # NumImplementedInterfaces:6, then ClassFlags. Observed.
    "C5x": {"counts": "bits4_11_11_6"},
    # UE 4.19-4.21: ClassNoRegisterFunc, DependencySingletonFuncArray, int32 NumDependencySingletons,
    # uint32 ClassFlags, FunctionLinkArray, int32 NumFunctions, PropertyArray, int32 NumProperties,
    # ClassConfigNameUTF8, CppClassInfo, ImplementedInterfaceArray, int32 NumImplementedInterfaces. Observed.
    "C419": {"counts": "interleaved", "deps": 0x08, "nd": 0x10, "funcs": 0x18, "nf": 0x20, "props": 0x28,
             "np": 0x30, "cfg": 0x38, "ifaces": 0x48, "ni": 0x50},
}

# FEnumParams: OuterFunc, DisplayNameFunc, NameUTF8, CppTypeUTF8, EnumValues, then counts/flags.
ENUM = {
    # int32 NumEnums, ObjectFlags, uint8 EnumFlags, uint8 CppForm. Observed (5.1; flag/form offsets from source).
    "E51": {"n": (0x28, "<I"), "object_flags":0x2C, "flags": 0x30, "form": 0x31},
    # ObjectFlags, int16 NumEnums, uint8 EnumFlags, uint8 CppForm (record size 0x30). Observed.
    "E5x": {"n": (0x2C, "<H"), "object_flags":0x28, "flags": 0x2E, "form": 0x2F},
    # UE 4.19-4.21: OuterFunc, EDynamicType, NameUTF8, ObjectFlags, DisplayNameFunc, uint8 CppForm,
    # CppTypeUTF8, EnumeratorParams, int32 NumEnumerators; no enum flags. Observed.
    "E419": {"name": 0x10, "cpp": 0x30, "values": 0x38, "n": (0x40, "<I"), "object_flags": 0x18,
             "flags": None, "form": 0x28},
}

# FFunctionParams changes independently of property/class packing. All three are observed:
# in 5.1, 5.4 and 5.7 shipping builds. Exact transition versions remain
# unverified; the reader validates records instead of deriving this family from PROP.
FUNCTION = {
    "F51": {"props": 0x30, "n": (0x38, "<I"), "size": 0x28, "flags": 0x3C},
    "F5xNames": {"props": 0x28, "n": (0x30, "<H"), "size": 0x32, "flags": 0x34},
    "F5x": {"props": 0x18, "n": (0x20, "<H"), "size": 0x22, "flags": 0x24},
}
# UE 4.19-4.21 FFunctionParams (observed: OuterFunc, NameUTF8, ObjectFlags, SuperFunc, FunctionFlags,
# SIZE_T StructureSize, PropertyArray, int32 NumProperties) are not read: UE4 has no registration
# tables to join them to their owner, as for every UE4 layout.

# EPropertyGenFlags type ids (low 6 bits). Stable across UE 5.0-5.7 for 0x00-0x20 (observed on 5.1 and
# 5.7). Newer dispatchers use 0x22 for VerseDynamic, 0x23 for Utf8Str and 0x24 for
# AnsiStr. VerseDynamic/VerseString are deliberately unsupported until their complete
# serialization semantics are verified; never relabel them as ordinary strings.
GEN = {0: "Byte", 1: "Int8", 2: "Int16", 3: "Int", 4: "Int64", 5: "UInt16", 6: "UInt32", 7: "UInt64",
       0x0A: "Float", 0x0B: "Double", 0x0C: "Bool", 0x0D: "SoftClass", 0x0E: "WeakObject", 0x0F: "LazyObject",
       0x10: "SoftObject", 0x11: "Class", 0x12: "Object", 0x13: "Interface", 0x14: "Name", 0x15: "Str",
       0x16: "Array", 0x17: "Map", 0x18: "Set", 0x19: "Struct", 0x1A: "Delegate", 0x1B: "InlineMulticastDelegate",
       0x1C: "SparseMulticastDelegate", 0x1D: "Text", 0x1E: "Enum", 0x1F: "FieldPath", 0x20: "LWCReal",
       0x21: "Optional", 0x23: "Utf8Str", 0x24: "AnsiStr"}

# UE 4.19-4.22 numbering (EPropertyClass / early EPropertyGenFlags): one multicast delegate kind,
# then Text and Enum. Observed on 4.19-4.22; 4.23 split the delegate kinds.
GEN_LEGACY = {k: v for k, v in GEN.items() if k < 0x1B}
GEN_LEGACY.update({0x1B: "InlineMulticastDelegate", 0x1C: "Text", 0x1D: "Enum"})

# Which version range a combination of detected layouts indicates (for the report only).
def version_hint(chosen):
    p = chosen.get("prop")
    if p == "P4": return "UE 4 (parent-first property arrays)"
    if p == "P4x": return "UE 4 (child-first arrays, five-bit property kinds)"
    if p in ("P422", "P422x"): return "UE 4.22 (name-first records, early property kinds)"
    if p in ("P419", "P419x"): return "UE 4.19-4.21 (type-first property records)"
    if p == "P50": return "unpacked property layout compatible with UE4/UE5.0"
    if p == "P51": return "UE 5 (accessor layout observed on 5.1/5.2)"
    if p == "P5x": return "UE 5 (packed layout observed on 5.3-5.7)"
    return "unknown"

# Intrinsic UObject classes (declared in C++ with IMPLEMENT_CORE_INTRINSIC_CLASS, no registration entry):
# name -> super. Engine-wide; the same for every game. Newer engines give some of them reflected
# properties (observed on 5.7: Field.Next, Struct.SuperStruct/Children/..., Class.ClassWithin/...); their
# FClassParams are found in data but unnamed, so they are recognised by their first property's name.
INTRINSIC = {"Object": None, "Field": "Object", "Struct": "Field", "Class": "Struct", "Function": "Struct",
             "ScriptStruct": "Struct", "Enum": "Field", "DelegateFunction": "Function",
             "SparseDelegateFunction": "DelegateFunction", "Package": "Object", "Interface": "Object"}
INTRINSIC_FIRST_PROP = {"Next": "Field", "SuperStruct": "Struct", "ClassWithin": "Class"}
