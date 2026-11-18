# NOAA GFS -> GRIB1 TimeZero | Render Worker V283

**Esta versão substitui Open-Meteo como fonte do gerador.** O modelo oficial NOAA GFS é entregue em GRIB2, recortado pelo NOMADS e codificado novamente em GRIB1 com ecCodes. Não altera medições nem preenche valores ausentes. Os dados representam previsões de modelo, não observações.

## Instalar no Render (no serviço existente)

1. Substitua os arquivos do repositório GitHub do serviço `timezero-grib-worker` pelo conteúdo deste ZIP, preservando a raiz, inclusive `Dockerfile`, `server.py`, `noaa_generator.py`, `grib_generator.py`, `requirements.txt` e `render.yaml`.
2. Faça commit na branch ligada ao Render. Render -> `timezero-grib-worker` -> `Deploys`: aguarde `Live`.
3. Mantenha no Render a variável secreta `TIMEZERO_WORKER_SECRET` com o **mesmo valor na Vercel**. Não compartilhe o valor.
4. Opcional: `NOAA_ENABLE_WAVES=false` (padrão). Só habilite `true` após verificar o mapeamento WW3, cobertura e teste TimeZero: downloads de ondas dobram as chamadas NOMADS e podem não estar disponíveis em todos os instantes.
5. Teste `/health`. Exemplo: `https://timezero-grib-worker.onrender.com/health` deve apresentar `source: NOAA GFS 0.25`, `eccodes: true`.

**Importante:** endpoint agora é `/api/timezero-jobs` e não `/api/timezero-grib`. A V283 transforma automaticamente a antiga variável `TIMEZERO_WORKER_URL` que termina em `/api/timezero-grib`, mas é melhor alterá-la para `https://timezero-grib-worker.onrender.com/api/timezero-jobs`.

## Como funciona

Vercel inicia um trabalho autenticado (`GET /api/timezero-jobs?owner=...&lat=...&lon=...&radius=30&hours=24`), monitora `GET ...?owner=...&id=...`, baixa com `download=1&format=grb|grbzip` e grava no bucket privado do Supabase. O navegador nunca recebe a chave do worker. Processamento sequencial com fila, uma tarefa por vez; downloads NOMADS espaçados em pelo menos 10 segundos, como recomendado pela NOAA. Só as variáveis U/V (10 m, m/s) são garantidas pela seleção de campos; ondas não são inventadas. Timestamps em UTC e grade espacial real do modelo NOAA 0.25°.

O processamento pode levar **vários minutos**, especialmente para 120–168 h, por respeitar o intervalo de requisições NOAA. Um serviço Render gratuito pode suspender ou reiniciar tarefas em memória. O resultado concluído é salvo no Supabase pelo aplicativo, mas **tarefas ainda em andamento são voláteis** (para confiabilidade operacional use Render pago + fila persistente). Arquivos gerados num ciclo de modelo são guardados pelo cache diário da Vercel; isso não substitui atualização de ciclo monitorada.

## Limitações que não devem ser escondidas

- Nenhum arquivo novo foi realmente baixado do NOMADS ou importado no TimeZero neste ambiente de desenvolvimento. Execute um teste end-to-end em produção.
- Não foi comprovada a conversão ecCodes GRIB2->GRIB1 com dados NOAA reais nesta execução; validar com `grib_ls`, `grib_dump` e importação TimeZero v4/v5.
- Sem ondas por padrão, pois a correspondência dos parâmetros WaveWatch III e a cobertura exigem teste real. A NOAA disponibiliza ondas em GRIB2, mas não é lícito rotular um parâmetro não verificado.
- A API pública NOAA também aplica limites e recomenda >=10 s entre requisições automatizadas. Não há garantia de disponibilidade/tempo de execução.
- Grade 0.25° é espacialmente grosseira, em particular para áreas de 30 MN. Recorte seleciona pontos nativos reais e pode incluir margem além do raio pedido.
- Todo download válido deve ser conferido contra a previsão original NOAA e exibido no TimeZero antes de emprego operacional. Nunca use como única fonte de navegação/segurança no mar.

## Diagnóstico

`/health` confirma somente servidor/ecCodes. Erros de trabalho ficam visíveis no painel, e o Render registra `NOAA job failed: ...` sem coordenadas. 429 impõe cooldown com `Retry-After`; não fique clicando repetidas vezes.
