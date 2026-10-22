# Instalar e usar o ProspectaX no Windows

Este guia é para uma instalação nova. Preserve uma cópia privada dos dados antes de atualizar uma instalação que já usa. Os comandos abaixo devem ser executados na pasta que contém `README.md`, `requirements.txt` e `prospector_api_parquet.py`.

## 1. Baixar e extrair o programa

Na página principal do repositório, clique em **Code > Download ZIP**. Extraia o ZIP em uma pasta do computador. Trabalhe na pasta extraída, sem executar arquivos de dentro do ZIP.

O download inclui código, telas, documentação e utilitários da Receita. A base nacional, usuários existentes, chaves particulares e o executável do túnel são preparados separadamente.

## 2. Instalar Python

Baixe Python 3.12 pelo [site oficial para Windows](https://www.python.org/downloads/windows/). No instalador, habilite a opção de adicionar Python ao PATH.

Abra a pasta do projeto no Explorador de Arquivos. Clique na barra de endereço, digite `powershell` e pressione Enter. Confira:

```powershell
python --version
```

O resultado deve indicar Python 3.12. Se aparecer uma mensagem de que Python não foi encontrado ou abrir a Microsoft Store, confira a instalação e reabra o terminal antes de continuar.

## 3. Instalar as dependências

Execute, uma linha de cada vez:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Esses comandos criam uma pasta local de dependências. Não é necessário ativar scripts do PowerShell: os próximos comandos chamam diretamente esse Python.

## 4. Preparar a base da Receita

Para pesquisar empresas reais, escolha um dos caminhos abaixo. Apenas abrir o programa não baixa essa base automaticamente.

**Se já possui uma base compatível:** disponibilize-a na pasta `dados_parquet` ou informe sua localização. O programa precisa dos arquivos no formato esperado; uma planilha qualquer de empresas não substitui o conjunto da Receita.

Para apontar para uma pasta externa, use, no mesmo terminal, substituindo o caminho de exemplo pelo seu:

```powershell
$env:PROSPECTAX_DATA_DIR = 'D:\BASE_PUBLICA\dados_parquet'
```

Essa configuração vale para o terminal atual. A pasta informada precisa existir e conter a base compatível e, para a busca rápida, o índice DuckDB.

**Se precisa baixar e preparar a base:** instale as dependências adicionais:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements_atualizador.txt
```

Confira a disponibilidade dos arquivos na fonte oficial, sem baixar a base inteira:

```powershell
.\.venv\Scripts\python.exe _ATUALIZADOR_RECEITA\atualizar_base_receita.py --dry-run
```

Feche o ProspectaX, confira o espaço livre e execute a preparação completa:

```powershell
.\.venv\Scripts\python.exe _ATUALIZADOR_RECEITA\atualizar_base_receita.py --replace
```

O atualizador baixa os arquivos públicos, converte para Parquet, gera o índice e valida o resultado. A substituição final preserva a base anterior em backup e pode pedir confirmação. Não desligue o computador enquanto estiver trabalhando.

O script exige pelo menos 60 GB livres e recomenda 80 GB ou mais em SSD. Arquivos temporários e a competência escolhida podem exigir mais espaço. O processo pode levar muitas horas. Leia também o [guia do atualizador](../_ATUALIZADOR_RECEITA/README_ATUALIZADOR.txt).

## 5. Abrir o programa

Na pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe iniciar_local.py
```

Mantenha o terminal aberto. O programa tenta abrir o navegador; se necessário, abra um navegador e digite o endereço local informado no terminal, normalmente a porta 8765.

Na primeira instalação, entre com usuário **admin** e senha **admin123**. Esses são os valores iniciais públicos da ferramenta. Contas existentes não são substituídas pelo padrão.

Para encerrar o servidor iniciado nesse terminal, pressione Ctrl+C. Para abrir novamente, repita o comando acima. Não é preciso reinstalar as dependências a cada uso.

## 6. Cadastrar suas APIs, quando necessário

Entre como administrador e abra as configurações do recurso. Alguns serviços possuem links para o cadastro no provedor; crie sua própria conta, obtenha a chave, volte à ferramenta e salve no campo correspondente.

NVIDIA, Brave, AliExpress e consultas públicas têm configurações nas respectivas telas. AliExpress pode exigir aplicativo aprovado; CGU exige chave própria; serviços como Apify e Infosimples dependem das condições da conta. Recursos que exigem uma chave dependem dela para funcionar. A busca na base local não exige uma chave de IA.

O download não faz cadastro nem autenticação automática nesses serviços. Verifique limites, permissões e custos diretamente no provedor. Nunca publique suas chaves nem as envie em mensagens de suporte. O programa não lê arquivos `.env` automaticamente.

## 7. Gerar um link externo, se desejar

O uso apenas no próprio computador dispensa o túnel. Para usar o botão de Link Online:

1. Baixe o executável para seu Windows e arquitetura nos [lançamentos oficiais do Cloudflared](https://github.com/cloudflare/cloudflared/releases).
2. Se necessário, renomeie o arquivo para `cloudflared.exe` e coloque na raiz do projeto, ao lado de `prospector_api_parquet.py`.
3. Defina uma senha administrativa própria antes de abrir acesso externo. A senha inicial `admin123` é pública.
4. Com a ferramenta funcionando, use o botão de Link Online.

O botão não instala o Cloudflared automaticamente. O link gerado aponta para o computador que abriu o túnel. Esse computador, a aplicação e a conexão precisam continuar funcionando; um novo túnel pode gerar outro endereço.

Não utilize configurações de conta, certificados ou estado de túnel de outra pessoa. O túnel real não foi executado nesta preparação; valide-o no computador em que será usado. Se o modo de licenciamento tiver sido ativado nessa instalação, as permissões configuradas também serão necessárias.

## 8. Guardar seus dados

Mantenha backups privados dos cadastros e bancos locais. O histórico do GitHub guarda versões do código publicado, e não faz backup automático da sua carteira, chaves ou registros comerciais. Os arquivos privados não devem ser enviados ao repositório.

## Se algo não funcionar

| Situação | O que conferir |
| --- | --- |
| Python ou módulo não encontrado | Volte às etapas 2 e 3 e use o Python de `.venv` indicado nos comandos. |
| Programa abre, mas não pesquisa empresas | Confira a pasta da base, a conclusão da conversão e a existência do índice. |
| Recurso externo informa que não está configurado | Cadastre suas próprias credenciais nas configurações desse recurso. |
| Cloudflared não encontrado | Confira o nome `cloudflared.exe` e sua localização na raiz do projeto. |
| O endereço local já está em uso | Confira se outra cópia da ferramenta já está aberta antes de iniciar um segundo servidor. |

Ao pedir ajuda, informe a mensagem do erro e a etapa em que ocorreu. Não envie senhas, chaves, cadastros, arquivos de licença ou diagnósticos com dados particulares sem revisão.
