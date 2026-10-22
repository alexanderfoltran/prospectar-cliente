# Instrucoes para trabalhar neste repositorio

## Privacidade antes de qualquer publicacao

A revisao de dados pessoais e credenciais e obrigatoria antes de cada envio ao GitHub. Este repositorio e publico. Nao publique arquivos ainda nao revisados.

- Confira os arquivos finais preparados para commit, os nomes e caminhos, o conteudo dos documentos e os metadados dos PDFs. Quando necessario, confira tambem o historico Git. Nao confunda a revisao do arquivo de trabalho com a revisao do que efetivamente sera enviado.
- Procure nomes pessoais, usuarios do computador, caminhos locais, emails particulares, documentos, enderecos, IPs particulares e URLs de acesso remoto. Procure tambem tokens, chaves de API, senhas, cookies, certificados, chaves de licenca e configuracoes privadas.
- Nao publique bases de usuarios, cadastros de clientes, notas, historicos, licencas locais, arquivos .env, backups ou logs. Dados publicos da Receita pertencem ao fluxo de importacao, mas as bases volumosas ficam fora do Git.
- Nao grave valores sensiveis em respostas, comandos, logs, relatorios ou testes. Informe a localizacao e o tipo do problema sem reproduzir o valor. Faca a revisao localmente; nao envie os arquivos a servicos externos de analise.
- Remova os dados particulares da copia de publicacao ou mantenha o arquivo fora do envio. Use configuracao externa para credenciais pessoais. Nao acrescente contas de teste permanentes ou segredos ficticios no codigo.
- Excecao expressamente autorizada pelo proprietario: o login inicial admin com senha admin123 faz parte da ferramenta e deve ser preservado. Essa senha de exemplo e publica; nao a confunda com uma credencial pessoal encontrada nos arquivos. Uma senha inicial propria pode ser fornecida por PROSPECTAX_ADMIN_PASSWORD. Cadastros existentes devem ser preservados.
- Preserve as referencias tecnicas necessarias: nomes de APIs, URLs oficiais publicas e enderecos de loopback nao sao, por si, credenciais particulares. Qualquer identificador de origem duvidosa deve ser investigado antes da publicacao.
- O .gitignore ajuda a prevenir acidentes, mas nao substitui a revisao do conteudo. Nao use git add -f para contornar exclusoes. Nao afirme que uma busca automatizada garante ausencia de vulnerabilidades.

## Preservacao e validacao

- Preserve os arquivos originais recebidos e os dados da instalacao do proprietario. Use somente dados e credenciais temporarios nos testes.
- Preserve o servidor local por padrao e a ausencia de segredo fixo de licenca. Preserve tambem a excecao do login inicial autorizada acima.
- Mantenha os modulos Python e iniciadores na raiz, a interface em site/ e os utilitarios em _ATUALIZADOR_RECEITA/.
- As tarefas na nuvem ja usam um ambiente isolado. Use o checkout existente; nao crie um Git worktree sem pedido expresso.
- Nao abra tuneis, publique uma instancia do sistema ou conceda acesso de escrita a terceiros como parte de organizar os arquivos para download.
- Registre os testes realmente executados e as limitacoes. Nao apresente relatos historicos de testes como validacao atual.
