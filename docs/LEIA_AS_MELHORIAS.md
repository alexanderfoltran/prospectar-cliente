**ProspectaX — descoberta de compradores e consultas públicas**

A busca existente por CNAE, estado e cidade continua disponível. Os filtros adicionais são aplicados à base encontrada antes da paginação e também à exportação CSV.

Para abrir, use `INICIAR_PROSPECTAX.bat` nesta pasta. Se o servidor anterior estiver aberto, feche-o primeiro. Acesse `http://127.0.0.1:8765` e recarregue com Ctrl+F5 após a atualização. Os usuários e senhas existentes são mantidos.

- Em **Encontrar compradores**, informe o que vende para receber sugestões editáveis de segmentos e CNAEs. Ou informe o CNPJ de um cliente conhecido para aproveitar suas atividades principal e secundárias.
- Em **Potencializar a busca**, filtre por telefone/e-mail disponível, porte, prefixo de CEP e palavras a excluir. Campos desabilitados ainda não existem no índice antigo: bairro, abertura, matriz/filial e segundo telefone são adicionados pelo gerador de índice atualizado. Não é necessário baixar outra base para usar os filtros já disponíveis.
- A busca de **mapas públicos** complementa a Receita com estabelecimentos e contatos publicados no OpenStreetMap. A consulta usa um raio em torno da cidade escolhida e não representa todos os limites do município. Há cache e limite conservador de consultas; a fonte pode ficar indisponível.
- Em **Produtos para importar**, compare até quatro anúncios e exporte os dados. O catálogo automático usa a API gratuita AliExpress Affiliate, que exige cadastro, aplicativo aprovado e chave. O administrador geral configura as credenciais na própria tela. O acesso real depende das permissões e do protocolo autorizado na conta; não é um catálogo ilimitado de todos os fabricantes chineses. A importação por link funciona apenas quando o anúncio permite leitura e publica dados estruturados.
- Em **Pendências e consultas públicas**, ou na ficha da empresa, consulte a base PGFN importada e sanções CEIS/CNEP. Esses registros não constituem consulta completa de crédito. Falha ou ausência de dados não é apresentada como “nome limpo”.
- Favoritos, retornos e buscas salvas têm armazenamento por usuário no servidor e uma cópia local de alterações pendentes. Conflitos concorrentes são detectados. Se duas janelas alterarem o mesmo campo, preserve um backup antes de escolher qual versão manter.

**Como ativar as consultas públicas gratuitas.**

1. PGFN: baixe e descompacte os CSVs da página oficial de dados abertos. Execute `IMPORTAR_DIVIDA_ATIVA.bat`, selecione os arquivos e informe competência e cobertura. O importador cria um índice separado, preservando a base anterior até concluir. Dados podem abranger inscrições com situações diferentes, inclusive suspensas; confira o registro e a fonte antes de concluir algo.
2. CGU: obtenha a chave gratuita no Portal da Transparência e cadastre-a no painel de consultas públicas como administrador geral. Ela fica no servidor.
3. Certidões e protestos: os botões abrem os serviços oficiais. Onde não existe parâmetro público confirmado para preencher o CNPJ, o botão copia o número para colar. CAPTCHA e autenticação permanecem no portal.

Nenhum cadastro em serviço externo, contratação ou envio de mensagens é feito por essa atualização. As rotas novas não usam os tokens pagos antigos da Apify/Infosimples; os recursos legados permanecem separados.

Fontes: [AliExpress](https://developer.alibaba.com/docs/api.htm?apiId=45803), [Photon](https://photon.komoot.io/), [Overpass Private.coffee](https://private.coffee/services.html), [OpenStreetMap](https://www.openstreetmap.org/copyright), [PGFN dados abertos](https://www.gov.br/pgfn/pt-br/assuntos/divida-ativa-da-uniao/transparencia-fiscal-1/dados-abertos), [API CGU](https://api.portaldatransparencia.gov.br/v3/api-docs), [chave gratuita CGU](https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email), [certidão federal](https://servicos.receitafederal.gov.br/servico/certidoes).
