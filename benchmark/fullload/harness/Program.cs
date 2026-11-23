// Full-asset-load check: mount an unencrypted game, load every package in a list with a given usmap (or none),
// and record per package whether every export deserializes fully plus data fingerprints.
//   UsmapLoadCheck --paks <dir[:dir]> --game GAME_UE5_4 --oodle <lib> list <out.txt>
//   UsmapLoadCheck --paks <dir> --game GAME_UE5_4 --oodle <lib> [--usmap <f>] [--pkgtimeout secs] check <list.txt> <out.jsonl>
// Row fields: st ok|partial|failed|crash|timeout (ok = every export loads, no exception, export ends at the right byte),
// soft = nested reads CUE4Parse skipped or repositioned silently, full = st ok and no soft events,
// h = exact JSON hash, hn = hash with object keys lowercased (values untouched), hl = whole JSON lowercased,
// abp = package holds an AnimBlueprintGeneratedClass (by loaded export type or Anim generated-struct lookup).
// Stops with exit code 3 if any archive or entry is encrypted. Resumable: skips paths already in out.jsonl.
using System.Security.Cryptography;
using System.Text;
using CUE4Parse;
using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;
using Serilog;

Log.Logger = new LoggerConfiguration().MinimumLevel.Fatal().CreateLogger();
CUE4ParseLog.UseLogger(Log.Logger);
Globals.FatalObjectSerializationErrors = true;
Globals.WarnMissingImportPackage = false;
Globals.LogVfsMounts = false;

var opts = new Dictionary<string, string>();
var rest = new List<string>();
for (var i = 0; i < args.Length; i++)
{
    if (args[i].StartsWith("--") && i + 1 < args.Length) opts[args[i][2..]] = args[++i];
    else rest.Add(args[i]);
}
var game = Enum.Parse<EGame>(opts.GetValueOrDefault("game", "GAME_UE5_LATEST"));
if (opts.TryGetValue("oodle", out var oodle)) OodleHelper.Initialize(oodle);
var dirs = opts["paks"].Split(':');
var provider = new DefaultFileProvider(new DirectoryInfo(dirs[0]), dirs.Skip(1).Select(d => new DirectoryInfo(d)).ToArray(),
    SearchOption.AllDirectories, true, new VersionContainer(game));
if (opts.TryGetValue("usmap", out var usmap)) provider.MappingsContainer = new CUE4Parse.MappingsProvider.Usmap.FileUsmapTypeMappingsProvider(usmap);
provider.Initialize();
if (provider.UnloadedVfs.Any(v => v.IsEncrypted)) { Console.Error.WriteLine("ENCRYPTED archive: stopped"); Environment.Exit(3); }
provider.Mount();
if (provider.MountedVfs.Any(v => v.EncryptedFileCount > 0) || provider.Files.Values.Any(f => f.IsEncrypted))
{ Console.Error.WriteLine("ENCRYPTED entries: stopped"); Environment.Exit(3); }
provider.PostMount();
if (opts.GetValueOrDefault("vpaths", "0") == "1") provider.LoadVirtualPaths();   // plugin mount points (/Wwise/, ...) for import resolution

static bool IsPkg(string k) => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase);
var cmd = rest[0];
if (cmd == "list")
{
    var keys = provider.Files.Keys.Where(IsPkg).OrderBy(k => k, StringComparer.Ordinal).ToList();
    File.WriteAllLines(rest[1], keys);
    Console.WriteLine(JsonConvert.SerializeObject(new { packages = keys.Count, vfs = provider.MountedVfs.Count }));
    return;
}
if (cmd != "check") throw new Exception("unknown command " + cmd);

var outPath = rest[2];
var curPath = outPath + ".cur";
var dumpDir = opts.GetValueOrDefault("dump"); if (dumpDir != null) Directory.CreateDirectory(dumpDir);
var diag = opts.GetValueOrDefault("diag", "0") == "1";
var pkgTimeout = int.Parse(opts.GetValueOrDefault("pkgtimeout", "900"));
var done = new HashSet<string>();
if (File.Exists(outPath))
    foreach (var line in File.ReadLines(outPath))
        try { done.Add((string)Newtonsoft.Json.Linq.JObject.Parse(line)["p"]!); } catch { }
var outw = new StreamWriter(outPath, append: true);
var wlock = new object();
void Emit(Dictionary<string, object?> r) { lock (wlock) { outw.WriteLine(JsonConvert.SerializeObject(r)); outw.Flush(); } }
// a previous process died inside this package (OOM kill, stack overflow): record it and move on
if (File.Exists(curPath))
{
    var c = File.ReadAllText(curPath).Trim();
    if (c.Length > 0 && !done.Contains(c)) { Emit(new() { ["p"] = c, ["st"] = "crash" }); done.Add(c); }
}
var settings = new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
var mism = new List<string>();
var soft = new Dictionary<string, int>();
TypeMappings.OnPositionMismatch = (t, rem) => mism.Add($"{t}:{rem}");
TypeMappings.OnSoft = k => soft[k] = soft.GetValueOrDefault(k) + 1;

string? curPkg = null; var pkgStart = DateTime.UtcNow;
var watchdog = new Thread(() =>
{
    while (true)
    {
        Thread.Sleep(5000);
        var p = curPkg;
        if (p != null && (DateTime.UtcNow - pkgStart).TotalSeconds > pkgTimeout)
        {
            Emit(new() { ["p"] = p, ["st"] = "timeout", ["ms"] = pkgTimeout * 1000 });
            try { File.WriteAllText(curPath, ""); } catch { }
            Environment.Exit(4);
        }
    }
}) { IsBackground = true };
watchdog.Start();

static Dictionary<string, object?> Diag(int i, string type, Exception e)
{
    var chain = new List<string>(); var frames = new List<string>();
    for (var x = e; x != null; x = x.InnerException)
    {
        chain.Add($"{x.GetType().Name}: {(x.Message.Length > 240 ? x.Message[..240] : x.Message)}");
        if (x.InnerException == null && x.StackTrace != null)
            foreach (var l in x.StackTrace.Split('\n'))
            {
                var t = l.Trim(); if (!t.StartsWith("at ")) continue;
                t = t[3..]; var c = t.IndexOf(" in "); if (c > 0) t = t[..c];
                frames.Add(t.Length > 200 ? t[..200] : t); if (frames.Count >= 8) break;
            }
    }
    return new() { ["i"] = i, ["type"] = type, ["chain"] = chain, ["frames"] = frames };
}
static string TryType(CUE4Parse.UE4.Assets.IPackage pkg, int i)
{
    try
    {
        CUE4Parse.UE4.Assets.ResolvedObject? c = null;
        if (pkg is CUE4Parse.UE4.Assets.Package lp) c = lp.ResolvePackageIndex(lp.ExportMap[i].ClassIndex);
        if (pkg is CUE4Parse.UE4.Assets.IoPackage io) c = io.ResolveObjectIndex(io.ExportMap[i].ClassIndex);
        if (c == null) return "?";
        var o = c.Outer; var mod = o?.Name.Text ?? "";
        while (o?.Outer != null) { o = o.Outer; mod = o.Name.Text; }
        return mod + "|" + c.Name.Text;
    }
    catch { }
    return "?";
}

// Streaming replacement for the old string-based hashing: the export JSON is written as UTF-8 into a spill buffer
// (memory up to SPILL_MAX bytes, then a temp file), never as one .NET string (limit ~2^30 chars: an AnimSequence
// export overflowed it). The byte streams hashed are exactly the old ones: h = UTF-8 of the JSON, hl = UTF-8 of the
// invariant-lowercased JSON, hn = UTF-8 of the JSON re-written with lowercased property names (see LowerKeysStream).
var spillMax = long.Parse(Environment.GetEnvironmentVariable("SPILL_MAX") ?? "67108864");
var spillDir = Path.GetDirectoryName(Path.GetFullPath(outPath))!;
var jsonSer = JsonSerializer.CreateDefault(settings);

static void LowerKeysStream(Stream src, Stream dst)
{
    using var r = new JsonTextReader(new StreamReader(src, new UTF8Encoding(false), false, 1 << 16, true)) { DateParseHandling = DateParseHandling.None, FloatParseHandling = FloatParseHandling.Double };
    using var sw = new StreamWriter(dst, new UTF8Encoding(false), 1 << 16, true);
    using var w = new JsonTextWriter(sw) { Formatting = Formatting.None };
    while (r.Read())
    {
        if (r.TokenType == JsonToken.PropertyName) w.WritePropertyName(((string)r.Value!).ToLowerInvariant());
        else w.WriteToken(r, false);
    }
    w.Flush();
}
static void FeedStream(HashAlgorithm a, Stream s)
{
    var buf = new byte[1 << 16]; int n;
    while ((n = s.Read(buf, 0, buf.Length)) > 0) a.TransformBlock(buf, 0, n, null, 0);
}
static void FeedLowered(HashAlgorithm a, Stream s)
{
    using var rd = new StreamReader(s, new UTF8Encoding(false), false, 1 << 16, true);
    var enc = new UTF8Encoding(false).GetEncoder();
    var cb = new char[1 << 16]; var bb = new byte[(1 << 16) * 3 + 8]; int carry = 0;
    while (true)
    {
        var n = rd.Read(cb, carry, cb.Length - carry);
        var end = n <= 0; n = (end ? 0 : n) + carry; carry = 0;
        if (!end && n > 0 && char.IsHighSurrogate(cb[n - 1])) { carry = 1; n--; }   // keep a surrogate pair in one piece
        if (n > 0)
        {
            var low = new string(cb, 0, n).ToLowerInvariant();
            var k = enc.GetBytes(low.AsSpan(), bb, false);
            a.TransformBlock(bb, 0, k, null, 0);
        }
        if (end) break;
        if (carry == 1) cb[0] = cb[n];
    }
    var f = enc.GetBytes(ReadOnlySpan<char>.Empty, bb, true);
    if (f > 0) a.TransformBlock(bb, 0, f, null, 0);
}

foreach (var path in File.ReadAllLines(rest[1]))
{
    if (path.Length == 0 || done.Contains(path)) continue;
    File.WriteAllText(curPath, path);
    mism.Clear(); soft.Clear();
    TypeMappings.Touched = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    pkgStart = DateTime.UtcNow; curPkg = path;
    var sw = System.Diagnostics.Stopwatch.StartNew();
    var row = new Dictionary<string, object?> { ["p"] = path };
    var excs = new Dictionary<string, int>();
    var fx = new List<Dictionary<string, object?>>();
    string? firstErr = null; int nEx = 0, nOk = 0, nBad = 0; bool unv = false, abp = false; int lkFail = 0;
    using var sha = SHA1.Create(); using var shn = SHA1.Create(); using var shl = SHA1.Create();
    void Feed(HashAlgorithm a, string s) { var b = Encoding.UTF8.GetBytes(s); a.TransformBlock(b, 0, b.Length, null, 0); }
    try
    {
        var pkg = provider.LoadPackage(path);
        unv = pkg.HasFlags(CUE4Parse.UE4.Objects.UObject.EPackageFlags.PKG_UnversionedProperties);
        nEx = pkg.ExportsLazy.Length;
        for (var i = 0; i < nEx; i++)
        {
            var before = mism.Count;
            try
            {
                var o = pkg.ExportsLazy[i].Value;
                if (o.ExportType == "AnimBlueprintGeneratedClass") abp = true;
                using var raw = new Spill(spillMax, spillDir);
                using (var rsw = new StreamWriter(raw, new UTF8Encoding(false), 1 << 16, true))
                using (var jw = new JsonTextWriter(rsw) { Formatting = jsonSer.Formatting })
                { jsonSer.Serialize(jw, o, null); jw.Flush(); }
                if (dumpDir != null) File.AppendAllText(Path.Combine(dumpDir, Convert.ToHexString(SHA1.HashData(Encoding.UTF8.GetBytes(path)))[..16] + ".json"), $"{{\"export\":{i},\"data\":{JsonConvert.SerializeObject(o, Formatting.Indented, settings)}}}\n");
                raw.Rewind(); FeedStream(sha, raw);
                raw.Rewind(); FeedLowered(shl, raw);
                using (var lk = new Spill(spillMax, spillDir))
                {
                    var lkOk = true;
                    try { raw.Rewind(); LowerKeysStream(raw, lk); } catch (JsonException) { lkOk = false; }
                    if (lkOk) { lk.Rewind(); FeedStream(shn, lk); }
                    else { Feed(shn, "#LKFAIL"); raw.Rewind(); FeedStream(shn, raw); lkFail++; }
                }
                if (mism.Count == before) nOk++; else { nBad++; if (diag && fx.Count < 6) fx.Add(new() { ["i"] = i, ["type"] = TryType(pkg, i), ["class"] = o.GetType().Name, ["mm"] = mism[^1] }); }
            }
            catch (Exception e)
            {
                nBad++;
                var inner = e; while (inner.InnerException != null) inner = inner.InnerException;
                var k = inner.GetType().Name; excs[k] = excs.GetValueOrDefault(k) + 1;
                firstErr ??= inner.Message.Length > 300 ? inner.Message[..300] : inner.Message;
                if (diag && fx.Count < 6) fx.Add(Diag(i, pkg.ExportsLazy.Length > i ? TryType(pkg, i) : "?", e));
                var marker = $"#ERR{i}:{k}";
                Feed(sha, marker); Feed(shn, marker); Feed(shl, marker);
            }
        }
        row["st"] = nEx == 0 ? "ok" : nOk == nEx ? "ok" : nOk == 0 ? "failed" : "partial";
    }
    catch (Exception e)
    {
        var inner = e; while (inner.InnerException != null) inner = inner.InnerException;
        excs[inner.GetType().Name] = 1; firstErr = inner.Message.Length > 300 ? inner.Message[..300] : inner.Message;
        if (diag) fx.Add(Diag(-1, "package", e));
        row["st"] = "failed";
    }
    curPkg = null;
    foreach (var t in TypeMappings.Touched)
        if (t.StartsWith("AnimBlueprintGenerated", StringComparison.OrdinalIgnoreCase)) { abp = true; break; }
    sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0); shn.TransformFinalBlock(Array.Empty<byte>(), 0, 0); shl.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
    row["ex"] = nEx; row["okx"] = nOk; row["unv"] = unv;
    if (abp) row["abp"] = true;
    if (excs.Count > 0) row["exc"] = excs;
    if (firstErr != null) row["err"] = firstErr;
    if (mism.Count > 0) row["mm"] = mism.Take(5).ToList();
    if (fx.Count > 0) row["fx"] = fx;
    if (lkFail > 0) row["lkfail"] = lkFail;
    if (soft.Count > 0) row["soft"] = soft.OrderByDescending(kv => kv.Value).Take(8).ToDictionary(kv => kv.Key, kv => kv.Value);
    row["full"] = (string)row["st"]! == "ok" && soft.Count == 0;
    row["h"] = Convert.ToHexString(sha.Hash!)[..16];
    row["hn"] = Convert.ToHexString(shn.Hash!)[..16];
    row["hl"] = Convert.ToHexString(shl.Hash!)[..16];
    row["ms"] = sw.ElapsedMilliseconds;
    Emit(row);
}
File.WriteAllText(curPath, "");
outw.Dispose();

sealed class Spill : Stream
{
    Stream s = new MemoryStream(); readonly long max; readonly string dir;
    public Spill(long max, string dir) { this.max = max; this.dir = dir; }
    public void Rewind() { s.Position = 0; }
    public override void Write(byte[] b, int o, int c)
    {
        if (s is MemoryStream && s.Length + c > max)
        {
            Directory.CreateDirectory(dir);
            var f = new FileStream(Path.Combine(dir, "spill-" + Guid.NewGuid().ToString("N") + ".tmp"), FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 16, FileOptions.DeleteOnClose);
            s.Position = 0; s.CopyTo(f); s.Dispose(); s = f;
        }
        s.Write(b, o, c);
    }
    public override int Read(byte[] b, int o, int c) => s.Read(b, o, c);
    public override bool CanRead => true; public override bool CanSeek => true; public override bool CanWrite => true;
    public override long Length => s.Length;
    public override long Position { get => s.Position; set => s.Position = value; }
    public override void Flush() { }
    public override long Seek(long o, SeekOrigin so) => s.Seek(o, so);
    public override void SetLength(long v) => s.SetLength(v);
    protected override void Dispose(bool d) { if (d) s.Dispose(); base.Dispose(d); }
}
