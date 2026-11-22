# Conexões de lojas e mídia no DropAI

## Shopify

Crie um app no Shopify Dev Dashboard com OAuth. Cadastre como callback:

`https://bot-produtos.onrender.com/oauth/shopify/callback`

Solicite somente os escopos necessários para cadastro de produto e estoque: `write_products`, `write_inventory` e `read_locations`. Configure no Render, em Environment, as variáveis `SHOPIFY_CLIENT_ID` e `SHOPIFY_CLIENT_SECRET` com os dados desse app.

Na aba **Loja** do DropAI, selecione Shopify e informe o domínio `sua-loja.myshopify.com`. O DropAI redireciona para a autorização oficial da Shopify. A loja será conectada após o lojista aceitar os escopos.

## Nuvemshop

Registre o DropAI como aplicativo no portal de parceiros da Nuvemshop e defina o callback:

`https://bot-produtos.onrender.com/oauth/nuvemshop/callback`

O aplicativo precisa do escopo `write_products`. Configure `NUVEMSHOP_APP_ID` e `NUVEMSHOP_APP_SECRET` no Render. Depois, use **Conectar Nuvemshop** na aba Loja e autorize o app na loja desejada.

## Pesquisa de fotos e vídeos

- `YOUTUBE_API_KEY`: chave de um projeto Google Cloud com YouTube Data API v3 habilitada. A busca mostra resultados para seleção; o DropAI não baixa o vídeo.
- `SERPAPI_API_KEY`: opcional, para buscar candidatos de imagens do Google. O lojista escolhe cada imagem e precisa confirmar que pode usá-la. O DropAI não faz a busca se essa variável estiver vazia.

As chaves são segredos de ambiente e devem ser adicionadas apenas ao Render. Nunca as publique no GitHub ou no código.

## Cadastro dos produtos

O cadastro por padrão cria rascunhos/itens ocultos. O preço normal é custo × 1,5; nenhum desconto promocional é criado automaticamente. Estoque inicia em zero até o lojista informar o estoque real. Campos como código de barras, NCM e garantia só devem ser preenchidos quando confirmados.
