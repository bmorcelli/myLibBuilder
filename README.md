# This brench
Allow skipping normal boot process by using a button pressed
Allow Disabling Launcher after DeepSleep recovery


# myLibBuilder
O objetivo deste projeto é pré compilar o framework para ser usado nos meus projetos esp32 com platformio usando as versoes especificadas em `versions.txt` e com os patches nos arquivos, de acordo com o que está na pasta `patches`

## Script de build
O script `run.py [-t {target}]` executa a seguinte tarefa:
* Ler o arquivo `versions.txt`, que possui o formato:
```
{repositorio}: {branch/tag} {commit}
{espressif component}: {version}
```
Os repositórios normalmente sao da `espressif`
* Fazer o `git clone` dos repositórios `esp-idf`, `esp32-arduino-lib-builder` e `arduino-esp32` para a pasta local `build/`, dentro do projeto, caso ainda não tenham sido clonados. O `arduino-esp32` é clonado diretamente em `esp32-arduino-lib-builder/components/arduino`, que é onde o `build.sh` já espera encontrá-lo — assim o `build.sh` compila exatamente o checkout preparado pelo `run.py`, em vez de clonar sua própria cópia por cima
* Ajustar o checkout (branch/tag e commit) de cada um desses repositórios conforme especificado em `versions.txt`
* ajustar os componentes nos repositórios `esp-idf` e `arduino-esp32` conforme especificado em `versions.txt`
* aplicar os patches contidos em `patches/**`, que estao separados nas pastas de cada repositório. Arquivos `{filename}.append` devem ser adicionados ao final do arquivo `{filename}`, arquivos .diff ou .patch devem ser adicionados conforme git, e outros arquivos devem ser copiados/sobrescritos completamente. O patch `patches/esp32-arduino-lib-builder/tools/install-arduino.sh` substitui o script original (que clonaria/daria `pull` no `arduino-esp32` por conta própria) por uma versão que apenas confirma que o checkout já preparado pelo `run.py` existe, preservando o commit/patches pinados
* executar o build do projeto (`build.sh -t {target} -e`, dentro do checkout do `esp32-arduino-lib-builder`) para o target especificado, podendo ser: esp32, esp32s2, esp32s3, esp32c2, esp32c3, esp32c6, esp32h2, esp32p4, esp32p4_es, esp32c5, esp32c61
* quando `-t/--target` nao for informado, pedir confirmacao e executar `build.sh -e`, deixando o `build.sh` compilar todos os envs/targets disponiveis em sequencia
* se `ccache` estiver instalado, ativar `IDF_CCACHE_ENABLE=1`; no GitHub Actions ele fica disponivel durante o job, mas nao é salvo em cache persistente para nao consumir a cota do GitHub

### Uso local com WSL
Por padrao, o `run.py` usa a pasta `build/` dentro do repositorio para armazenar os checkouts temporarios. Ao rodar pelo WSL com o repositorio em `/mnt/c/**`, isso pode deixar o build mais lento por causa do filesystem montado do Windows.

Antes de usar o `run.py` no WSL, aponte `MYLIBBUILDER_ROOT` para uma pasta no filesystem Linux, por exemplo:

```bash
export MYLIBBUILDER_ROOT="$HOME/.build/myLibBuilder"
```

Depois rode normalmente:

```bash
python3 run.py -t esp32
```

> A compilação de um único target pode ser bem demorada (~20 minutos para o `esp32p4`), pois roda o pipeline completo do `esp32-arduino-lib-builder` (idf-libs, bootloaders, memory variants, modelos do ESP-SR, etc).

## Workflow de compilacao
O workflow `.github/workflows/push.yml` executa a seguinte automação manualmente via `workflow_dispatch`:

### build-libs
* Permitir escolher entre compilar todos os targets disponiveis no matrix ou selecionar no minimo 1 target para compilar
* Preparar uma unica vez os checkouts pinados em `build/`, aplicar patches e baixar git submodules antes de criar o artifact temporario `prepared-sources`
* Rodar `run.py --build-only -t {target}` para cada target selecionado em paralelo (matrix): `esp32, esp32s2, esp32s3, esp32c2, esp32c3, esp32c6, esp32h2, esp32p4, esp32p4_es, esp32c5, esp32c61`
* Rodar `run.py --prepare-only` para fazer download dos repositórios e dos componentes para preparar o CI/CD
* Fazer upload do conteúdo de `dist/` (o `arduino-esp32-libs-{target}-{idf_version}.tar.gz` gerado pelo `build.sh -e`, igual ao que o `esp32-arduino-lib-builder` gera na própria CI) como artifact `artifacts-{target}`
* Apagar o artifact temporario `prepared-sources` ao fim do build dos targets para reduzir uso de storage em repositorio privado

### combine-artifacts
* Recolher todos os artifacts `artifacts-*`
* Extrair o `.tar.gz` de cada target em uma única pasta `out`, exatamente como é feito no workflow do `esp32-arduino-lib-builder`
* Compactar `out/tools/esp32-arduino-libs` em `{branch}_esp32-arduino-libs-{yyyymmdd-hhmmss}.tar.gz`
* Copiar o `package_esp32_index.template.json` gerado
* Enviar o `.tar.gz` e o `package_esp32_index.template.json` para a release/tag `builds` (criada automaticamente caso ela nao exista)

## Workflow de sincronizacao
* Sincronizar os assets da release tag `builds` deste projeto com a tag `builds` do meu fork `bmorcelli/esp32-arduino-lib-builder`, usando o token LIB_BUILDER_TOKEN salvo no Secrets do repositório.
* a fonte é este repositório, o `bmorcelli/esp32-arduino-lib-builder` deve ficar igual a este aqui, enviando os assets que nao estao no repo de destino.
