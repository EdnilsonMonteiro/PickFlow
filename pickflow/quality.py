"""Medição barata de uma foto que chega ao chat: tamanho e nitidez, o que o Clef precisa para decidir entre
RESTAURAR (redesenhar em ~2 MP, bom para foto pequena ou sem detalhe) e só MELHORAR (manter o tamanho, bom para
foto grande e nítida).

Como mede: abre a imagem, guarda `width`/`height` do arquivo original, reduz para o lado maior = `SIDE` (768 px) com
`Image.LANCZOS`, passa para cinza, aplica `FIND_EDGES` e devolve a VARIÂNCIA do mapa de bordas. É a ideia da
"variância do Laplaciano" (quanto de detalhe fino sobrou) feita só com PIL, sem numpy: foto desfocada tem bordas
fracas, logo variância baixa; foto nítida tem bordas fortes e variância alta. Medir sempre no mesmo tamanho é o que
torna o número comparável entre fotos de tamanhos diferentes.

DE ONDE VIERAM OS LIMIARES (`SOFT`, `CRISP`): medidos com este mesmo código em 241 fotos reais do projeto
(`data/images/`: 199 PNG, 32 JPEG, 10 WEBP), mínimo 126, p10 358, p25 718, mediana 1123, p75 2150, p90 3263,
máximo 6968.
  * `SOFT = 350` (~ p10): 9% das fotos reais ficam abaixo. Cópias propositalmente desfocadas dessas mesmas fotos
    (GaussianBlur de sigma ~2,3 px no tamanho de medição) caem abaixo de 350 em 98% dos casos — a mediana delas é
    117 e o p90 é 180. Ou seja: quem é de verdade desfocado quase sempre entra aqui.
  * `CRISP = 1100` (~ mediana, e a média geométrica entre `SOFT` e o p90 das fotos): metade das fotos reais passa.
    A versão desfocada da mesma foto fica ~10x abaixo (mediana 117), então há folga grande dos dois lados.

O número NÃO mede só foco: ele depende do conteúdo (foto de fundo liso, mesmo nítida, tem pouca borda) e o tamanho
de medição é fixo (foto menor que 768 px não é ampliada, pois `thumbnail` nunca aumenta, e nela o detalhe de 1 pixel
pesa mais). Por isso os limites são generosos e o Clef recebe `megapixels` e `sharpness` SEPARADOS: ele combina as
duas coisas em vez de confiar num único número.

Nada aqui pode levantar exceção: isto roda no caminho de toda mensagem, então qualquer erro (arquivo que não é
imagem, imagem degenerada, arquivo cortado) vira `Pic` vazio.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

SMALL_MP = 1.0      # abaixo disto a foto é "pequena": redesenhar em 2 MP acrescenta bastante (ver docstring acima)
BIG_MP = 2.0        # daqui para cima ela já tem tamanho de sobra: melhorar mantendo o tamanho
SOFT = 350.0        # nitidez abaixo disto = fora de foco (p10 das 241 fotos reais; 98% das desfocadas caem aqui)
CRISP = 1100.0      # nitidez daqui para cima = detalhe firme (mediana das 241 fotos reais)
SIDE = 768          # lado maior em que TODA foto é medida (é o que torna a nitidez comparável entre fotos)
MIN_SIDE = 3        # abaixo disto o filtro de bordas não tem o que medir: imagem degenerada, tratada como vazia


@dataclass(frozen=True)
class Pic:
    """O que se sabe de uma foto: tamanho em pixels, megapixels e nitidez (0..~7000, comparável entre fotos)."""

    width: int
    height: int
    megapixels: float
    sharpness: float

    @property
    def small(self) -> bool:
        """Foto pequena: redesenhar em 2 MP tende a acrescentar detalhe."""
        return self.megapixels < SMALL_MP

    @property
    def big(self) -> bool:
        """Foto já grande: não há por que redesenhar em 2 MP."""
        return self.megapixels >= BIG_MP

    @property
    def soft(self) -> bool:
        """Sem detalhe fino (fora de foco, ou fundo liso demais): candidata a restauração."""
        return self.sharpness < SOFT

    @property
    def crisp(self) -> bool:
        """Detalhe firme: dá para só melhorar sem redesenhar."""
        return self.sharpness >= CRISP


# foto que não deu para medir (é o que `measure` devolve em qualquer erro)
EMPTY = Pic(0, 0, 0.0, 0.0)


def measure(data: bytes) -> Pic:
    """Mede a foto a partir dos bytes do arquivo. Nunca levanta: em qualquer erro devolve `EMPTY`.

    O caminho é barato de propósito (~30 ms para um JPEG de 12 MP, que é o caso comum; um PNG de 12 MP custa ~150 ms
    porque o PNG não tem como decodificar já reduzido): JPEG usa `draft` para decodificar em cinza e em 1/2, 1/4 ou
    1/8 do tamanho, e o resto é uma redução `LANCZOS` até 768 px mais o mapa de bordas.
    """
    try:
        im = Image.open(io.BytesIO(data))
        width, height = im.size                     # antes de reduzir: é o tamanho de verdade do arquivo
        if width < MIN_SIDE or height < MIN_SIDE:
            return EMPTY                            # 1x1, 2x2: não há o que medir
        if im.format in ("JPEG", "MPO"):            # MPO é o JPEG de várias fotos das câmeras de celular
            im.draft("L", (SIDE, SIDE))             # decodifica já em cinza e reduzido (2 a 3x mais rápido)
        im.thumbnail((SIDE, SIDE), Image.LANCZOS)   # nunca aumenta: foto menor que 768 é medida no tamanho dela
        if min(im.size) < MIN_SIDE:
            return EMPTY
        edges = im.convert("L").filter(ImageFilter.FIND_EDGES)   # transparência é ignorada aqui (só o RGB conta)
        sharpness = float(ImageStat.Stat(edges).var[0])           # variância do mapa de bordas
        return Pic(width, height, width * height / 1e6, sharpness)
    except Exception:                               # noqa: BLE001 - qualquer defeito vira "não sei medir"
        return EMPTY


def describe(pic: Pic) -> str:
    """Frase curta em inglês (é o que o Clef lê). `Pic` vazio vira "" (nada foi medido)."""
    if pic.width <= 0 or pic.height <= 0:
        return ""
    if pic.soft:
        detail = "soft focus"
    elif pic.crisp:
        detail = "sharp detail"
    else:
        detail = "normal detail"
    return f"{pic.width}x{pic.height}, {pic.megapixels:.1f} MP, {detail}"
