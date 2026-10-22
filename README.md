# ProspectaX

Ferramenta de prospeccao empresarial com servidor Python, consultas em DuckDB/Parquet, organizacao comercial e integracoes opcionais com fontes publicas.

## O que esta neste repositorio

- Modulos Python na raiz, preservando os caminhos de importacao do programa.
- Iniciadores `.bat` para Windows na raiz.
- Guias e PDFs em [`docs/`](docs/).
- Dependencias de Python em [`requirements.txt`](requirements.txt).

As quatro partes recebidas foram reunidas: backend, telas em `site/`, atualizador em `_ATUALIZADOR_RECEITA/` e documentacao. A base empresarial e os dados privados da instalacao nao fazem parte do repositorio.

Salvar o codigo no GitHub nao coloca o sistema no ar. Este repositorio nao configura hospedagem, publicacao de site ou tunel.

## Baixar e entender o GitHub

Na pagina principal do repositorio, clique em **Code > Download ZIP** para baixar o codigo. Depois, extraia o ZIP no computador antes de executar os comandos abaixo.

O repositorio publico permite leitura e copia. Isso nao concede acesso de escrita ao original. Nao foi adicionado nenhum colaborador nem concedido acesso de escrita a terceiros nesta organizacao. O historico do Git guarda as versoes enviadas do codigo; os cadastros e bancos privados precisam de backup separado.

Veja tambem [`docs/COMO_USAR_O_GITHUB.md`](docs/COMO_USAR_O_GITHUB.md).

## Preparar o Python

Use Python 3.12. No Windows, execute na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Para iniciar no Windows, use o Python desse ambiente:

```powershell
.\.venv\Scripts\python.exe iniciar_local.py
```

Abra o endereco local informado pelo programa no navegador. O acesso inicial e **usuario `admin`, senha `admin123`**, mantido por solicitacao expressa do proprietario. Essa e uma senha padrao publica da ferramenta, e nao uma credencial particular extraida da instalacao original. Instalacoes com cadastros existentes preservam esses cadastros e suas senhas.

Opcionalmente, antes da primeira execucao, `PROSPECTAX_ADMIN_PASSWORD` permite definir outra senha inicial, com pelo menos 16 caracteres. Nenhum cadastro de producao foi transferido para este repositorio.

Para pesquisar empresas reais, disponibilize sua base em `dados_parquet/` ou configure `PROSPECTAX_DATA_DIR` para a pasta externa que ja possui. Os testes desta copia utilizaram somente uma base sintetica temporaria, que nao foi instalada como dado real.

## Atualizador da Receita

Os scripts de atualizacao e geracao do indice acompanham o codigo. Para instalar suas dependencias adicionais:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_atualizador.txt
```

Consulte [`_ATUALIZADOR_RECEITA/README_ATUALIZADOR.txt`](_ATUALIZADOR_RECEITA/README_ATUALIZADOR.txt) antes de executar. A base nacional pode exigir bastante tempo e espaco. O download completo e a troca de uma base existente nao foram executados nesta revisao.

## Configuracoes e dados privados

As chaves de APIs sao opcionais para o codigo-base e devem ser informadas pelo ambiente do processo ou pelos campos protegidos da aplicacao. O programa nao carrega arquivos `.env` automaticamente. Nunca coloque chaves reais no codigo ou nos exemplos do GitHub.

Variaveis de configuracao principais:

| Variavel | Uso |
| --- | --- |
| `PROSPECTAX_HOST` | Endereco de escuta; o padrao desta copia e `127.0.0.1`. |
| `PROSPECTAX_PORT` | Porta local, normalmente `8765`. |
| `PROSPECTAX_ADMIN_PASSWORD` | Substitui a senha inicial padrao em uma instalacao nova. |
| `PROSPECTAX_SITE_DIR` | Pasta das telas e dos JSONs locais de usuarios. |
| `PROSPECTAX_PRIVATE_DIR` | Pasta dos bancos e configuracoes privados. |
| `PROSPECTAX_DATA_DIR` | Pasta da base Parquet, mantida fora do Git. |
| `PROSPECTAX_LICENSE_API` | Endpoint de licenciamento, quando essa integracao for usada. |
| `PROSPECTAX_LICENSE_SECRET` | Chave privada de assinatura de licencas; sem valor padrao no codigo. |

O modo de licenciamento conserva a configuracao original do programa e nao foi validado contra um servico real. O endpoint particular da instalacao de origem foi retirado. A protecao de chaves por Windows DPAPI descrita nos guias depende do Windows; o funcionamento das integracoes com chaves no Linux nao foi validado.

## Antes de enviar outros arquivos

O `.gitignore` exclui dados, JSONs, licencas locais, chaves, bancos, backups, executaveis, configuracoes locais do Cloudflare e arquivos compactados. Ele reduz envios acidentais, mas nao remove segredos gravados dentro de codigo ou documentos.

Revise novos arquivos antes de publica-los. As bases de clientes, usuarios, historicos e chaves devem ficar apenas no ambiente privado. Nao use `git add -f` para contornar essas exclusoes.

Consulte [`docs/REVISAO_PARA_PUBLICACAO.md`](docs/REVISAO_PARA_PUBLICACAO.md) para os ajustes desta copia. Os guias historicos descrevem a instalacao original; seus relatos de testes nao comprovam testes nesta copia.
