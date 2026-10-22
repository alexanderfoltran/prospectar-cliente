# Radar de oportunidades — ProspectaX

No menu da página principal, abra **Radar de oportunidades**. Os quatro módulos ficam nas abas da nova tela. A instalação mantém o login e as permissões de acesso do sistema.

## Busca automática — digite só o item

A primeira aba agora é **Busca automática**. Digite o nome de um item, produto ou serviço e clique em **Buscar oportunidades**. A região é opcional.

O sistema consulta em paralelo editais, contratos e fornecedores, obras, notícias de expansão, a edição mais recente do INPI e mercados internacionais. Para o comércio exterior, procura códigos relacionados no catálogo oficial da NCM e usa automaticamente o último período de 12 meses disponível. A pesquisa apresenta até dois códigos e cinco destinos por código, com a descrição utilizada. Essas correspondências servem para explorar o mercado, não para confirmar classificação fiscal.

Os resultados ficam reunidos, com filtros por fonte, links para conferir, exportação CSV e salvamento na sua conta. A seção **Ver fontes consultadas e cobertura da busca** informa a consulta utilizada, fontes indisponíveis e serviços ainda não conectados. A seleção tem limites de cobertura; não representa toda a internet.

Alguns itens são relacionados a tipos de obra — por exemplo, uniformes e escolas. Esses cartões são identificados como hipóteses comerciais, sem afirmar que a obra esteja comprando o produto.

### Conectar a NVIDIA uma vez

O administrador geral pode abrir **Conectar IA e ampliar a busca na web · configuração única**, cadastrar a chave NVIDIA, escolher um modelo e ativar a análise. A chave usa a proteção do Windows já existente no sistema e não é devolvida ao navegador. Uma chave não foi cadastrada automaticamente.

Com a NVIDIA ativada, a busca ganha um resumo e até seis destaques vinculados aos resultados coletados. A análise considera até quatro resultados por fonte para limitar o uso de tokens. A IA não cria URLs, empresas, contatos ou códigos NCM para os cartões: os registros vêm das fontes consultadas. O uso respeita o limite diário de chamadas definido nas configurações existentes. Se a IA falhar ou não estiver conectada, os resultados das fontes continuam disponíveis.

A NVIDIA faz a análise; a busca nas fontes é executada pelo sistema. Para incluir também resultados de sites na web, conecte uma chave **Brave Search** ou use a configuração Google já existente. Esse buscador é opcional para a consulta às fontes públicas integradas. Cadastre as chaves somente nos campos protegidos da aplicação.

As buscas completas recentes podem ser reutilizadas por 10 minutos, separadamente por usuário. Consultas com fontes indisponíveis ou falha de IA podem ser refeitas imediatamente, reaproveitando os caches individuais das fontes que responderam.

## Compras públicas

- Busque um produto ou serviço e, se desejar, selecione a UF.
- Escolha **Editais com propostas abertas** para encontrar contratações no índice público do PNCP.
- Escolha **Contratos e fornecedores** para consultar contratos publicados e seus fornecedores. Quando há CNPJ empresarial, o cartão permite abrir a pesquisa do fornecedor na função já existente do sistema.
- Os cartões trazem o objeto, órgão, local, valor informado e datas disponíveis. Abra o PNCP para conferir documentos, condições e prazos atuais.
- A busca tem paginação. O CSV exporta apenas a página apresentada.

Fonte: https://pncp.gov.br/app/editais

## Obras e expansão

**Obras públicas:** consulte projetos do Obrasgov por nome, UF, situação e tipo de intervenção. A fonte reúne projetos com financiamento federal, não todas as obras privadas ou municipais do país. O botão do cartão abre o registro na API oficial.

**Expansão empresarial:** pesquise notícias sobre novas unidades, fábricas e lojas. É possível filtrar por nome de empresa, cidade ou estado, e pelos últimos 7, 30 ou 90 dias. O GDELT retorna até 50 títulos e links de veículos brasileiros em português; a seleção depende da cobertura do índice. A data indicada é a detecção no índice, não necessariamente a publicação. Abra a matéria para conferir o anúncio e sua relação com a empresa.

Um cadastro de obra ou uma menção na imprensa é um ponto de partida para investigação comercial. Não confirma, por si só, uma compra em andamento.

Fontes: https://api-publica.obrasgov.gestao.gov.br/ e https://www.gdeltproject.org/

## Marcas — INPI

1. Selecione uma edição recente da Revista da Propriedade Industrial.
2. Escolha **Pedidos publicados**, **Registros concedidos** ou **Todos os atos da edição**.
3. Filtre por marca, titular, produto, UF do titular e/ou classe Nice, de 1 a 45.
4. Confira o número do processo e o ato no PDF oficial.

A primeira consulta de uma edição baixa seu XML e prepara um índice local, que será reutilizado. A pesquisa considera a edição selecionada, não todo o histórico do INPI. Os XMLs são versões simplificadas; a referência para conferir o ato completo é o PDF oficial. Publicação de pedido não equivale a concessão do registro nem confirma lançamento de produto.

Fonte: https://revistas.inpi.gov.br/rpi/

## Comércio exterior

- Escolha **Exportação brasileira** ou **Importação brasileira**.
- Informe a NCM com oito dígitos, sem pontuação. A tela oferece um link para consultar a classificação oficial.
- Escolha um período de até 24 meses e, opcionalmente, a UF.
- Consulte o ranking de países, valor total em **US$ FOB**, peso líquido em kg e evolução mensal. Os valores do gráfico também estão disponíveis em tabela.
- O período inicial sugerido cobre os últimos 12 meses disponíveis na fonte. A tela informa o mês mais recente e a data de atualização da base.

Os dados são estatísticas agregadas do Comex Stat. Não identificam empresas compradoras ou fornecedoras. O critério de UF é o da base oficial de dados gerais.

Fontes: https://comexstat.mdic.gov.br/pt/home e https://api-comexstat.mdic.gov.br/docs

## Salvar e exportar

Use **Salvar oportunidade** e consulte a aba **Salvas**. Cada usuário possui sua própria seleção, com até 300 registros, e pode escrever anotações. A remoção de um cartão salvo não modifica a fonte de origem.

As exportações CSV contêm os resultados exibidos, com origem, URL e data da consulta. Valores de comércio exterior são em USD; os valores de editais e contratos são em BRL, conforme publicados.

## Funcionamento e instalação

- As consultas são feitas sob demanda. Não há agendamento de buscas nem envio automático de mensagens.
- Não é necessário cadastrar chave de API para as fontes deste módulo.
- As fontes públicas podem limitar consultas ou ficar indisponíveis. Nesses casos, o sistema mostra o erro sem apresentar a falha como ausência de registros.
- Resultados públicos são mantidos em cache para reduzir consultas repetidas: PNCP por 10 minutos; obras e notícias por 30 minutos; consultas de comércio exterior por uma hora. Catálogos de edições e períodos disponíveis são reutilizados por seis horas.
- O cache, os índices de edições do INPI e as oportunidades salvas ficam em `private_data/radar`, respeitando a pasta privada configurada na instalação.
- Foram acrescentados `prospectax_radar.py` e `site/radar.html`, `site/radar.js`, `site/radar.css`. O servidor principal recebeu as rotas e a página inicial recebeu o acesso ao Radar. Não foram acrescentadas dependências Python.
- Depois da instalação, reinicie o servidor pelo iniciador habitual e atualize o navegador com **Ctrl+F5**.
