"""Find the game executable and its archives in an installed Unreal Engine game folder."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SHIPPING_SUFFIXES = ("-Win64-Shipping.exe", "-WinGDK-Shipping.exe", "-Win64-Test.exe")
SKIP_DIRS = {"CrashReportClient", "Redist", "__Installer", "_CommonRedist", "DirectX", "Prereq", "Prerequisites"}


class InstallError(Exception):
    """The folder does not look like an installed Unreal Engine game; the message says what to do."""


@dataclass
class Install:
    root: Path
    exe: Path
    project: Path                      # <project>/ (holds Binaries/ and Content/)
    paks: List[Path] = field(default_factory=list)       # .pak archives under */Content/Paks
    containers_dir: Optional[Path] = None                # where .utoc/.ucas/.pak live (or loose files)

    @property
    def has_iostore(self):
        return bool(self.containers_dir) and any(p.suffix == ".utoc" for p in self.containers_dir.rglob("*.utoc"))


def _walk(root, depth=5):
    root = Path(root)
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and len(d.parts) - base < depth]
        yield d, filenames


def find_executables(root):
    out = []
    for d, files in _walk(root, depth=4):
        if d.name.lower() not in ("win64", "wingdk"): continue
        for f in files:
            if f.endswith(SHIPPING_SUFFIXES): out.append(d / f)
    return sorted(out)


def project_of(exe):
    """<project>/Binaries/Win64/<exe> -> <project>."""
    exe = Path(exe)
    if len(exe.parents) < 3 or exe.parents[1].name.lower() != "binaries":
        raise InstallError(f"{exe}: expected the executable inside <Project>/Binaries/Win64/. "
                           "Pass --exe with the *-Win64-Shipping.exe file of the game.")
    return exe.parents[2]


def locate(folder, exe=None, paks_dir=None):
    folder = Path(folder).expanduser()
    if exe:
        exe = Path(exe).expanduser()
        if not exe.is_file(): raise InstallError(f"--exe {exe}: file not found.")
        if not folder.is_dir(): folder = project_of(exe).parent
    else:
        if folder.is_file() and folder.suffix.lower() == ".exe":
            exe, folder = folder, project_of(folder).parent
        elif not folder.is_dir():
            raise InstallError(f"{folder}: folder not found. Pass the game's install folder "
                               "(the one that holds the game's project folder and Engine/).")
        else:
            found = find_executables(folder)
            if not found:
                raise InstallError(f"{folder}: no Unreal Engine shipping executable (*-Win64-Shipping.exe) found "
                                   "within 4 folder levels. Pass the install folder, or the executable with --exe.")
            if len(found) > 1:
                listing = "\n  ".join(str(p) for p in found)
                raise InstallError(f"{folder}: several shipping executables found; choose one with --exe:\n  {listing}")
            exe = found[0]
    project = project_of(exe)
    root = project.parent                                   # install root: <root>/<Project>/Binaries/...
    paks = sorted(p for p in Path(root).rglob("*.pak")
                  if "/Content/Paks" in p.as_posix() and "CrashReportClient" not in p.as_posix())
    if paks_dir:
        cdir = Path(paks_dir).expanduser()
        if not cdir.is_dir(): raise InstallError(f"--paks {cdir}: folder not found.")
        extra = sorted(p for p in cdir.rglob("*.pak") if p not in paks)
        paks += extra
    else:
        cdir = project / "Content" / "Paks"
        if not cdir.is_dir(): cdir = project                 # loose cooked files (no archives)
    return Install(root=Path(root), exe=exe, project=project, paks=paks, containers_dir=cdir)
