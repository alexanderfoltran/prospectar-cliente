# ProspectaX — Fornecedores e Patrocínios

Atualização aprovada e ativa em C:\Users\USUARIO\Desktop\prospector_local.

## Uso no sistema original

Abra http://127.0.0.1:8765 ou continue usando o mesmo link do túnel. Atualize a página com Ctrl+F5 e faça login novamente, usando o usuário e senha habituais. No topo, clique em **Fornecedores parceiros**. O administrador geral encontra a gestão também no painel de Administração.

Foram incluídos vitrine de parceiros aprovados, filtros, favoritos individuais, sugestões por ramo e objetivo, benefícios, gestão de aprovação e anotações internas, convites copiáveis, patrocínios por categoria/região e métricas agregadas com exportação CSV.

Somente o servidor Python foi reiniciado. A troca levou aproximadamente 2.72 segundos. O processo do Cloudflare foi preservado e a mesma URL respondeu com a atualização. Quem estava conectado pode precisar entrar novamente.

Os usuários, senhas e registros comerciais atuais foram preservados. Os Parquets e o índice da Receita continuam na pasta original; nenhum arquivo grande foi copiado.

## Cópia para próximas ideias

A pasta C:\Users\USUARIO\Desktop\prospectax_atualizacao_patrocinio foi mantida. Ela continua na porta 8766 e usa a base da Receita da pasta original em modo de leitura. Cadastros e registros dessa cópia são separados dos registros de produção; não há sincronização automática entre as duas versões.

Use a cópia para avaliar mudanças futuras antes de aprová-las para o sistema original. Não é necessário transferir a base da Receita para ela enquanto esse modo de teste for útil.

## Backup da implantação

Backup dos arquivos substituídos: `C:\Users\USUARIO\Desktop\prospector_local\backup\antes_patrocinio_20261009-140648\programa_anterior.zip`.

O backup contém o programa anterior. A base da Receita e os registros atuais não foram substituídos. Uma restauração deve ser feita de forma controlada, preservando o túnel, como nesta implantação.

## Verificações

A versão de produção passou nos testes de cadastro, aprovação, permissões, filtros, favoritos, métricas e reconexão ao túnel. Após a implantação, foram conferidos os arquivos instalados, o mesmo processo do túnel, o acesso pela mesma URL e a preservação dos usuários e registros comerciais.
