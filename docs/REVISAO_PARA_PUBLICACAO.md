# Revisao para publicacao

As quatro partes fornecidas pelo proprietario foram reunidas, comparadas com a versao previamente revisada e conferidas antes do envio. Os originais e os arquivos de empacotamento permanecem separados do repositorio.

## Privacidade e configuracao

- A chave fixa de assinatura de licencas e o endpoint particular de licenciamento continuam fora do codigo publicado. Quando usados, devem ser configurados no ambiente privado.
- Os caminhos pessoais foram retirados dos guias e do conteudo do PDF tecnico. Os PDFs publicados mantiveram a versao com metadados limpos, em vez de serem substituidos pelos originais recebidos novamente.
- Foram procurados nomes pessoais, usuarios do computador, caminhos locais, IPs particulares, emails pessoais, documentos e padroes de credenciais nos arquivos preparados para publicacao.
- A verificacao do tunel incluiu links, tokens, identificadores de conta e arquivos de estado. Nao foi encontrado token de conta ou link pessoal de tunel gravado nos arquivos de codigo e interface revisados.
- O estado do tunel original nao foi publicado. Os unicos campos desse arquivo recebido eram identificador de processo, horario de criacao e porta; seus valores nao foram copiados para o repositorio.
- Configuracoes de Cloudflare, bancos, dados privados, cadastros, licencas locais, backups, logs e executaveis de terceiros ficam excluidos pelo .gitignore.
- Enderecos locais de loopback e URLs oficiais de fontes publicas foram preservados. Esses enderecos tecnicos nao representam um IP particular do proprietario ou uma chave de API pessoal.
- A obrigacao de revisar o conteudo antes de cada envio esta registrada em AGENTS.md. A revisao se aplica aos arquivos recebidos e nao equivale a garantia de ausencia de vulnerabilidades.

## Excecao autorizada: acesso inicial

O proprietario solicitou expressamente preservar o login inicial admin com senha admin123. Essa senha padrao publica faz parte da ferramenta e foi mantida. A criacao inicial aceita uma senha propria por PROSPECTAX_ADMIN_PASSWORD, e os cadastros existentes nao sao sobrescritos. Nenhuma base de usuarios de producao foi publicada.

## Validacao executada

- Instalacao das dependencias do servidor e do atualizador em ambiente Python isolado.
- Compilacao de 18 arquivos Python e verificacao de sintaxe dos sete arquivos JavaScript e cinco scripts embutidos nas paginas.
- Conversao de CSVs sinteticos em ZIP para Parquet, geracao de indice DuckDB e validacao das contagens, incluindo CNAE secundario.
- Inicializacao pelo iniciador real, restrita a loopback, usando pastas temporarias. Health, rejeicao de acesso sem autenticacao, login e busca no indice responderam corretamente.
- Login inicial autorizado, armazenamento do hash da senha, preservacao de cadastro existente e substituicao opcional da senha inicial em uma instalacao nova.
- Teste em Chromium: formulario de login, pagina principal, busca pela interface e exibicao da empresa sintetica; as paginas de parceiros, negocios, inteligencia, radar e administracao responderam.
- Recusa de assinatura de licencas quando falta uma chave privada configurada.

Os testes utilizaram apenas dados sinteticos temporarios. O acesso a fontes externas foi bloqueado nesses testes, e nenhum tunel foi iniciado. Nao foram executados o download nacional da Receita, a substituicao de uma base real, os iniciadores no Windows ou as integracoes que exigem credenciais.
