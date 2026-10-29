"""Copia os custom nodes do PickFlow (pasta `comfyui_nodes/`) para o `custom_nodes` do seu ComfyUI.

    python -m pickflow.install_nodes                 # procura o ComfyUI e pergunta
    python -m pickflow.install_nodes "D:\\ComfyUI"    # pasta do ComfyUI (ou a custom_nodes dela)
    python -m pickflow.install_nodes --check         # só mostra o que falta, sem copiar

Só usa a biblioteca padrão. Não apaga nada; arquivos diferentes dos seus só são trocados se você confirmar.
Depois de instalar, reinicie o ComfyUI.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "comfyui_nodes"


def find_custom_nodes(arg: str | None = None) -> list[Path]:
    """Pastas custom_nodes prováveis, da mais provável para a menos."""
    cands: list[Path] = []
    if arg:
        p = Path(arg).expanduser()
        cands += [p, p / "custom_nodes"]
    cands += [ROOT.parent / "custom_nodes",                    # PickFlow dentro da pasta do ComfyUI
              Path.home() / "Documents" / "ComfyUI" / "custom_nodes",   # ComfyUI Desktop (Windows/macOS)
              Path.home() / "ComfyUI" / "custom_nodes"]
    seen, out = set(), []
    for c in cands:
        if c.name == "custom_nodes" and c.is_dir() and c.resolve() not in seen:
            seen.add(c.resolve())
            out.append(c)
    return out


def items() -> list[Path]:
    return sorted(p for p in SRC.iterdir() if p.name != "README.md" and not p.name.startswith(("__", ".")))


def same(a: Path, b: Path) -> bool:
    if a.is_file():
        return b.is_file() and filecmp.cmp(a, b, shallow=False)
    if not b.is_dir():
        return False
    cmp = filecmp.dircmp(a, b, ignore=["__pycache__"])
    return not (cmp.left_only or cmp.right_only or cmp.diff_files) and all(
        same(a / d, b / d) for d in cmp.common_dirs)


def status(dest: Path) -> dict[str, str]:
    out = {}
    for it in items():
        t = dest / it.name
        out[it.name] = "falta" if not t.exists() else ("ok" if same(it, t) else "diferente")
    return out


def install(dest: Path, overwrite: bool = False) -> dict[str, str]:
    res = status(dest)
    for it in items():
        if res[it.name] == "ok" or (res[it.name] == "diferente" and not overwrite):
            continue
        t = dest / it.name
        if it.is_dir():
            shutil.copytree(it, t, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(it, t)
        res[it.name] = "instalado"
    return res


def main(argv: list[str]) -> int:
    check = "--check" in argv
    args = [a for a in argv if not a.startswith("--")]
    found = find_custom_nodes(args[0] if args else None)
    if not found:
        print("Não achei a pasta custom_nodes do ComfyUI. Rode de novo passando a pasta do ComfyUI:\n"
              '  python -m pickflow.install_nodes "C:\\caminho\\do\\ComfyUI"')
        return 1
    dest = found[0]
    print(f"ComfyUI: {dest}")
    if len(found) > 1 and not args and sys.stdin.isatty():
        for i, f in enumerate(found, 1):
            print(f"  {i}) {f}")
        pick = input(f"Qual usar? [1] ").strip() or "1"
        dest = found[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(found) else found[0]
    for name, st in status(dest).items():
        print(f"  {name}: {st}")
    if check:
        return 0
    over = False
    if "diferente" in status(dest).values():
        over = sys.stdin.isatty() and input("Há arquivos diferentes dos do PickFlow. Substituir? [s/N] ").strip().lower() in ("s", "sim", "y")
    res = install(dest, over)
    print("Pronto:", ", ".join(f"{k} ({v})" for k, v in res.items()))
    print("Reinicie o ComfyUI para carregar os nós.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
