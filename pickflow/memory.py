"""Decide se vale descarregar o Clef antes de gerar a imagem (ou liberar o ComfyUI antes do Clef).

Só decisão: quem lê a memória (ComfyUI `/system_stats`, Clef `/health`) e quem age
(`/v1/unload`, `/free`) é o `runner`. Assim a regra é testável sem GPU.

Há duas políticas, e quem escolhe é o device em que o Clef está (`Memory.clef_device`):

- **Clef em `cuda`**: a disputa é de VRAM. Ele ocupa ~17,8 GB dos 24 GB da GPU
  em bf16, então tem que sair antes de o Qwen (16 GB) entrar. A RAM do processo entra
  depois, como segundo critério — com o Clef na GPU ela costuma ser pequena, mas se o
  `/health` informar um valor alto e a RAM estiver apertada ele também sai, porque a
  geração no ComfyUI usa bastante RAM do sistema.
- **Clef em `cpu`**: a disputa é de RAM. Na GPU ele não ocupa nada, e na RAM ele precisa
  de ~9,5 GB quantizado em int8 (~5,6 GB em int4). Descarregar libera RAM do sistema, não
  VRAM: a regra de VRAM não se aplica (nem deve disparar) e a de RAM vem primeiro.

`clef_device` fica "" quando o servidor não informou o device; nesse caso vale a regra
antiga (VRAM primeiro, RAM depois) e a política de "não consegui medir" continua de pé.
"""
from __future__ import annotations

from dataclasses import dataclass

GB = 2 ** 30


@dataclass
class Memory:
    vram_total_gb: float | None = None   # GPU inteira (ComfyUI, `vram_total`)
    vram_free_gb: float | None = None    # livre agora, com tudo que está carregado
    ram_total_gb: float | None = None
    ram_free_gb: float | None = None
    clef_loaded: bool = False
    clef_vram_gb: float = 0.0            # o que o Clef ocupa na GPU agora
    clef_ram_gb: float = 0.0             # RAM do processo do Clef (0 se não informado)
    clef_known: bool = False             # o servidor respondeu /health com `loaded`
    clef_device: str = ""                # "cpu", "cuda" ou "" = desconhecido
    clef_quant: str = ""                 # "nf4" (4 bits), "int8", "" = bf16 (o servidor informa)
    clef_expected_vram_gb: float = 0.0   # quanto ele ocupa **mesmo descarregado**: o servidor mediu ou estimou
    clef_work_vram_gb: float = 0.0       # quanto ele precisa para DECIDIR (pesos + ativações; medido: +8,5 GB)


def _device_kind(health: dict) -> str:
    """`"cuda:0"` → `"cuda"`, `"CPU"` → `"cpu"`, ausente → `""`."""
    return str(health.get("device") or "").strip().lower().split(":", 1)[0]


def clef_vram_need(m: Memory, setting_gb: float) -> float:
    """Quanto contar de VRAM para o Clef: o que ele precisa para decidir > o medido agora > o informado > o ajuste.

    O primeiro da lista é o que importa de verdade: **decidir** com imagens sobe o pico muito acima dos pesos
    (medido: 7,9 → 16,4 GB no int4; 17,8 → 25,8 GB no bf16). Reservar espaço só para os pesos é o que fazia o
    modelo vazar para a memória compartilhada e cada decisão passar de 30 s."""
    return (m.clef_work_vram_gb or m.clef_vram_gb or m.clef_expected_vram_gb or setting_gb)


def _quant_text(m: Memory) -> str:
    """`" em 4 bits"`, `" em 8 bits"` ou `""` (bf16) — só para o texto do motivo."""
    q = (m.clef_quant or "").lower()
    if q in ("nf4", "int4", "4bit"):
        return " em 4 bits"
    if q in ("int8", "8bit"):
        return " em 8 bits"
    return ""


def from_stats(stats: dict | None) -> Memory:
    """Lê a resposta de `/system_stats` do ComfyUI (bytes)."""
    m = Memory()
    if not stats:
        return m
    sysd = stats.get("system") or {}
    if sysd.get("ram_total"):
        m.ram_total_gb = sysd["ram_total"] / GB
    if sysd.get("ram_free") is not None and sysd.get("ram_total"):
        m.ram_free_gb = sysd["ram_free"] / GB
    devs = [d for d in (stats.get("devices") or []) if d.get("vram_total")]
    if devs:
        d = max(devs, key=lambda x: x["vram_total"])
        m.vram_total_gb = d["vram_total"] / GB
        if d.get("vram_free") is not None:
            m.vram_free_gb = d["vram_free"] / GB
    return m


def add_clef(m: Memory, health: dict | None) -> Memory:
    """Junta o que o servidor do Clef informa em `/health`.

    `ram_gb` é sempre aproveitado (com o Clef na GPU ele é pequeno, com o Clef na RAM
    é o número que decide) — antes só era lido quando o device começava com "cpu".
    """
    if not health or "loaded" not in health:
        return m
    m.clef_known = True
    m.clef_loaded = bool(health.get("loaded")) and not health.get("mock")
    m.clef_device = _device_kind(health)
    v = health.get("vram") or {}
    m.clef_vram_gb = float(v.get("allocated_gb") or 0.0)
    m.clef_ram_gb = float(health.get("ram_gb") or 0.0)
    m.clef_quant = str(health.get("quant") or "")
    m.clef_expected_vram_gb = float(health.get("vram_expected_gb") or 0.0)
    m.clef_work_vram_gb = float(health.get("vram_work_gb") or 0.0)
    if m.vram_total_gb is None and v.get("gpu_total_gb"):
        m.vram_total_gb = float(v["gpu_total_gb"])
    return m


def _ram_rule(m: Memory, need_ram_gb: float, margin_gb: float, in_ram: bool) -> tuple[bool, str]:
    """Regra de RAM: descarrega se o que sobra depois do Clef não comporta o workflow.

    `in_ram` diz se o Clef está na RAM (device cpu), o que muda o texto do motivo.
    """
    if m.ram_free_gb is None or m.clef_ram_gb < 2:
        return False, ""                       # sem medição confiável: não decide por RAM
    left = m.ram_free_gb - m.clef_ram_gb
    if left >= need_ram_gb + margin_gb:
        return False, ""
    if in_ram:
        return True, (f"Descarreguei o Clef: ele roda na RAM e usa {m.clef_ram_gb:.1f} GB; sobram "
                      f"{max(left, 0):.1f} GB livres e o workflow precisa de ~{need_ram_gb:.0f} GB.")
    return True, (f"Descarreguei o Clef: só {m.ram_free_gb:.1f} GB de RAM livres e o workflow precisa de "
                  f"~{need_ram_gb:.0f} GB; o Clef usa {m.clef_ram_gb:.1f} GB.")


def should_unload(policy: str, m: Memory, need_vram_gb: float, need_ram_gb: float,
                  margin_gb: float) -> tuple[bool, str]:
    """(descarregar?, motivo em português). `policy`: auto | always | never.

    Com o Clef em `cpu` a disputa é de RAM (a regra de VRAM não dispara); em `cuda` ou
    com device desconhecido vale a regra de VRAM e, depois dela, a de RAM.
    """
    if policy == "never":
        return False, ""
    if m.clef_known and not m.clef_loaded:
        return False, ""                                   # nada a descarregar
    if policy == "always":
        return True, "Clef descarregado antes de gerar (configuração: sempre)."
    # auto
    if m.clef_device != "cpu" and m.vram_total_gb is not None:
        usa = clef_vram_need(m, 0.0)                       # medido agora, ou o que o servidor informou
        room = m.vram_total_gb - usa - margin_gb
        if usa > 0 and room < need_vram_gb:
            return True, (f"Descarreguei o Clef{_quant_text(m)}: ele ocupa {usa:.1f} GB dos "
                          f"{m.vram_total_gb:.0f} GB de VRAM e sobrariam {max(room, 0):.1f} GB; o workflow "
                          f"precisa de ~{need_vram_gb:.0f} GB.")
    go, why = _ram_rule(m, need_ram_gb, margin_gb, in_ram=(m.clef_device == "cpu"))
    if go:
        return True, why
    if not m.clef_known and m.vram_total_gb is None:
        return True, "Não consegui medir a memória; descarreguei o Clef por segurança."
    if m.clef_device == "cpu":
        if m.ram_free_gb is None:
            return False, ""                               # sem a RAM do sistema não dá para afirmar nada
        if m.clef_ram_gb < 2:
            return False, ("O Clef roda na RAM, mas o servidor não informou quanto ele ocupa "
                           "(/health sem ram_gb); deixei carregado.")
        return False, (f"Memória suficiente: o Clef continua carregado na RAM "
                       f"({m.ram_free_gb:.1f} GB livres no sistema).")
    return False, ("Memória suficiente: o Clef continua carregado."
                   if m.vram_total_gb is not None else "")


def should_free_comfy(m: Memory, need_clef_vram_gb: float, margin_gb: float) -> tuple[bool, str]:
    """Antes de o Clef decidir: se ele não está carregado e falta VRAM para carregá-lo, libera o ComfyUI.

    `need_clef_vram_gb` é só a reserva: vale o que o servidor do Clef informou (`vram_expected_gb`,
    medido no último carregamento) — é o que faz o Clef em 4 bits (~6 GB) conviver com o Qwen sem
    liberar nada, enquanto o bf16 (17,8 GB) continua pedindo a placa inteira."""
    if m.clef_known and m.clef_loaded:
        return False, ""
    if m.clef_device == "cpu":
        # O Clef vai carregar na RAM: liberar a VRAM do ComfyUI não abre espaço para ele.
        return False, ""
    if m.vram_free_gb is None:
        return False, ""
    need = clef_vram_need(m, need_clef_vram_gb)
    if m.vram_free_gb < need + margin_gb:
        return True, (f"Liberei os modelos do ComfyUI: só {m.vram_free_gb:.1f} GB de VRAM livres e o Clef"
                      f"{_quant_text(m)} precisa de ~{need:.1f} GB para carregar.")
    return False, ""
