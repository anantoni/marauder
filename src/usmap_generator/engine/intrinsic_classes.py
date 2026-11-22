"""Intrinsic classes that only builds with assertions enabled (Development, DebugGame) register.

Shipping builds in the benchmark never list these classes; builds that keep assertion texts do.
Two data markers are used, both strings in the executable:
- UE5 assertion texts name each class's registration record ("Z_Registration_Info_UClass_U<Name>.");
  a class is added when its record is named.
- Builds whose assertion texts include the linker placeholder check are non-shipping; UE4 builds
  of that kind register the whole set (the dynamic class only when its type name is present).
Records carry no properties; supers follow the engine's class declarations.
"""
SUPERS = {"GCBarrier": "Object", "LinkerPlaceholderClass": "Class", "LinkerPlaceholderFunction": "Function",
          "LinkerPlaceholderExportObject": "Object", "Property": "Field", "PackageMap": "Object",
          "DynamicClass": "Class"}
ASSERTION_MARKER = b"IsA<ULinkerPlaceholderFunction"


def recover(data, structs):
    """Add missing intrinsic class records to structs (in place). Returns the added names."""
    added = []
    named = {n for n in SUPERS if f"Z_Registration_Info_UClass_U{n}.".encode() in data}
    if not named and ASSERTION_MARKER in data:
        named = {n for n in SUPERS if n not in ("GCBarrier", "DynamicClass")}
        if b"UDynamicClass" in data:
            named.add("DynamicClass")
    for n in sorted(named):
        if n not in structs:
            structs[n] = {"super": SUPERS[n], "count": 0, "props": []}
            added.append(n)
    return added
