"""Extrai os textos literais de um arquivo JavaScript (aspas simples, duplas e crases), sem comentários.

Usado pelo teste de tradução: todo texto de interface em português precisa ter a versão em inglês no catálogo
(`web/locales/en.json`). Crases com `${...}` viram um modelo com `{}` no lugar de cada expressão.
"""
from __future__ import annotations

import re

UI_HINT = re.compile(r"[ãõçáéíóúâêôàÁÉÍÓÚÃÕÇ]|^[A-ZÁÉÍÓÚ][a-zà-ú]+(\s|$|[.,:;!?…])|\s[a-zà-ú]{3,}\s")
CODE_LIKE = re.compile(r"^[\w\-./:#\[\]=*>+~(),'\"@%\s]*$")


def literals(src: str) -> list[str]:
    out: list[str] = []
    i, n = 0, len(src)
    prev_sig = ""                                    # último caractere significativo (para distinguir regex de divisão)
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and prev_sig in "(,=:[!&|?{};+-*%<>~^" + "\n":
            j = i + 1                                # regex literal: pula até a barra de fechamento
            klass = False
            while j < n and (src[j] != "/" or klass):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    klass = True
                elif src[j] == "]":
                    klass = False
                elif src[j] == "\n":
                    break
                j += 1
            i = j + 1
            prev_sig = "a"
            continue
        if c in "'\"":
            j, buf = i + 1, []
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", "u": "\\u"}.get(nxt, nxt))
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            out.append(_unescape("".join(buf)))
            i = j + 1
            prev_sig = "a"
            continue
        if c == "`":
            j, buf = i + 1, []
            while j < n and src[j] != "`":
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j + 1])
                    j += 2
                    continue
                if src.startswith("${", j):
                    depth, k = 1, j + 2
                    while k < n and depth:
                        if src[k] == "{":
                            depth += 1
                        elif src[k] == "}":
                            depth -= 1
                        elif src[k] in "'\"`":         # texto dentro da expressão (ex.: ${a ? 'x' : 'y'})
                            q, k = src[k], k + 1
                            while k < n and src[k] != q:
                                k += 2 if src[k] == "\\" else 1
                        k += 1
                    out.extend(literals(src[j + 2:k - 1]))
                    buf.append("{}")
                    j = k
                    continue
                buf.append(src[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
            prev_sig = "a"
            continue
        if not c.isspace():
            prev_sig = c
        elif c == "\n" and prev_sig in ";{}":
            pass
        i += 1
    return out


def _unescape(s: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def ui_texts(src: str) -> list[str]:
    """Textos que parecem de interface (português), na ordem em que aparecem, sem repetir."""
    seen, out = set(), []
    for s in literals(src):
        t = s.strip()
        if len(t) < 2 or t in seen or not UI_HINT.search(t):
            continue
        if CODE_LIKE.match(t) and not re.search(r"[ãõçáéíóúâêôà]", t) and " " not in t:
            continue
        seen.add(t)
        out.append(t)
    return out


if __name__ == "__main__":
    import sys
    from pathlib import Path
    for t in ui_texts(Path(sys.argv[1]).read_text(encoding="utf-8")):
        print(repr(t))
