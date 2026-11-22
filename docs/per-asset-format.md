# Per-asset mapping format (version 1)

`marauder generate --per-asset` writes two files with the same content:
`<name>.perasset.json` and `<name>.perasset.bin`. They hold every UE5 Anim Blueprint generated struct
(`AnimBlueprintGeneratedConstantData`, `AnimBlueprintGeneratedMutableData`) found in the cooked
packages, keyed by the package that exports it. A `.usmap` keeps one record per name, so it cannot
hold these: each Anim Blueprint has its own struct under the same name.

## Lookup rule

When a reader resolves a struct **by name** while reading package `P`:

1. look up `perasset[P][name]`;
2. if absent, look up the global `.usmap`.

A child Anim Blueprint's constant-data struct has its parent's struct as super, under the same name.
Its `superPackage` names the package that exports that parent struct: resolve the super with
`perasset[superPackage][name]`, not by name in the global map.

## JSON

```json
{
  "format": "usmap-per-asset",
  "version": 1,
  "engineVersion": "5.3",
  "packages": [
    {
      "package": "/Game/Characters/ABP_Hero",
      "structs": [
        {
          "owner": "ABP_Hero_C",
          "name": "AnimBlueprintGeneratedConstantData",
          "super": "AnimBlueprintConstantData",
          "superPackage": null,
          "propertyCount": 2,
          "properties": [
            {"index": 0, "arrayDim": 1, "name": "Speed", "type": {"kind": "FloatProperty"}},
            {"index": 1, "arrayDim": 1, "name": "Bones",
             "type": {"kind": "ArrayProperty", "inner": {"kind": "StructProperty", "struct": "BoneReference"}}}
          ]
        }
      ]
    }
  ]
}
```

- `owner` is the class that owns the struct, when the package records it (may be `null`).
- `propertyCount` is the struct's property count as in a `.usmap` record (it can be larger than the
  number of listed properties when a property has an array size above 1).
- `type.kind` is a `.usmap` property type name. `StructProperty` has `struct`; `EnumProperty` has
  `inner` and `enum`; `ArrayProperty`, `SetProperty` and `OptionalProperty` have `inner`;
  `MapProperty` has `key` and `value`.
- Packages are sorted by name; structs keep the order in which they were read.

## Binary

Little-endian. Struct records use the `.usmap` version 4 encoding, so `.usmap` reader code can be reused.

```
header   char[4] magic = "UPAM"
         u8      version = 1
         u8      compression = 0 (none)
         u16     reserved = 0
         u32     payload size in bytes
payload  names     u32 count, then per name: u16 byte length, UTF-8 bytes
         packages  u32 count, then per package:
                     u32 package name index
                     u32 struct count, then per struct:
                       u32 owner name index          (0xFFFFFFFF = none)
                       u32 super package name index  (0xFFFFFFFF = none)
                       -- .usmap struct record --
                       u32 struct name index
                       u32 super name index          (0xFFFFFFFF = none)
                       u16 property count
                       u16 serialized property count, then per property:
                         u16 index, u8 array dim, u32 name index, property type tree
```

The property type tree is the `.usmap` one: a `u8` type id, then for `StructProperty` a `u32` struct
name index, for `EnumProperty` the inner type tree and a `u32` enum name index, for `ArrayProperty`,
`SetProperty` and `OptionalProperty` the inner type tree, and for `MapProperty` the key and value type
trees. A reference reader and writer are in `src/usmap_generator/perasset.py`.
