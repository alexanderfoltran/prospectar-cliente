# ProspectaX - novas ferramentas de negócio

Instalação: `C:\Users\USUARIO\Desktop\prospector_local`.

Acesse **http://127.0.0.1:8765/negocios.html** ou use o seu link do túnel com **/negocios.html** ao final. Entre com sua conta do sistema. Se a página estiver antiga, use **Ctrl + F5**.

## Recursos incluídos

1. **Cotações:** prepare pedidos com produto, quantidade, cidade e até 25 fornecedores aprovados. Copie a mensagem para enviar pelo seu canal, ou compartilhe internamente com contas de fornecedores. Registre respostas recebidas e compare preço por unidade, frete, total, pedido mínimo e prazo. Pedidos já compartilhados ficam preservados; para mudar seus termos, prepare outro.
2. **Área do fornecedor:** uma conta vinculada pode enviar atualizações do perfil e de seus produtos. As alterações só entram na vitrine após aprovação do administrador geral. Também recebe pedidos compartilhados e pode responder suas propostas.
3. **Catálogo de produtos:** produtos com descrição, foto por URL, link da oferta, unidade, pedido mínimo, preço ou faixa de preço. Há filtros de texto, fornecedor e categoria. Os botões levam à cotação ou à calculadora.
4. **Quadro de negociações:** reúne clientes salvos, favoritos com retorno e cartões adicionados na nova área. Arraste entre etapas ou use o seletor do cartão. Os status são os mesmos da Prospecção e continuam respeitando as regras de responsável da equipe. Até 500 cartões são exibidos; há filtro por nome/CNPJ. As datas de retorno são compartilhadas com as ferramentas comerciais de Prospecção.
5. **Carteira de clientes:** importe CSV ou XLSX com cabeçalho `CNPJ`, até 500 CNPJs e 2 MB por arquivo. Empresas encontradas, CNPJs ausentes e frequência de CNAEs ficam disponíveis. Exporte os resultados ou carregue os principais CNAEs na Prospecção para uma nova pesquisa. A leitura de XLSX usa a biblioteca de planilhas já utilizada pelo sistema e precisa de internet para seu carregamento inicial.
6. **Margem de revenda:** informe compra por unidade, quantidade, frete total, outras despesas do lote, venda por unidade e percentuais sobre a venda. Veja custo e resultado por unidade, resultado do lote, margem, markup e preço de equilíbrio. A conta inclui somente os custos informados.
7. **Alertas:** retornos vencidos ou previstos para hoje, benefícios que vencem em até sete dias e parceiros publicados nos últimos 14 dias que combinam com o perfil. É possível marcar como visto. Os avisos são atualizados ao abrir a área ou clicar em Atualizar dados. O cadastro do parceiro agora aceita a validade do benefício.
8. **Resultados dos parceiros:** cada usuário pode marcar “Pedi cotação”, “Iniciei negociação” e “Comprei”. O fornecedor vinculado vê os totais dos próprios parceiros; o administrador geral vê o conjunto. O relatório considera os últimos 30 dias e exporta CSV, junto aos indicadores de abertura e cliques. São declarações de usuários; não comprovam faturamento.

## Preparar o primeiro parceiro e produto

1. Com administrador geral, abra **Fornecedores parceiros > Gestão de parceiros** e cadastre/aprove um parceiro.
2. Em **Ferramentas de negócio > Gestão**, cadastre um produto para esse parceiro. Selecione Publicado para ele aparecer no catálogo.
3. Para dar acesso ao fornecedor, crie uma conta pela Administração do sistema, se necessário. Depois, em Gestão, selecione o parceiro e a conta e salve o vínculo. Cada parceiro tem uma conta responsável; uma conta pode cuidar de vários parceiros. Remover o vínculo revoga o acesso pela área do fornecedor.
4. Envios feitos pelo fornecedor aparecem em **Alterações aguardando aprovação**. Confira o conteúdo e aprove ou rejeite. Mudanças concorrentes são sinalizadas; um envio antigo não substitui um cadastro mais recente.

## Arquivo de carteira

Exemplo de estrutura (preencha com CNPJs reais da sua carteira):

```csv
CNPJ;Nome
<CNPJ da empresa 1>;Empresa 1
<CNPJ da empresa 2>;Empresa 2
```

Somente a coluna CNPJ é necessária. A consulta usa a base local, sem enviar a carteira a uma API externa. Na Prospecção, os CNAEs carregados da carteira não iniciam a busca automaticamente: escolha a região e execute a pesquisa.

## Instalação e backup

- As oito ferramentas foram aplicadas à pasta original após autorização. A pasta `prospectax_atualizacao_patrocinio` continua disponível para testar ideias futuras.
- Nenhum Parquet ou índice DuckDB foi copiado ou alterado. O mesmo túnel permanece ativo.
- Os novos cadastros ficam em `private_data/business.sqlite`, ao lado dos bancos próprios de parceiros e descoberta do sistema.
- Os usuários, fornecedores, favoritos e registros comerciais originais foram preservados. Não foram importados cadastros fictícios ou bancos da cópia de testes.
- O backup anterior fica em `backup/antes_ferramentas_negocio_<data-hora>/`. Ele contém os arquivos substituídos, os JSONs do site e cópias consistentes dos bancos SQLite existentes.
- A pasta de testes continua com cadastros próprios; não há sincronização automática com o sistema original.
- Compartilhar cotação é uma ação explícita do usuário dentro do sistema; cobranças e pagamentos continuam fora desta atualização.

## Verificação

Testes cobrem publicação e revisões, permissões do fornecedor, privacidade de pedidos/propostas, cálculo decimal, importação e isolamento da carteira, retornos/alertas, resultados agregados e autenticação. A interface foi conferida com contas e dados fictícios.
