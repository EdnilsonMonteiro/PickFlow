"""Feedback do usuário sobre as decisões do Clef, e o que fazer com ele.

O Clef não aprende sozinho: ele devolve probabilidades e o `router` escolhe. Este módulo
guarda o julgamento da pessoa sobre cada execução ("ficou bom", "refazer", "a rota certa
era outra") em `data/feedback.jsonl` e transforma isso num histórico de acertos por braço
(a rota de um workflow ou um preset de prompt). Esse histórico volta para dentro das
probabilidades em `apply`, como um Beta-Binomial **semeado pelo próprio Clef**: o prior é
a probabilidade que ele deu (`alpha = p * k`) e cada acerto/erro mexe devagar nela. Assim
uma decisão confiante e sem histórico não é derrubada por dois cliques errados, e um braço
que erra sempre afunda sozinho. Não é aprendizado de verdade (não mexe em pesos): é
memória de uso, e é reversível apagando o arquivo.

Regras de convivência com o resto do app: NADA de I/O na importação (só a classe recebe o
`data_dir`), nenhuma exceção sai daqui por arquivo corrompido ou disco cheio — o feedback
nunca pode derrubar uma execução —, e nenhuma dependência nova (só a biblioteca padrão;
as contas são escalares porque há poucos braços, `numpy` não faz falta).
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

from .clef import _STOP, _tokens

#: O que a pessoa (ou o app) quis dizer sobre uma execução. `download`/`redo`/`same` são
#: sinais implícitos tirados do uso; `up`/`down`/`other` são o clique explícito.
VERDICTS = ("up", "down", "other", "preset", "download", "redo", "same")
#: De onde veio o registro: `user` (clique) ou um dos sinais implícitos.
SOURCES = ("user", "download", "redo", "same")
IMPLICIT = ("download", "redo", "same")
#: Veredito que ainda não conta como acerto nem erro (o pedido só se repetiu).
NEUTRAL = "same"
#: Quem escolheu a rota desta execução (`clef` = o modelo, os outros não ensinam a rota:
#: uma rota que a pessoa forçou não diz nada sobre o acerto do Clef).
ORIGINS = ("clef", "user", "history", "agent")

#: Memória do `similar`: além disso o histórico antigo não ajuda e só custa tempo.
MAX_ENTRIES = 2000
#: Pedido gigante (texto colado) é cortado: o começo é o que descreve o pedido.
MAX_WORDS = 400
#: Quanto vale um par de palavras em comum, na mesma ordem, em `similar`.
BIGRAM_BONUS = 0.15

_WORD = re.compile(r"[a-z0-9]{3,}|\d+")


# ------------------------------------------------------------------ utilidades
def _text(value) -> str:
    """Texto seguro (None vira ""): tudo o que entra no jsonl é string simples."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _num(value, default: float = 0.0) -> float:
    """Número seguro: campo faltando, texto ou lixo vira o padrão."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _verdict_of(verdict) -> str:
    """Normaliza o veredito. Desconhecido vira `same` (neutro) em vez de virar acerto."""
    v = _text(verdict).strip().lower()
    return v if v in VERDICTS else NEUTRAL


def _source_of(source, verdict) -> str:
    """Canal do registro; um veredito implícito já carrega o canal junto.

    Quem chama `add(verdict="download")` sem passar `source` quer obviamente
    `source="download"`: manter "user" ali faria o sinal implícito passar por opinião
    explícita e vencer as outras entradas da mesma execução.
    """
    s = _text(source).strip().lower()
    v = _text(verdict).strip().lower()
    if s in SOURCES and not (s == "user" and v in IMPLICIT):
        return s
    if v in IMPLICIT:
        return v
    return s if s in SOURCES else "user"


def _origin_of(origin) -> str:
    """Quem escolheu a rota: `clef` (padrão), `user`, `history` ou `agent`."""
    o = _text(origin).strip().lower()
    return o if o in ORIGINS else "clef"


def _resolve(verdict: str) -> tuple[str, bool]:
    """Veredito cru -> `(up|down|other|preset|same, weak)`.

    `download` é sinal fraco (a pessoa baixou a imagem, então provavelmente gostou, mas
    não afirmou nada); `redo` é um "não gostei" silencioso; `same` não diz nada.
    `preset` é o caso "o workflow estava certo, o prompt não": confirma a rota e marca o
    prompt — sem ele, o erro de prompt sujaria o histórico da rota (e vice-versa).
    """
    if verdict == "download":
        return "up", True
    if verdict == "redo":
        return "down", False
    if verdict in ("up", "down", "other", "preset"):
        return verdict, False
    return NEUTRAL, False


def _normalize(probs: dict[str, float]) -> dict[str, float]:
    """Devolve cópia somando 1 (probabilidade zerada/negativa vira 0; tudo zerado vira uniforme)."""
    base = {str(o): max(0.0, _num(p, 0.0)) for o, p in (probs or {}).items()}
    total = sum(base.values())
    if not base:
        return {}
    if total <= 0.0:
        n = float(len(base))
        return {o: 1.0 / n for o in base}
    return {o: p / total for o, p in base.items()}


def _words(text: str) -> list[str]:
    """Palavras do pedido na ordem em que aparecem, com a normalização de `clef._tokens`.

    `_tokens` devolve um conjunto (serve para a interseção), mas o bônus de bigrama precisa
    da ordem — daí esta versão em lista, com as mesmas regras: sem acento, 3+ letras,
    sem as palavras vazias de `clef._STOP`.
    """
    t = unicodedata.normalize("NFKD", _text(text).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in _WORD.findall(t) if w not in _STOP][:MAX_WORDS]


def _bigrams(words: list[str]) -> set[tuple[str, str]]:
    """Pares de palavras vizinhas ("por", "praia" saem; o que fica é a expressão)."""
    return {(words[i], words[i + 1]) for i in range(len(words) - 1)}


def similarity(older: str, newer: str) -> float:
    """Quanto o pedido novo (`newer`) repete o antigo (`older`), de 0 a 1.

    Mesma conta de `FeedbackStore.similar`, exposta para reconhecer um pedido refeito sem passar pelo histórico
    (o runner usa isto para o feedback implícito: "melhore a foto" e "dá uma melhorada nesta foto" pedem a mesma
    coisa, e refazer com a mesma ideia é sinal de que a primeira geração não serviu).
    """
    wo, wn = _words(older), _words(newer)
    to, tn = _tokens(" ".join(wo)), _tokens(" ".join(wn))
    if not to or not tn:
        return 0.0
    score = len(tn & to) / max(1, len(tn))
    return min(1.0, score + BIGRAM_BONUS * len(_bigrams(wn) & _bigrams(wo)))


def _branch_counts(hist: dict[str, tuple[int, int]], prefix: str, opt: str) -> tuple[int, int]:
    """(acertos, erros) do braço de uma opção; `prefix` casa com ids tipo "edit/livre"."""
    for name in ((prefix + opt,) if prefix else ()) + (opt,):
        if name and hist.get(name):
            return hist[name]
    return (0, 0)


# ------------------------------------------------------------------ a classe
class FeedbackStore:
    """Histórico de feedback em `data/feedback.jsonl` (uma linha JSON por evento).

    Recebe o `data_dir` em vez de descobri-lo: o módulo não conhece o app, e os testes usam
    `tmp_path`. O arquivo é append-only (nada de reescrever o histórico: uma linha perdida
    não invalida as outras) e é lido com cache pela assinatura do arquivo, do mesmo jeito
    que `Store.manifests` faz com `_mf_sig` — a tela de Ajustes e cada decisão chamam isto
    muitas vezes por minuto.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "feedback.jsonl"
        #: Cache derivado (resumo + contagens) para quem quiser ler sem recalcular; o
        #: jsonl continua sendo a única fonte de verdade.
        self.state_path = self.data_dir / "feedback_state.json"
        self._sig: tuple[int, int] | None = None
        self._rows: list[dict] | None = None

    # ------------------------------------------------------------- gravar
    def add(self, conv, run, request, route, preset="", confidence=0.0, verdict="up",
            picked="", source="user", origin="clef") -> dict:
        """Grava um feedback e devolve a entrada normalizada.

        Não levanta exceção de propósito: se o disco estiver cheio ou a pasta não existir,
        o pior resultado aceitável é perder o registro — nunca a execução que o gerou.
        `picked` só faz sentido em `other` (a resposta certa era outra rota) e em `preset`
        (o prompt certo era outro), então nos outros vereditos ele é descartado para o
        arquivo não guardar lixo contraditório. `origin` diz quem escolheu a rota daquela
        execução: uma rota forçada pela pessoa não ensina nada sobre o acerto do Clef.
        """
        entry = {
            "ts": time.time(),
            "conv": _text(conv),
            "run": _text(run),
            "request": _text(request),
            "route": _text(route),
            "preset": _text(preset),
            "confidence": _clamp01(_num(confidence, 0.0)),
            "verdict": _verdict_of(verdict),
            "picked": "",
            "source": _source_of(source, verdict),
            "origin": _origin_of(origin),
        }
        if entry["verdict"] in ("other", "preset"):
            entry["picked"] = _text(picked)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — feedback nunca derruba a execução
            pass
        self._sig, self._rows = None, None      # força releitura na próxima consulta
        return entry

    # -------------------------------------------------------------- ler
    def entries(self) -> list[dict]:
        """Todas as entradas em ordem de gravação; linha inválida é ignorada.

        Uma linha pela metade (app morto no meio do append) ou um JSON editado à mão com
        erro não podem esconder o resto do histórico. O cache vale enquanto mudarem nem o
        tamanho nem o mtime do arquivo.
        """
        sig = self._signature()
        if sig != self._sig or self._rows is None:
            self._sig = sig
            self._rows = self._read_lines() if sig is not None else []
        return [dict(r) for r in (self._rows or [])]

    def _signature(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _read_lines(self) -> list[dict]:
        rows: list[dict] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(row, dict):
                        rows.append(self._clean(row))
        except OSError:
            return []
        return rows

    @staticmethod
    def _clean(row: dict) -> dict:
        """Mesma forma que `add` grava, mesmo para linha antiga ou editada à mão."""
        verdict = _verdict_of(row.get("verdict") if row.get("verdict") is not None else row.get("source"))
        entry = {
            "ts": _num(row.get("ts"), 0.0),
            "conv": _text(row.get("conv")),
            "run": _text(row.get("run")),
            "request": _text(row.get("request")),
            "route": _text(row.get("route")),
            "preset": _text(row.get("preset")),
            "confidence": _clamp01(_num(row.get("confidence"), 0.0)),
            "verdict": verdict,
            "picked": _text(row.get("picked")) if verdict in ("other", "preset") else "",
            "source": _source_of(row.get("source"), verdict),
            "origin": _origin_of(row.get("origin")),
        }
        return entry

    # ------------------------------------------------- a regra de ouro
    def effective(self) -> list[dict]:
        """Uma entrada por execução (`run`), com o veredito já resolvido.

        A mesma execução pode receber vários sinais (a pessoa baixa a imagem e depois clica
        "não gostei", ou corrige a rota). A prioridade é: a ÚLTIMA opinião explícita do
        usuário vence (ele pode se corrigir); sem opinião explícita, `download` conta como
        acerto fraco; senão `redo` conta como erro. `same` sozinho não entra: repetir o
        pedido não diz se ficou bom, e inventar um voto ali envenenaria as contagens.
        """
        groups: dict[str, list[dict]] = {}
        for i, e in enumerate(self.entries()):
            groups.setdefault(e["run"] or f"#linha{i}", []).append(e)

        out: list[dict] = []
        for rows in groups.values():
            users = [e for e in rows if e["source"] == "user"]
            if users:
                item = dict(users[-1])
                verdict, weak = _resolve(item["verdict"])
            else:
                weak = True
                if any(e["source"] == "download" for e in rows):
                    item = dict([e for e in rows if e["source"] == "download"][-1])
                    verdict = "up"
                elif any(e["source"] == "redo" for e in rows):
                    item = dict([e for e in rows if e["source"] == "redo"][-1])
                    verdict, weak = "down", False
                else:
                    continue                    # só `same`: sem informação
            if verdict == NEUTRAL:
                continue
            item["verdict"] = verdict
            item["weak"] = weak
            out.append(item)
        return out

    def counts(self, kind: str = "route") -> dict[str, tuple[int, int]]:
        """Braço -> (acertos, erros) para montar o prior de `apply`.

        A regra é "o que este veredito diz sobre esta decisão", e não o mesmo sinal para
        rota e prompt:

        - `up` ("era isso"): acerto da rota **e** do prompt (os dois estavam certos).
        - `down` ("não era isso"): erro da rota; do prompt, nada — a pessoa não disse que
          o prompt era o problema, e jogar a culpa nos dois é o que sujava o histórico.
        - `other` ("era outro workflow"): erro do braço escolhido e acerto do braço que a
          pessoa apontou (`picked`); do prompt, nada (a rota estava errada, então o prompt
          dela não diz nada).
        - `preset` ("o workflow estava certo, o prompt não"): acerto da rota, erro do
          prompt escolhido e acerto do prompt apontado (`picked`).

        Rota forçada pela própria pessoa (`origin="user"`) não ensina a rota: o Clef não
        escolheu aquilo. Braço vazio nunca entra — não há o que corrigir.
        """
        route_kind = kind != "preset"
        key = "preset" if not route_kind else "route"
        out: dict[str, tuple[int, int]] = {}

        def bump(branch: str, ok: bool):
            branch = _text(branch)
            if not branch:
                return
            up, down = out.get(branch, (0, 0))
            out[branch] = (up + 1, down) if ok else (up, down + 1)

        for e in self.effective():
            verdict = e["verdict"]
            own_route = e.get("origin") != "user"          # a rota veio do Clef (ou do histórico)
            if route_kind:
                if verdict == "up":
                    bump(e.get("route", ""), True)
                elif verdict == "down":
                    if own_route:
                        bump(e.get("route", ""), False)
                elif verdict == "other":
                    if own_route:
                        bump(e.get("route", ""), False)
                    bump(e.get("picked", ""), True)
                elif verdict == "preset":
                    if own_route:
                        bump(e.get("route", ""), True)
            else:
                if verdict == "up":
                    bump(e.get("preset", ""), True)
                elif verdict == "preset":
                    bump(e.get("preset", ""), False)
                    bump(e.get("picked", ""), True)
        return out

    # ------------------------------------------------- corrigir o Clef
    def apply(self, probs: dict[str, float], kind: str = "route", weight: float = 0.3,
              k: float = 5.0, prefix: str = "") -> dict[str, float]:
        """Empurra as probabilidades do Clef na direção do histórico, e renormaliza.

        Beta-Binomial com prior semeado pelo Clef: `alpha = p*k + acertos` e
        `beta = (1-p)*k + erros`. O prior TEM que ser a própria probabilidade do Clef (e não
        0,5, nem a proporção global de acertos): um braço nunca usado deve manter exatamente
        a opinião do modelo, e um braço com o mesmo número de acertos e erros também. `k`
        é a "força" desse prior, em quantas observações ele vale: com `k=5` são precisos
        uns 5 erros para derrubar uma opção em que o Clef acreditava.
        `weight` é o quanto do resultado histórico entra na mistura — 0 desliga o
        aprendizado sem mexer em quem chama. `prefix` casa o id da opção ("livre") com o
        braço gravado ("edit/livre").
        """
        base = _normalize(probs)
        weight = _clamp01(_num(weight, 0.0))
        kk = max(1e-6, _num(k, 5.0))
        if not base or weight <= 0:
            return base                          # desligado: cópia normalizada, sem histórico
        hist = self.counts(kind)

        out: dict[str, float] = {}
        touched = False
        for opt, p in base.items():
            up, down = _branch_counts(hist, prefix, opt)
            if not (up or down):
                out[opt] = p                    # braço sem histórico: fica com o Clef
                continue
            touched = True
            alpha = p * kk + up
            beta = (1.0 - p) * kk + down
            p_hist = alpha / (alpha + beta)
            out[opt] = (1.0 - weight) * p + weight * p_hist
        return _normalize(out) if touched else base

    @staticmethod
    def delta(probs_antes: dict[str, float], probs_depois: dict[str, float]) -> float:
        """Distância L1 entre as probabilidades (para logar "isto mudou a decisão")."""
        antes, depois = probs_antes or {}, probs_depois or {}
        keys = set(antes) | set(depois)
        return sum(abs(_num(depois.get(o), 0.0) - _num(antes.get(o), 0.0)) for o in keys)

    # ------------------------------------------------- memória de pedidos
    def similar(self, request: str, k: int = 5, verdicts: tuple[str, ...] = ("up",),
                min_score: float = 0.55) -> list[dict]:
        """Pedidos parecidos com este que deram certo (ou errado, se `verdicts` mudar).

        Serve para mostrar ao Clef "pedidos assim antes deram nesta rota" e para a pessoa
        reconhecer um pedido repetido. A pontuação não tem modelo nenhum atrás (o Clef não
        está disponível neste ponto): é a fração das palavras do pedido novo que aparecem no
        antigo, mais um bônus por expressão repetida na mesma ordem — "na praia" vale mais
        que "praia" e "ele" sozinhos. O denominador é o pedido NOVO, para um pedido antigo
        enorme não ganhar nota por ter todas as palavras.
        """
        wanted = _tokens(" ".join(_words(request)))
        if not wanted:
            return []
        allow = set(verdicts or ())
        found: list[tuple[float, int, dict]] = []
        for i, e in enumerate(self.effective()[-MAX_ENTRIES:]):
            if e["verdict"] not in allow:
                continue
            old = e["request"].strip()
            if not old:
                continue
            score = similarity(old, request)
            if score < min_score:
                continue
            found.append((score, i, {"request": old, "route": e["route"],
                                     "picked": e.get("picked") or "", "verdict": e["verdict"],
                                     "preset": e["preset"], "score": score}))
        found.sort(key=lambda t: (-t[0], -t[1]))      # nota, depois o mais recente
        return [item for _, _, item in found[:max(0, int(k))]]

    def examples(self, request: str, k: int = 3, min_score: float = 0.55,
                 max_per_target: int = 1) -> list[dict]:
        """Casos parecidos já CONSOLIDADOS: uma linha por lição, com quantas vezes ela apareceu.

        É a versão "memória fundida" do `similar`. O problema que ela resolve: pedidos que
        apontam para o MESMO fluxo viravam três linhas iguais no estado do Clef — as três
        vagas gastas repetindo a mesma coisa, e um caso novo (o quarto) nunca entrava porque
        empatava com os antigos. Aqui:

        - os casos são agrupados pelo destino (a rota aprovada, ou a que a pessoa apontou em
          "era outro workflow"), e o grupo guarda a contagem — é a evidência, não o texto;
        - texto repetido dentro do grupo conta uma vez só;
        - a distribuição é em rodadas: primeiro uma linha de cada destino (o mais parecido
          primeiro); sobrando vaga, entra uma segunda amostra do mesmo destino (texto
          diferente), até `max_per_target` por destino. Assim as vagas nunca ficam gastas
          em repetição quando há lições diferentes para mostrar;
        - um caso novo só entra se for mais parecido que o pior que está nas vagas.

        Não é resumo por LLM: é agrupamento por destino, que é exatamente o que a fusão
        precisa preservar ("pedidos assim vão para este fluxo") sem inventar texto.
        """
        want = max(0, int(k))
        if not want:
            return []
        pool = self.similar(request, k=max(want * 4, want), verdicts=("up", "other"), min_score=min_score)
        groups: dict[str, dict] = {}
        for item in pool:                          # `pool` já vem do mais parecido para o menos
            target = _text(item.get("picked")) or _text(item.get("route"))
            if not target:
                continue
            g = groups.setdefault(target, {"target": target, "count": 0, "score": item["score"],
                                           "verdict": item["verdict"], "texts": []})
            g["count"] += 1
            if item["verdict"] == "other":
                g["verdict"] = "other"             # o caso corrigido é o mais informativo do grupo
            seen = {_text(t["request"]).strip().lower() for t in g["texts"]}
            if _text(item["request"]).strip().lower() not in seen:
                g["texts"].append({"request": item["request"], "score": item["score"]})
        ordered = sorted(groups.values(), key=lambda g: (-g["score"], -g["count"]))
        out: list[dict] = []
        limit = max(1, int(max_per_target or 1))
        for round_ in range(limit):                # 1ª rodada: um caso de cada destino; depois, amostras extras
            for g in ordered:
                if len(out) >= want:
                    return out
                if round_ < len(g["texts"]):
                    out.append({"request": g["texts"][round_]["request"], "target": g["target"],
                                "count": g["count"], "score": g["texts"][round_]["score"],
                                "verdict": g["verdict"]})
        return out[:want]

    # ----------------------------------------------------------- resumo
    def summary(self) -> dict:
        """Números para a tela de Ajustes e para julgar se o aprendizado está andando.

        `accuracy` usa só o que foi julgado (acertos + erros + "era outra" + "o prompt errado"):
        contar `same` como acerto maquiava o número, e um histórico sem nenhum julgamento
        dividiria por zero. `weak` separa o que veio de download para não confundir sinal
        fraco com opinião explícita.
        """
        rows = self.effective()
        up = sum(1 for e in rows if e["verdict"] == "up")
        down = sum(1 for e in rows if e["verdict"] == "down")
        other = sum(1 for e in rows if e["verdict"] == "other")
        # "o workflow estava certo, o prompt não": a ROTA acertou, então entra no numerador
        preset = sum(1 for e in rows if e["verdict"] == "preset")
        total = len(rows)
        judged = up + down + other + preset
        return {
            "total": total,
            "up": up,
            "down": down,
            "other": other,
            "preset": preset,
            "unknown": total - judged,
            "weak": sum(1 for e in rows if e.get("weak")),
            "by_route": {r: {"up": u, "down": d} for r, (u, d) in self.counts("route").items()},
            "accuracy": ((up + preset) / judged) if judged else 0.0,
        }

    def write_state(self, extra: dict | None = None) -> dict:
        """Grava `feedback_state.json` com o resumo pronto (cache para a interface).

        Opcional: o jsonl é a fonte de verdade e nada aqui o lê de volta. Também não
        levanta exceção — é só uma conveniência para a tela não recalcular a cada abertura.
        """
        state = {
            "ts": time.time(),
            "summary": self.summary(),
            "counts": {branch: list(v) for branch, v in self.counts("route").items()},
            "presets": {branch: list(v) for branch, v in self.counts("preset").items()},
        }
        if isinstance(extra, dict):
            state.update(extra)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
        except Exception:  # noqa: BLE001 — cache não pode derrubar nada
            pass
        return state
