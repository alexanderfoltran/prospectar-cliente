# Revisao dos arquivos recebidos

Esta copia foi preparada a partir dos arquivos fornecidos pelo proprietario. Os originais permanecem separados da pasta do repositorio.

## Ajustes de privacidade

- Retirada a chave fixa de assinatura de licencas do servidor. A integracao passa a exigir `PROSPECTAX_LICENSE_SECRET` para gravar licencas assinadas. A verificacao obrigatoria de assinatura recusa configuracao sem chave.
- Retirado o endpoint particular de licenciamento. Quando necessario, configure `PROSPECTAX_LICENSE_API` no ambiente privado.
- Retiradas as senhas padrao e a conta de teste do provisionamento inicial. Novas instalacoes precisam de uma senha administrativa propria. Cadastros existentes nao sao alterados por essa mudanca.
- O servidor passa a escutar somente em loopback por padrao. Nenhum tunel foi aberto para esta revisao.
- Caminhos com nome pessoal de usuario do Windows foram substituidos nos guias Markdown e removidos do conteudo do PDF tecnico. Os metadados dos PDFs tambem foram limpos.
- O arquivo de estado de tunel e o executavel Windows de terceiros nao foram incluidos.
- Acrescentadas exclusoes para credenciais, bases, cadastros, historicos, backups e arquivos gerados.

Nao foram encontrados emails pessoais, CPFs formatados ou chaves reconheciveis de APIs externas nos arquivos de texto revisados. A chave fixa de licenciamento foi tratada como sensivel. Essa verificacao se aplica apenas aos arquivos recebidos e nao constitui garantia de ausencia de vulnerabilidades ou auditoria completa do sistema.

## Componentes ausentes

A pasta `site/` e os programas de `_ATUALIZADOR_RECEITA/` nao estavam nos anexos iniciais. A base Receita/Parquet nao foi recebida e deve continuar fora do GitHub. Sem as telas e a base local, nao e possivel validar o fluxo completo de prospeccao.

As integracoes opcionais com APIs e o licenciamento externo precisam de configuracao e validacao independentes. Credenciais usadas no computador de origem nao foram transferidas.

## Validacao desta copia

- Dependencias instaladas com Python 3.12, DuckDB 1.5.2 e ReportLab 5.0.1; verificacao de dependencias sem conflitos.
- Todos os modulos Python passaram na compilacao.
- O provisionamento sem senha explicita foi recusado. Com uma senha temporaria aleatoria, foi criada somente a conta administrativa; uma nova leitura preservou o cadastro. A senha em texto nao foi gravada no arquivo de usuarios.
- Em servidor restrito a loopback e com pastas temporarias: health respondeu, acesso privado sem sessao foi recusado, senha incorreta foi recusada e login e perfil autenticado responderam corretamente.
- A assinatura de licencas sem chave privada foi recusada antes de gravar arquivo.
- A busca pelos valores sensiveis identificados nos originais nao encontrou esses valores no codigo ou no texto extraido dos PDFs preparados.
- O iniciador informa a ausencia da pasta `site/`, em vez de apresentar a copia como completa.

Os dados e as credenciais do teste foram temporarios. Nao foram usados cadastros reais nem feitas chamadas a integracoes externas. Nao foram testadas a interface, a base da Receita, as integracoes com chaves ou a execucao dos iniciadores no Windows.
