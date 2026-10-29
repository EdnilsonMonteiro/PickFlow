# Bibliotecas de terceiros (sem build, servidas por /static/vendor/)

Usadas só pelo visualizador 3D (`web/splat.js`), carregado sob demanda. Ficam aqui para o PickFlow funcionar sem internet.

| Pasta | Pacote | Versão | Licença | Arquivos |
|---|---|---|---|---|
| `three/` | [three](https://www.npmjs.com/package/three) | 0.180.0 | MIT (`three/LICENSE`) | `build/three.module.min.js`, `build/three.core.min.js`, `examples/jsm/postprocessing/Pass.js` (usado pelo Spark) |
| `spark/` | [@sparkjsdev/spark](https://www.npmjs.com/package/@sparkjsdev/spark) | 2.3.1 | MIT (`spark/LICENSE`) | `dist/spark.module.min.js` |

O mapa de import está em `web/index.html` (`three`, `three/addons/`, `spark`). Para atualizar: `npm pack` das versões novas e
copiar os mesmos arquivos (o Spark pede `three >= 0.180`).
