import sys

from .store import Store
from .app import ROOT


def main(argv: list[str]) -> int:
    if argv[:2] == ["addon", "new"]:
        from .addons import AddonError, new_addon
        if len(argv) < 3:
            print("Uso: python -m pickflow addon new <nome>")
            return 2
        try:
            dest = new_addon(Store(ROOT).addons_dir, argv[2], ROOT / "defaults" / "addon_template")
        except AddonError as e:
            print(e)
            return 1
        print(f"Addon criado em {dest}. Reinicie o PickFlow para ele aparecer em Ajustes > Addons.")
        return 0
    if argv:
        print("Comandos: (sem nada) inicia o servidor | addon new <nome>")
        return 2
    import uvicorn
    s = Store(ROOT).settings()["server"]
    uvicorn.run("pickflow.app:app", host=s["host"], port=int(s["port"]), log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
