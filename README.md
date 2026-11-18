# TimeZero GRIB Worker para Render (Painel de Bordo V280)

Serviço HTTP independente para executar `grib_generator.py` com **ecCodes**. O painel continua na Vercel. **Este projeto não contém o painel Next.js**.

## Publicar no Render

1. Extraia este ZIP e publique **seu conteúdo** na raiz de um novo repositório GitHub (por exemplo, `timezero-grib-worker`). Não publique arquivos `.env` nem senhas.
2. No Render, abra o projeto `painel de bordo`, clique em **Create new service** > **Web Service** e conecte esse repositório.
3. Configure **Language = Docker**, **Branch = main**, **Root Directory = vazio** e **Dockerfile Path = ./Dockerfile**. Não configure manualmente comandos Build/Start para este Dockerfile.
4. Em **Environment**, crie `TIMEZERO_WORKER_SECRET` com uma chave aleatória **de pelo menos 32 caracteres**. A chave deve ser idêntica à cadastrada na Vercel.
5. Configure o **Health Check Path** como `/health`, se o Render pedir.
6. Faça o deploy. Acesse `https://SEU-SERVICO.onrender.com/health`. O esperado é `{"status":"ok","service":"timezero-grib-worker","eccodes":true}`.
7. Na Vercel, no projeto Painel de Bordo V280, defina:
   - `TIMEZERO_WORKER_URL=https://SEU-SERVICO.onrender.com/api/timezero-grib`
   - `TIMEZERO_WORKER_SECRET` com a MESMA senha do Render
   - `SUPABASE_SERVICE_ROLE_KEY` e `NEXT_PUBLIC_SUPABASE_URL` válidos (necessários para a rota Next.js V280).
8. No Supabase, crie um bucket **privado** chamado `timezero-forecasts` e confira as permissões. Faça novo deploy do projeto V280 na Vercel.

**Segurança:** não teste o endpoint de geração colocando a senha na URL ou no navegador. O endpoint exige o cabeçalho `X-Worker-Secret` e deve ser chamado pelo servidor Next.js. `/health` é público, mas não expõe segredos.

## Funcionamento e limites

- `GET /health` informa se o Python consegue importar o ecCodes.
- `GET /api/timezero-grib?lat=-27&lon=-48&radius=30&hours=24&format=grb` exige `X-Worker-Secret` e devolve bytes GRIB1 ou erro JSON. Para `format=grbzip`, devolve ZIP com `grib.grb`.
- Este serviço usa a geração da V280, com grade de 25 pontos e consultas Open-Meteo. Não está homologado para navegação.
- O cache do worker fica na memória e a proteção de concorrência funciona **por instância**. Para múltiplas réplicas e uso comercial, implementar cache e limitador distribuídos, além de conferir os termos de licenciamento meteorológico.
- O serviço não armazena previsões persistentemente; a **rota Next.js da V280** é responsável pelo armazenamento privado no Supabase Storage.
- No plano que suspende serviços por inatividade, o primeiro acesso pode demorar e exceder o timeout de 50 segundos da rota Next.js. Considere um plano sem suspensão.
- A compatibilidade com TimeZero Professional v4/v5 **não foi testada**. Validar manualmente a importação e as camadas de vento/onda/linha temporal antes de usar no mar.
- O Dockerfile contém os pacotes Python para ecCodes. Não há API oficial do TimeZero nem credenciais TimeZero neste worker.

## Testar localmente

```bash
python -m unittest discover -s tests -v
# Se tiver Docker:
docker build -t timezero-grib-worker .
docker run --rm -p 10000:10000 -e TIMEZERO_WORKER_SECRET=COLOQUE_AQUI_SUA_CHAVE_FORTE timezero-grib-worker
```

Não compartilhe `TIMEZERO_WORKER_SECRET`. Gere uma chave com PowerShell usando `RandomNumberGenerator` como instruído anteriormente.
