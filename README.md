# myLibBuilder
O objetivo deste projeto é pré compilar o framework para ser usado nos meus projetos esp32 com platformio usando as versoes especificadas em `versions.txt` e com os patches nos arquivos, de acordo com o que está na pasta `patches`

## Script de build
O script `run.py -t {target}` deve executar a seguinte tarefa:
* Ler o arquivo `versions.txt`, que possui o formato:
```
{repositorio}: {brench/tag} {commit}
{espressif component}: {version}
```
Os repositórios normalmente sao da `espressif`
* mudar a versao dos submodules `esp-idf`, `esp32-arduino-lib-builder` e `arduino-esp32` para as versoes especificadas
* ajustar os componentes nos repositórios `esp-idf` e `arduino-esp32` conforme especificado em `versions.txt`
* aplicar os patches contidos em `patches/**`, que estao separados nas pastas de cada repositório. Arquivos `{filename}.append` devem ser adicionados ao final do arquivo `{filename}`, arquivos .diff ou .patch devem ser adicionados conforme git, e outros arquivos devem ser copiados/sobrescritos completamente.
* executar o build do projeto para o target especificado, podendo ser: esp32, esp32s2, esp32s3, esp32c2, esp32c3, esp32c6, esp32h2, esp32p4, esp32p4_es, esp32c5, esp32c61

## Workflow de compilacao
O workflow `.github/workflow/push.yml` deve executar a seguinte automação:

### build
* Rodar a compilação dos targets `esp32, esp32s2, esp32s3, esp32c2, esp32c3, esp32c6, esp32h2, esp32p4, esp32p4_es, esp32c5, esp32c61` em paralelo
* Compactar o conteúdo da saída do build, e fazer upload para os artifacts

### pos-build
* Recolher os artefatos, 
* agrupa-los em uma só pasta, como é feito em `esp32-arduino-lib-builder`
* Compactar a pasta com todas as builds em `{brench}_{yyyy-mm-dd.hhmmss}.tar.gz
* Enviar para uma release/tag `builds` (criar caso ela nao exista)

## Workflow de sincronizacao
* Sincronizar os assets da release tag `builds` deste projeto com a tag `builds` do meu fork `bmorcelli/esp32-arduino-lib-builder`, usando o token LIB_BUILDER_TOKEN salvo no Secrets do repositório.
* a fonte é este repositório, o `bmorcelli/esp32-arduino-lib-builder` deve ficar igual a este aqui, enviando os assets que nao estao no repo de destino.


