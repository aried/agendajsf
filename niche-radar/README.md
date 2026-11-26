# Radar de nichos

Coletor gratuito de sinais de demanda por conteúdo. Lê várias fontes públicas,
cruza o que encontra e, com uma chave da YouTube Data API, mede quem está
recebendo as visualizações de um assunto: canais grandes ou canais pequenos e novos.

Só usa a biblioteca padrão do Python 3.11 ou mais novo. Não há nada para instalar.

## O que ele responde

1. **O que está subindo fora do YouTube?** Buscas em alta, leitura na Wikipedia,
   notícias, comunidades de tecnologia e redes abertas.
2. **O que as pessoas digitam no YouTube sobre um tema?** O autocomplete da busca,
   expandido de A a Z, vira uma lista de subnichos.
3. **Há espaço para um canal novo?** Entre os vídeos mais vistos dos últimos 30 dias
   sobre um termo, quanto das views foi para canais pequenos e quantos vídeos de
   canais pequenos passaram muito do próprio tamanho.

## Fontes

| Fonte | O que entrega | Precisa de conta | Limite conhecido |
|---|---|---|---|
| Google Trends, RSS "em alta agora" | 10 buscas em pico por país | Não | Quase sempre notícia, esporte e celebridade |
| Google News, RSS de busca | Notícias dos últimos 30 dias sobre um termo | Não | No máximo cerca de 100 itens |
| Autocomplete do YouTube | O que a busca sugere, por país e idioma | Não | Sem volume; endpoint não documentado |
| Wikimedia Pageviews | Artigos mais lidos e série diária por artigo | Não | Pede contato no User-Agent para limite maior; o contato só vai para ela |
| Hacker News (Algolia) | Página inicial e contagem de histórias por termo | Não | Só tecnologia |
| GitHub Search | Repositórios novos mais estrelados | Não | Só tecnologia; 10 buscas por minuto |
| Mastodon | Hashtags em alta no mastodon.social | Não | Público pequeno |
| Bluesky | Assuntos em alta | Não | Sem contagem; viés de inglês |
| YouTube Data API v3 | Vídeos, canais, inscritos, views | Chave gratuita | 10.000 unidades por dia; uma busca custa 100 |
| Anotações manuais | O que você viu no TikTok, Instagram, X ou Reddit | – | Você preenche um CSV |

TikTok, Instagram, X e Reddit ficaram de fora da coleta automática porque não
oferecem API gratuita que um script possa usar. O GDELT foi testado e recusou as
requisições por limite de taxa, então não entrou.

## Uso

```bash
cd niche-radar
cp radar.example.toml radar.toml      # ajuste mercados e sementes
export YOUTUBE_API_KEY="sua-chave"    # opcional; sem ela o YouTube é pulado

python -m radar run                   # coleta, valida, mede e gera data/report.html
python -m radar run --skip-youtube    # não gasta cota
python -m radar check "termo" --market br --youtube
python -m radar expand "termo" --market br
python -m radar status                # cota usada hoje e últimas execuções
python -m radar report                # regera o HTML da última execução
```

Os mercados padrão são `br` (português), `us` (inglês) e `mx` (espanhol).

### Chave da YouTube Data API

1. Entre em <https://console.cloud.google.com/> e crie um projeto.
2. Em "APIs e serviços", ative a **YouTube Data API v3**.
3. Em "Credenciais", crie uma **chave de API** e restrinja-a à YouTube Data API v3.
4. Guarde a chave na variável de ambiente `YOUTUBE_API_KEY`. Não a coloque em arquivo versionado.

A chave vai no cabeçalho `X-goog-api-key`, nunca na URL, e não é gravada no banco.

### Sinais manuais

Crie `data/manual_signals.csv`:

```csv
date,source,market,term,note
2026-11-26,tiktok_search_insights,br,termo que apareceu,content gap
```

Cada linha com mercado válido vira candidato e é validada como os demais.

## Como ler o relatório

- **Views em canais pequenos**: parcela das visualizações que foi para canais abaixo
  de `small_subs`. Alta quer dizer que o assunto não está fechado entre os grandes.
- **Estouros**: vídeos de canais pequenos com pelo menos `breakout_views` e
  `breakout_ratio` vezes o número de inscritos.
- **Views nos 3 maiores**: concentração. Perto de 100% indica território dominado.
- **Impulso externo** (0 a 1): crescimento fora do YouTube. Mês contra mês pesa mais
  que a última semana, para um pico de notícia não passar por tendência.
- **Formas**: "alta sustentada", "subindo", "pico recente", "estável", "em queda".
- **Índice** (0 a 100): soma ponderada de demanda, abertura, estouros e impulso.

Os pesos e todos os limiares são heurísticas. Nenhum foi validado contra resultados
reais de canais; leia os componentes antes de confiar no índice.

## Limites

- O YouTube não publica volume de busca. O radar mede o que já aconteceu.
- A busca da API devolve os mais vistos, até 50 por página. É amostra, não censo.
- Com 10.000 unidades por dia cabem cerca de 85 termos medidos por dia.
- A API só informa totais atuais. Velocidade de crescimento exige rodar todo dia.
- Os dados vindos da API do YouTube são apagados após 30 dias, como pedem as
  políticas da API. Use um único projeto do Google Cloud.

## Onde ficam os dados

Tudo vai para `data/` (banco SQLite e relatório), que está no `.gitignore`, junto
com `radar.toml`. Nada do que você coleta é versionado.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes usam respostas sintéticas no formato de cada fonte e não acessam a rede.
