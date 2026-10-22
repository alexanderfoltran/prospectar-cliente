# ProspectaX - pesquisa com fontes e propostas

Esta integração foi aplicada em `<PASTA_DO_PROJETO>` após autorização. A pasta de atualização continua disponível para testes.

Abra `http://127.0.0.1:8765/inteligencia.html`, entre com a conta do sistema e use Ctrl + F5 se necessário. Há atalhos na Prospecção, nos detalhes da empresa, no detalhe do parceiro e na área Ferramentas de negócio.

## O que a NVIDIA faz nesta versão

- Analisa trechos das páginas consultadas e prepara um resumo com referências.
- Sugere oportunidades comerciais, identificadas como hipóteses para avaliar.
- Compara a atividade/oferta do cliente com os parceiros aprovados da vitrine. Não cadastra fornecedores inventados.
- Ajuda a redigir a abertura e o encerramento da proposta. Preços e condições vêm do que você informar.
- Cada ação permite escolher Automático ou um modelo habilitado. O administrador define qual modelo é preferido para análise, fornecedores e propostas. A escolha automática usa essas regras; não é uma promessa de encontrar sempre o melhor modelo. Se um modelo automático estiver indisponível por erro do serviço, há uma tentativa com outro habilitado e a troca é mostrada. Seleção manual não troca de modelo silenciosamente.

## Cadastrar a chave depois

1. Entre com administrador geral e abra Configurar NVIDIA.
2. Cole sua própria chave NVIDIA usada para acessar `integrate.api.nvidia.com`.
3. Confira os modelos habilitados. Você pode acrescentar o identificador de um modelo disponível no catálogo NVIDIA e selecioná-lo para uma tarefa ou manualmente na hora de executar.
4. Ative as chamadas somente em uma modalidade que permita seu uso e salve. A chave fica protegida pelo Windows DPAPI, vinculada à conta do Windows. Ela não aparece novamente na tela nem é devolvida em respostas JSON.
5. Consultar catálogo NVIDIA tenta obter os modelos disponíveis. A disponibilidade real só pode ser verificada depois de cadastrar a chave; sem ela, a lista exibe os modelos cadastrados para configurar.

O limite diário padrão é de 20 chamadas por usuário, ajustável entre 1 e 200 pelo administrador. Falhas de chamada também contam. Um fallback automático consome uma segunda chamada. Esses limites do programa não substituem os da conta NVIDIA.

Os endpoints gratuitos públicos são voltados a desenvolvimento e testes. Confira a modalidade da sua conta antes de usar em produção e não envie dados pessoais ou confidenciais. A análise recebe trechos públicos reduzidos; CNPJ, e-mails, telefones e URLs são removidos do texto enviado ao modelo. A assinatura e as condições da proposta ficam locais. Essa redução não garante que qualquer texto público esteja livre de dados pessoais; revise o material.

## Busca web e confirmação dos contatos

A NVIDIA analisa informações; a pesquisa de páginas é separada. Você pode configurar Brave Search, usar uma configuração Google existente ou colar até quatro sites públicos. A API Google Custom Search está fechada a novos clientes e a documentação informa transição dos clientes existentes até 01/01/2027; por isso há opção de Brave e de URLs informadas.

1. Informe o CNPJ de uma empresa presente no índice local ou escolha um parceiro aprovado.
2. Informe o que você oferece e os sites que já conhece, se houver.
3. Execute Pesquisar contatos e analisar. As fontes consultadas, trechos e datas são salvos na sua conta.
4. O CNPJ mostrado no texto de uma página é uma evidência para identificar aquele site. Os contatos nele podem pertencer a funcionários, agências ou outras marcas: e-mail e rede social continuam pendentes até você conferir.
5. Use Ver fonte e Abrir perfil/site para verificar. Depois, confirme ou rejeite. Apenas contatos confirmados por você aparecem como canais de envio da proposta.

Nome semelhante ou link de resultado de busca não confirma identidade. Quando o site bloqueia a leitura ou não responde, a falha é mostrada; nenhum perfil é presumido. Não existe garantia automática de 100% de acerto. As URLs de contato vêm das páginas/resultados, nunca do texto da IA. Páginas que precisam de login não são acessadas com suas credenciais.

## Preparar texto e PDF, sem envio automático

1. Selecione uma pesquisa salva em Preparar proposta.
2. Informe sua assinatura, a oferta e os preços/condições que você definiu.
3. Prepare o rascunho. Sem a NVIDIA configurada, é utilizado um roteiro local claramente identificado.
4. Edite assunto e texto, revise valores e marque a revisão quando concluída. Salve antes de baixar o PDF; ele corresponde à versão salva. Rascunhos são identificados no rodapé.
5. Copie o texto ou baixe o PDF. Abra um contato confirmado para fazer o envio manual. O botão de e-mail abre seu programa de e-mail com o assunto; cole o texto ou anexe o PDF.

Instagram, Facebook e LinkedIn abrem o perfil para contato manual. O sistema não envia mensagens externas, não executa disparos e não marca a proposta como enviada. O compartilhamento e a confirmação do envio ficam sob seu controle.

## Dados, instalação e backup

- Pesquisas e propostas ficam em `private_data/intelligence.sqlite`, isoladas por conta, com controle de revisão.
- Configurações ficam em `private_data/intelligence_config.json`; os segredos são cifrados pelo Windows. Ao migrar para outra conta do Windows, cadastre as chaves novamente.
- O PDF usa ReportLab 5.0.1, listado em `requirements_inteligencia.txt`. Em outro computador: `python -m pip install -r requirements_inteligencia.txt`.
- Nenhum Parquet ou índice DuckDB foi copiado. O sistema continua lendo sua base original; a pasta de avaliação também usa essa base sem duplicá-la.
- O código foi aplicado à original com backup em `backup/antes_pesquisa_nvidia_<data-hora>/`. O mesmo túnel foi preservado. Os cadastros anteriores e a pasta de atualização foram mantidos; os bancos da cópia não foram importados.
- Dados de exemplo, respostas simuladas e chaves fictícias dos testes não foram instalados nos seus cadastros.

## Referências

- NVIDIA: https://docs.api.nvidia.com/nim/reference/llm-apis
- Oferta e modalidades: https://developer.nvidia.com/nim
- Termos de avaliação: https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf
- Brave Search: https://api-dashboard.search.brave.com/documentation/guides/authentication
- Google Custom Search: https://developers.google.com/custom-search/v1/overview
