# ProspectaX

Ferramenta de prospeccao empresarial com servidor Python, consultas em DuckDB/Parquet, organizacao comercial e integracoes opcionais com fontes publicas.

## O que esta neste repositorio

- Modulos Python na raiz, preservando os caminhos de importacao do programa.
- Iniciadores `.bat` para Windows na raiz.
- Guias e PDFs em [`docs/`](docs/).
- Dependencias de Python em [`requirements.txt`](requirements.txt).

**Esta copia ainda esta incompleta para uso no navegador.** Os arquivos recebidos nao incluem a pasta `site/` com HTML, CSS e JavaScript, nem `_ATUALIZADOR_RECEITA/`, usada pelos iniciadores de atualizacao. Esses componentes precisam ser acrescentados apos revisao. A base empresarial tambem nao faz parte do repositorio.

Salvar o codigo no GitHub nao coloca o sistema no ar. Este repositorio nao configura hospedagem, publicacao de site ou tunel.

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

Quando os arquivos de `site/` estiverem disponiveis, inicie com o Python desse ambiente:

```powershell
.\.venv\Scripts\python.exe iniciar_local.py
```

O iniciador pede uma senha de pelo menos 16 caracteres para criar a conta `admin` na primeira execucao. A senha nao aparece na tela. Nao existe mais senha padrao nem conta de teste criada automaticamente. Instalacoes com cadastros existentes preservam esses cadastros e suas senhas.

## Configuracoes e dados privados

As chaves de APIs sao opcionais para o codigo-base e devem ser informadas pelo ambiente do processo ou pelos campos protegidos da aplicacao. O programa nao carrega arquivos `.env` automaticamente. Nunca coloque chaves reais no codigo ou nos exemplos do GitHub.

Variaveis de configuracao principais:

| Variavel | Uso |
| --- | --- |
| `PROSPECTAX_HOST` | Endereco de escuta; o padrao desta copia e `127.0.0.1`. |
| `PROSPECTAX_PORT` | Porta local, normalmente `8765`. |
| `PROSPECTAX_ADMIN_PASSWORD` | Senha inicial, se fornecida pelo ambiente em vez do iniciador. |
| `PROSPECTAX_SITE_DIR` | Pasta das telas e dos JSONs locais de usuarios. |
| `PROSPECTAX_PRIVATE_DIR` | Pasta dos bancos e configuracoes privados. |
| `PROSPECTAX_DATA_DIR` | Pasta da base Parquet, mantida fora do Git. |
| `PROSPECTAX_LICENSE_API` | Endpoint de licenciamento, quando essa integracao for usada. |
| `PROSPECTAX_LICENSE_SECRET` | Chave privada de assinatura de licencas; sem valor padrao no codigo. |

O modo de licenciamento conserva a configuracao original do programa e nao foi validado contra um servico real. O endpoint particular da instalacao de origem foi retirado. A protecao de chaves por Windows DPAPI descrita nos guias depende do Windows; o funcionamento das integracoes com chaves no Linux nao foi validado.

## Antes de enviar outros arquivos

O `.gitignore` exclui dados, JSONs, licencas locais, chaves, bancos, backups, executaveis e arquivos compactados. Ele reduz envios acidentais, mas nao remove segredos gravados dentro de codigo ou documentos.

Revise novos arquivos antes de publica-los. As bases de clientes, usuarios, historicos e chaves devem ficar apenas no ambiente privado. Nao use `git add -f` para contornar essas exclusoes.

Consulte [`docs/REVISAO_PARA_PUBLICACAO.md`](docs/REVISAO_PARA_PUBLICACAO.md) para os ajustes desta copia. Os guias historicos descrevem a instalacao original; seus relatos de testes nao comprovam testes nesta copia.
