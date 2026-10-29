"""Testes do histórico de feedback do Clef (`pickflow.feedback`).

Tudo em `tmp_path`: o módulo não conhece o app, só recebe o `data_dir`. O que se cobra
aqui é o que o resto do app vai depender: a leitura não cai com arquivo estragado, a
regra do `effective` (última opinião vence) e as contas do `apply` (o prior é o Clef).
"""
from __future__ import annotations

import json

import pytest

from pickflow.feedback import FeedbackStore


# ----------------------------------------------------------- gravar e ler
def test_add_grava_uma_linha_e_entries_devolve_igual(tmp_path):
    fs = FeedbackStore(tmp_path)
    e = fs.add("abc123", "deadbeef", "põe ele na praia", "troca_roupa",
               preset="edit/livre", confidence=0.66, verdict="up")
    assert e["conv"] == "abc123" and e["run"] == "deadbeef"
    assert e["verdict"] == "up" and e["source"] == "user"
    assert e["confidence"] == pytest.approx(0.66)
    assert e["picked"] == ""
    linhas = fs.path.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    assert json.loads(linhas[0])["request"] == "põe ele na praia"
    assert fs.entries() == [e]


def test_arquivo_corrompido_no_meio_nao_derruba(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "run1", "pedido", "rota_a")
    with fs.path.open("a", encoding="utf-8") as f:
        f.write('{"ts": 1.0, "run": "run2", "route": "rota_b", "verdict": "up"}\n')
        f.write("{isso nao e json\n")
        f.write('["uma", "lista", "qualquer"]\n')
        f.write("\n")
    fs.add("c", "run3", "outro pedido", "rota_c")
    assert [r["run"] for r in fs.entries()] == ["run1", "run2", "run3"]
    assert fs.entries()[1]["route"] == "rota_b"


def test_add_nao_levanta_quando_nao_da_para_gravar(tmp_path):
    bloqueado = tmp_path / "bloqueado"
    bloqueado.write_text("não sou uma pasta", encoding="utf-8")
    fs = FeedbackStore(bloqueado)                 # mkdir vai falhar lá dentro
    e = fs.add("c", "r1", "p", "rota_a")
    assert e["run"] == "r1" and e["verdict"] == "up"   # devolve a entrada e segue
    assert fs.entries() == []


# ------------------------------------------------------- a regra do effective
def test_effective_ultima_opiniao_do_usuario_vence(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="down")
    fs.add("c", "r1", "p", "rota_a", verdict="up")       # a pessoa se corrigiu
    assert [(e["run"], e["verdict"], e["weak"]) for e in fs.effective()] == [("r1", "up", False)]


def test_effective_download_vira_acerto_fraco(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="download")
    [e] = fs.effective()
    assert e["verdict"] == "up" and e["weak"] is True


def test_effective_redo_vira_erro(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="redo")
    [e] = fs.effective()
    assert e["verdict"] == "down" and e["weak"] is False


def test_effective_same_sozinho_nao_conta(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="same")
    assert fs.effective() == []
    assert fs.counts() == {}


def test_effective_explicito_vence_o_download_da_mesma_execucao(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="download")
    fs.add("c", "r1", "p", "rota_a", verdict="down")
    [e] = fs.effective()
    assert e["verdict"] == "down" and e["weak"] is False


# ------------------------------------------------------------------ counts
def test_counts_other_pune_o_original_e_premia_o_escolhido(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="other", picked="rota_b")
    fs.add("c", "r2", "p", "rota_b", verdict="up")
    c = fs.counts()
    assert c["rota_a"] == (0, 1)          # errou e não ganhou acerto
    assert c["rota_b"] == (2, 0)          # acertou como escolhido e como decisão própria


def test_counts_por_preset(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "edit", preset="edit/livre", verdict="up")
    # "não era isso" NÃO mexe no prompt: a pessoa não disse que o prompt era o problema
    fs.add("c", "r2", "p", "edit", preset="edit/outro", verdict="down")
    assert fs.counts("preset") == {"edit/livre": (1, 0)}
    assert fs.counts() == {"edit": (1, 1)}
    # "o workflow estava certo, o prompt não": confirma a rota, derruba o prompt e sobe o apontado
    fs.add("c", "r3", "p", "edit", preset="edit/outro", verdict="preset", picked="edit/melhorar")
    assert fs.counts("preset") == {"edit/livre": (1, 0), "edit/outro": (0, 1), "edit/melhorar": (1, 0)}
    assert fs.counts() == {"edit": (2, 1)}


def test_counts_ignora_rota_que_a_pessoa_forcou(tmp_path):
    """Rota escolhida na mão (origin=user) não ensina o Clef: ele não escolheu aquilo."""
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "edit", verdict="down", origin="user")
    fs.add("c", "r2", "p", "troca_roupa", verdict="down", origin="clef")
    assert fs.counts() == {"troca_roupa": (0, 1)}
    # o prompt continua valendo (o Clef escolhe o prompt mesmo quando a rota foi forçada)
    fs.add("c", "r3", "p", "edit", preset="edit/livre", verdict="up", origin="user")
    assert fs.counts("preset") == {"edit/livre": (1, 0)}


# ------------------------------------------------------------------- apply
def test_apply_derruba_braco_com_erros(tmp_path):
    fs = FeedbackStore(tmp_path)
    for i in range(5):
        fs.add("c", f"r{i}", "p", "ruim", verdict="down")
    probs = {"ruim": 0.6, "bom": 0.4}
    out = fs.apply(probs, weight=1.0)
    assert out["ruim"] < probs["ruim"] and out["bom"] > probs["bom"]
    assert sum(out.values()) == pytest.approx(1.0)
    assert probs == {"ruim": 0.6, "bom": 0.4}        # não muta a entrada


def test_apply_sobe_braco_com_acertos(tmp_path):
    fs = FeedbackStore(tmp_path)
    for i in range(3):
        fs.add("c", f"r{i}", "p", "bom", verdict="up")
    out = fs.apply({"bom": 0.3, "outro": 0.7}, weight=1.0)
    assert out["bom"] > 0.3 and out["outro"] < 0.7
    assert sum(out.values()) == pytest.approx(1.0)


def test_apply_sem_historico_do_braco_nao_muda_nada(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "outro_braco", verdict="down")
    assert fs.apply({"a": 0.75, "b": 0.25}) == {"a": 0.75, "b": 0.25}
    assert fs.apply({}) == {}


def test_apply_com_prefix_de_preset(tmp_path):
    fs = FeedbackStore(tmp_path)
    for i in range(4):
        fs.add("c", f"r{i}", "p", "edit", preset="edit/livre", verdict="preset", picked="edit/melhorar")
    out = fs.apply({"livre": 0.8, "melhorar": 0.2}, kind="preset", prefix="edit/", weight=1.0)
    assert out["livre"] < 0.8 and out["melhorar"] > 0.2
    assert sum(out.values()) == pytest.approx(1.0)


# ----------------------------------------------------------------- similar
def test_similar_acha_o_mesmo_pedido_e_ignora_outro_assunto(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "põe ele na praia", "troca_roupa", preset="edit/livre", verdict="up")
    fs.add("c", "r2", "remove o fundo da foto", "background_removal", verdict="up")
    achados = fs.similar("põe ele na praia com um chapéu")
    assert [a["route"] for a in achados] == ["troca_roupa"]
    assert achados[0]["score"] > 0.55 and achados[0]["preset"] == "edit/livre"
    assert {a["route"] for a in fs.similar("remove o fundo da foto")} == {"background_removal"}


def test_similar_respeita_verdicts_k_e_min_score(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "põe ele na praia", "rota_a", verdict="up")
    fs.add("c", "r2", "põe ele na praia de novo", "rota_b", verdict="up")
    fs.add("c", "r3", "põe ele na praia", "rota_c", verdict="down")
    todos = fs.similar("põe ele na praia")
    assert len(todos) == 2                              # `down` fica de fora
    assert [t["route"] for t in todos] == ["rota_b", "rota_a"]   # empate: mais recente primeiro
    assert len(fs.similar("põe ele na praia", k=1)) == 1
    assert fs.similar("põe ele na praia", min_score=1.01) == []
    assert [t["route"] for t in fs.similar("põe ele na praia", verdicts=("down",))] == ["rota_c"]
    assert fs.similar("") == []


# ----------------------------------------------------------------- resumo
def test_summary_e_accuracy(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "p", "rota_a", verdict="up")
    fs.add("c", "r2", "p", "rota_a", verdict="up")
    fs.add("c", "r3", "p", "rota_a", verdict="down")
    fs.add("c", "r4", "p", "rota_b", verdict="down")
    fs.add("c", "r5", "p", "rota_b", verdict="download")
    fs.add("c", "r6", "p", "rota_c", verdict="same")     # não entra em nada
    s = fs.summary()
    assert s["total"] == 5 and s["up"] == 3 and s["down"] == 2 and s["other"] == 0
    assert s["unknown"] == 0 and s["weak"] == 1
    assert s["accuracy"] == pytest.approx(3 / 5)
    assert s["by_route"] == {"rota_a": {"up": 2, "down": 1}, "rota_b": {"up": 1, "down": 1}}


def test_similar_devolve_o_apontado_em_other(tmp_path):
    """O caso "era outro workflow" é o mais útil como exemplo, então tem que sair do similar."""
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "põe o cachorro na praia", "edit", verdict="other", picked="recipe__colar_e_fundir")
    achados = fs.similar("põe o cachorro na praia ao pôr do sol", verdicts=("up", "other"))
    assert achados and achados[0]["picked"] == "recipe__colar_e_fundir" and achados[0]["verdict"] == "other"
    assert fs.similar("põe o cachorro na praia ao pôr do sol") == []      # por padrão só os aprovados


# ------------------------------------------------- memória fundida (exemplos)
def test_examples_funde_o_mesmo_destino_numa_linha_com_contagem(tmp_path):
    """Três repetições do mesmo pedido viram UMA linha com a contagem — não três vagas gastas."""
    fs = FeedbackStore(tmp_path)
    for i in range(3):
        fs.add("c", f"r{i}", "põe o cachorro na praia", "recipe__colar_e_fundir", "", 0.6, "up", "", "user")
    got = fs.examples("põe o cachorro na praia ao pôr do sol", k=3)
    assert len(got) == 1
    assert got[0]["target"] == "recipe__colar_e_fundir" and got[0]["count"] == 3


def test_examples_mostra_lixoes_diferentes_antes_de_repetir_o_mesmo(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "põe o cachorro na praia", "recipe__colar_e_fundir", "", 0.6, "up", "", "user")
    fs.add("c", "r2", "pôr o cachorro na praia", "transfer_object", "", 0.6, "up", "", "user")
    fs.add("c", "r3", "o cachorro na praia", "edit", "", 0.6, "up", "", "user")
    got = fs.examples("põe o cachorro na praia ao pôr do sol", k=3)
    # três lições diferentes (a ordem entre notas iguais é livre): o mais parecido primeiro
    assert got[0]["target"] == "recipe__colar_e_fundir"
    assert {g["target"] for g in got} == {"recipe__colar_e_fundir", "transfer_object", "edit"}
    assert all(g["count"] == 1 for g in got)
    # com duas amostras por destino, a segunda rodada só usa as vagas que sobraram
    got2 = fs.examples("põe o cachorro na praia ao pôr do sol", k=3, max_per_target=2)
    assert len(got2) == 3


def test_examples_um_caso_novo_entra_se_for_mais_parecido(tmp_path):
    """A pergunta do "quarto caso": não fica congelado — o melhor parecido ocupa a vaga."""
    fs = FeedbackStore(tmp_path)
    fs.add("c", "r1", "pôr o cachorro na praia", "transfer_object", "", 0.6, "up", "", "user")
    fs.add("c", "r2", "o cachorro na praia", "edit", "", 0.6, "up", "", "user")
    fs.add("c", "r3", "cachorro na praia", "background_removal", "", 0.6, "up", "", "user")
    antes = fs.examples("põe o cachorro na praia", k=2)
    assert len(antes) == 2
    # um quarto caso, com o texto exato do pedido (nota máxima), entra e derruba o menos parecido
    fs.add("c", "r4", "põe o cachorro na praia", "recipe__colar_e_fundir", "", 0.6, "up", "", "user")
    depois = fs.examples("põe o cachorro na praia", k=2)
    assert depois[0]["target"] == "recipe__colar_e_fundir"
    assert len(depois) == 2 and "recipe__colar_e_fundir" in [g["target"] for g in depois]


