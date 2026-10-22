ProspectaX - Atualizador da base Receita Federal
================================================

Arquivo principal:
  ATUALIZAR_BASE_RECEITA.bat

O que ele faz:
  1. Descobre o mes mais recente na fonte oficial da Receita.
  2. Baixa os ZIPs de Empresas, Estabelecimentos, Socios e tabelas auxiliares.
  3. Converte para Parquet no mesmo layout usado pelo ProspectaX.
  4. Gera dados_parquet\prospectax_index.duckdb.
  5. Se tudo der certo, renomeia a base antiga para backup e coloca a nova no lugar.

Fonte oficial padrao:
  https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/

Antes de rodar:
  - Feche o ProspectaX.
  - Garanta internet estavel.
  - O script exige pelo menos 60 GB livres.
  - Reserve preferencialmente 80 GB ou mais em SSD; o volume e os temporarios variam.
  - Tenha paciencia: pode demorar bastante.

Teste leve: consulta a fonte na internet, sem baixar a base completa:
  python _ATUALIZADOR_RECEITA\atualizar_base_receita.py --dry-run

Usar um mes especifico:
  python _ATUALIZADOR_RECEITA\atualizar_base_receita.py --month 2026-01 --replace

Usar outra URL base, se a Receita mudar o endereco:
  python _ATUALIZADOR_RECEITA\atualizar_base_receita.py --base-url "https://..." --replace

Onde fica o trabalho temporario:
  _atualizacao_receita\<MES>\

Se a troca final for cancelada:
  A base nova fica preservada em:
    _atualizacao_receita\<MES>\dados_parquet_novo

Observacao:
  Este atualizador foi feito para ficar na sua maquina de producao da base.
  Nao precisa ir no pendrive do cliente.
